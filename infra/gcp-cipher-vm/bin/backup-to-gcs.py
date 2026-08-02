#!/usr/bin/env python3
"""Create SQLite-safe daily backup of Cipher operational state to GCS.

SQLite databases are backed up via the ``.backup`` command so that
concurrent writes do not corrupt the archive.  Other operational data
(scans, snapshots, reports) is included as a plain tar.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import storage

ROOT = Path("/home/aarav/Aarav/cipher")
DATA = ROOT / "cipher-system" / "data"
BUCKET = os.environ.get("CIPHER_BACKUP_BUCKET")

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
    "reports",
]


def sqlite_safe_copy(src: Path, dst: Path) -> bool:
    """Use the SQLite backup API to copy a live database safely."""
    if not src.exists():
        return False
    try:
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()
        return True
    except Exception as exc:
        print(f"WARN: sqlite backup failed for {src.name}: {exc}")
        # Fall back to raw copy if backup API fails
        try:
            shutil.copy2(src, dst)
            return True
        except Exception:
            return False


def main() -> int:
    if not BUCKET:
        raise SystemExit("CIPHER_BACKUP_BUCKET is required")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"backups/{datetime.now(timezone.utc):%Y/%m/%d}"

    with tempfile.TemporaryDirectory(prefix="cipher-backup-") as tmpdir:
        tmp = Path(tmpdir)

        # ── SQLite-safe copies ───────────────────────────────────────
        sqlite_dir = tmp / "sqlite"
        sqlite_dir.mkdir()
        for db in SQLITE_DBS:
            if db.exists():
                safe = sqlite_dir / db.name
                if sqlite_safe_copy(db, safe):
                    print(f"  sqlite: {db.name} ({safe.stat().st_size} bytes)")

        # ── Operational tar (non-SQLite data) ────────────────────────
        archive = tmp / f"cipher-operational-{stamp}.tar.zst"
        tar_paths = []
        for rel in TAR_INCLUDES:
            p = ROOT / rel
            if p.exists():
                tar_paths.append(rel)

        if tar_paths:
            cmd = [
                "tar", "--zstd", "--ignore-failed-read",
                "-cf", str(archive), "-C", str(ROOT), *tar_paths,
            ]
            subprocess.run(cmd, check=False)

        # ── Upload to GCS ────────────────────────────────────────────
        client = storage.Client()
        bucket = client.bucket(BUCKET)

        # Upload SQLite backups
        for safe_db in sqlite_dir.iterdir():
            obj = f"{prefix}/sqlite/{safe_db.name}"
            blob = bucket.blob(obj)
            blob.upload_from_filename(str(safe_db), content_type="application/x-sqlite3")
            print(f"  gs://{BUCKET}/{obj}")

        # Upload operational archive
        if archive.exists() and archive.stat().st_size > 0:
            obj = f"{prefix}/{archive.name}"
            blob = bucket.blob(obj)
            blob.upload_from_filename(str(archive), content_type="application/zstd")
            print(f"  gs://{BUCKET}/{obj}")

    print("Backup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
