#!/usr/bin/env python3
"""Create SQLite-safe daily backup of Cipher operational state to GCS.

SQLite databases are backed up via the ``.backup`` command so that
concurrent writes do not corrupt the archive.  Other operational data
(scans, snapshots, reports) is included as a plain tar.
"""
from __future__ import annotations

import os
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/aarav/Aarav/cipher")
DATA = ROOT / "cipher-system" / "data"
BUCKET = os.environ.get("CIPHER_BACKUP_BUCKET")
MIN_FREE_RESERVE_BYTES = int(
    os.environ.get("CIPHER_BACKUP_MIN_FREE_BYTES", str(5 * 1024**3))
)

# SQLite databases that need .backup (not raw copy)
SQLITE_DBS = [
    DATA / "gex_history.sqlite",
    DATA / "tradier_stream.sqlite",
    DATA / "historical_bars.sqlite",
    DATA / "governance" / "research_registry.sqlite",
    DATA / "live_option_chains_archive.sqlite",
]

# Directories/files to include as plain tar
TAR_INCLUDES = [
    "cipher-system/data/gex_snapshots",
    "cipher-system/data/tradier_stream_events",
    "cipher-system/data/accessobsidian_scans",
    "cipher-system/data/backtest_results",
    "cipher-system/data/flash_agentic",
    "cipher-system/data/forward_tests",
    "cipher-system/data/governance/artifacts",
    "cipher-system/data/governance/latest_system_inventory.json",
    "cipher-system/data/research_snapshots",
    "cipher-system/data/warehouse_exports",
    # 772 KB of download-run configs that make the deliberately-excluded 9.8 GB of
    # data/historical_options rebuildable. Excluding the bars is only economical if the
    # recipe survives, and the recipe is not in download_manifest.json -- that carries
    # latest_run_config, one run of the 205 that built a single dataset. The full set lives
    # in download_runs.config_json inside each dataset database, i.e. inside the directory
    # this backup skips. Refreshed by refresh_options_rebuild_recipes() below.
    "cipher-system/data/options_rebuild_recipes",
    "reports",
]

RECIPE_EXPORTER = ROOT / "cipher-system" / "scripts" / "export_options_rebuild_recipes.py"


def sqlite_safe_copy(src: Path, dst: Path) -> bool:
    """Use the SQLite backup API to copy a live database safely."""
    if not src.exists():
        return False
    src_conn = None
    dst_conn = None
    try:
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        src_conn.backup(dst_conn)
        return True
    except Exception as exc:
        print(f"WARN: sqlite backup failed for {src.name}: {exc}")
        # A raw copy of a live WAL database is not a backup: it can omit committed
        # pages that still live in the WAL. Fail closed and leave the source alone.
        dst.unlink(missing_ok=True)
        return False
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def upload_verified(bucket, local_path: Path, object_name: str, *, metadata: dict[str, str]) -> None:
    """Upload one object and verify its size and application checksum remotely."""
    digest = sha256_file(local_path)
    expected_size = local_path.stat().st_size
    blob = bucket.blob(object_name)
    blob.metadata = {**metadata, "cipher-sha256": digest}
    blob.upload_from_filename(str(local_path))
    blob.reload()
    if int(blob.size or -1) != expected_size:
        raise RuntimeError(
            f"backup size verification failed for {object_name}: "
            f"expected {expected_size}, got {blob.size}"
        )
    if (blob.metadata or {}).get("cipher-sha256") != digest:
        raise RuntimeError(f"backup checksum metadata verification failed for {object_name}")


def require_backup_headroom(paths: list[Path], directory: Path) -> None:
    """Require room for the largest one-at-a-time SQLite snapshot plus reserve."""
    largest = max((path.stat().st_size for path in paths if path.exists()), default=0)
    free = shutil.disk_usage(directory).free
    required = largest + MIN_FREE_RESERVE_BYTES
    if free < required:
        raise RuntimeError(
            f"insufficient backup headroom: free={free}, required={required}, "
            f"largest_database={largest}, reserve={MIN_FREE_RESERVE_BYTES}"
        )


def refresh_options_rebuild_recipes() -> str | None:
    """Regenerate the rebuild recipes so the tar carries current ones.

    Deliberately non-fatal. A stale or missing recipe weakens a documented recovery path for
    reproducible data; a failed backup loses irreplaceable data. Those are not the same
    severity, so this warns and continues rather than aborting the run that protects the
    quote corpus. Returns the warning text, or None on success.
    """
    if not RECIPE_EXPORTER.is_file():
        return f"recipe exporter missing at {RECIPE_EXPORTER}"
    try:
        subprocess.run(
            [sys.executable, str(RECIPE_EXPORTER)],
            check=True, capture_output=True, text=True, timeout=300,
        )
    except Exception as exc:  # noqa: BLE001 - never fail the backup for this
        detail = getattr(exc, "stderr", None) or str(exc)
        return f"rebuild-recipe export failed: {str(detail).strip()[:300]}"
    return None


def create_operational_archive(archive: Path, root: Path, includes: list[str]) -> bool:
    """Create a complete operational archive, or raise instead of accepting partial data."""
    tar_paths = [rel for rel in includes if (root / rel).exists()]
    if not tar_paths:
        return False
    cmd = ["tar", "--zstd", "-cf", str(archive), "-C", str(root), *tar_paths]
    # A partial archive is worse than a failed backup because it looks
    # restorable. Surface disappearing/unreadable inputs immediately.
    subprocess.run(cmd, check=True)
    if not archive.exists() or archive.stat().st_size <= 0:
        raise RuntimeError("operational archive command succeeded without producing data")
    return True


def main() -> int:
    from google.cloud import storage

    if not BUCKET:
        raise SystemExit("CIPHER_BACKUP_BUCKET is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"backups/{datetime.now(timezone.utc):%Y/%m/%d}"

    with tempfile.TemporaryDirectory(prefix="cipher-backup-") as tmpdir:
        tmp = Path(tmpdir)
        required_dbs = [db for db in SQLITE_DBS if db.exists()]
        missing_dbs = [str(db) for db in SQLITE_DBS if not db.exists()]
        if missing_dbs:
            raise RuntimeError(f"required SQLite databases are missing: {missing_dbs}")
        require_backup_headroom(required_dbs, tmp)

        client = storage.Client()
        bucket = client.bucket(BUCKET)

        # ── SQLite-safe copies ───────────────────────────────────────
        sqlite_dir = tmp / "sqlite"
        sqlite_dir.mkdir()
        sqlite_manifest = []
        for db in required_dbs:
            safe = sqlite_dir / db.name
            started_at = datetime.now(timezone.utc).isoformat()
            if not sqlite_safe_copy(db, safe):
                raise RuntimeError(f"SQLite-safe backup failed for {db}")
            finished_at = datetime.now(timezone.utc).isoformat()
            source_stat = db.stat()
            entry = {
                "name": db.name,
                "source_path": str(db),
                "source_size_bytes": source_stat.st_size,
                "source_mtime_ns": source_stat.st_mtime_ns,
                "backup_size_bytes": safe.stat().st_size,
                "backup_started_at": started_at,
                "backup_finished_at": finished_at,
            }
            obj = f"{prefix}/sqlite/{db.name}"
            upload_verified(
                bucket,
                safe,
                obj,
                metadata={
                    "source-size-bytes": str(source_stat.st_size),
                    "source-mtime-ns": str(source_stat.st_mtime_ns),
                    "backup-finished-at": finished_at,
                },
            )
            print(f"  gs://{BUCKET}/{obj} ({safe.stat().st_size} bytes, verified)")
            sqlite_manifest.append(entry)
            # Peak local use is one database, not the sum of every database.
            safe.unlink()

        manifest_path = tmp / "sqlite-backup-manifest.json"
        manifest_path.write_text(
            json.dumps({"created_at": stamp, "databases": sqlite_manifest}, indent=2),
            encoding="utf-8",
        )
        upload_verified(
            bucket,
            manifest_path,
            f"{prefix}/sqlite/backup-manifest.json",
            metadata={"kind": "cipher-sqlite-backup-manifest"},
        )

        # ── Operational tar (non-SQLite data) ────────────────────────
        # Refreshed before the tar is built, so the archive carries recipes matching the
        # datasets as they are now rather than as they were at the last manual export.
        recipe_warning = refresh_options_rebuild_recipes()
        if recipe_warning:
            print(f"WARN: {recipe_warning}")
        archive = tmp / f"cipher-operational-{stamp}.tar.zst"
        create_operational_archive(archive, ROOT, TAR_INCLUDES)

        # Upload operational archive
        if archive.exists() and archive.stat().st_size > 0:
            obj = f"{prefix}/{archive.name}"
            upload_verified(
                bucket,
                archive,
                obj,
                metadata={"kind": "cipher-operational-tar-zstd"},
            )
            print(f"  gs://{BUCKET}/{obj} (verified)")

    print("Backup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
