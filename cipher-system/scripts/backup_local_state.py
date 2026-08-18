#!/usr/bin/env python3
"""Create and restore-verify a recoverable backup of small user-owned state DBs."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import shutil
import time
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import operator_status  # noqa: E402


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _wal_trio_copy(source: Path, destination: Path) -> None:
    """Fallback hot copy for WAL-mode DBs whose read-only open is blocked.

    When a writer is mid-commit, `mode=ro` can fail with CANTOPEN because the
    reader cannot create/recover the -shm file. Copying the main file, then the
    -wal, then the -shm (in that order) is the documented safe hot-copy order:
    SQLite replays the copied WAL into the copied main file on open, so the
    destination is consistent as of the copy moment. Integrity is verified by
    the caller on the restored copy.
    """
    for suffix in ("", "-wal", "-shm"):
        src = Path(f"{source}{suffix}")
        if src.is_file():
            shutil.copy2(src, Path(f"{destination}{suffix}"))
    with sqlite3.connect(destination, timeout=5) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("WAL trio copy failed integrity check")


def sqlite_backup(source: Path, destination: Path, *, attempts: int = 3) -> None:
    """Online backup with bounded retries, then a WAL trio copy fallback.

    The read-only URI open is preferred (it never blocks the writer), but it
    can fail with "unable to open database file" on a WAL DB when the writer
    is mid-commit and the reader cannot create the -shm file. Retry with
    backoff; if every attempt fails, fall back to the ordered WAL hot copy.
    """
    last_error: sqlite3.Error | None = None
    for attempt in range(attempts):
        destination.unlink(missing_ok=True)
        try:
            with sqlite3.connect(
                f"file:{source.as_posix()}?mode=ro", uri=True, timeout=10
            ) as src, sqlite3.connect(destination, timeout=10) as dst:
                src.backup(dst)
            return
        except sqlite3.Error as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.0 * (attempt + 1))
    # Writer is still holding the WAL; use the ordered trio hot copy instead.
    try:
        _wal_trio_copy(source, destination)
        return
    except (sqlite3.Error, OSError) as exc:
        raise last_error or exc from exc


def backup(target_root: Path = operator_status.BACKUPS) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_root / f".partial-{stamp}"
    final_target = target_root / stamp
    target.mkdir(parents=True, exist_ok=False)
    stores = []
    try:
        for relative in operator_status.BACKUP_STORES:
            source = operator_status.DATA / relative
            if not source.is_file() or source.stat().st_size > 512 * 1024 * 1024:
                continue
            destination = target / Path(relative).name
            try:
                sqlite_backup(source, destination)
            except sqlite3.Error as exc:
                raise RuntimeError(f"SQLite backup failed for {relative}: {exc}") from exc
            stores.append({"source": relative, "file": destination.name, "bytes": destination.stat().st_size,
                           "sha256": digest(destination)})
    except Exception:
        # A failed partial must not block a retry in the same second or look like a
        # usable backup to an operator inspecting the directory.
        shutil.rmtree(target, ignore_errors=True)
        raise
    verified = True
    with tempfile.TemporaryDirectory(prefix="cipher-restore-check-") as temporary:
        for row in stores:
            restored = Path(temporary) / row["file"]
            restored.write_bytes((target / row["file"]).read_bytes())
            if digest(restored) != row["sha256"]:
                verified = False
                break
            with sqlite3.connect(restored) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    verified = False
                    break
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "restore_verified": verified, "stores": stores,
                "scope": "Small user state only; large market-data archives are intentionally excluded."}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not verified:
        raise RuntimeError("backup restore verification failed")
    target.rename(final_target)
    return {**manifest, "path": str(final_target)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=operator_status.BACKUPS)
    args = parser.parse_args()
    print(json.dumps(backup(args.target), indent=2))
