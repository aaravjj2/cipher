#!/usr/bin/env python3
"""Create verified, append-only daily Parquet mirrors of completed stream days.

The SQLite source is always opened read-only by parquet_offload and is never pruned.
An atomic SQLite ledger makes retries resumable and records both source and Parquet
fingerprints for every completed partition.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from . import parquet_offload
except ImportError:
    import parquet_offload

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = ROOT / "data" / "parquet_archive" / "tradier_stream_events"


def _ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS partitions (
          utc_date TEXT PRIMARY KEY, parquet_path TEXT NOT NULL, audit_path TEXT NOT NULL,
          row_count INTEGER NOT NULL, min_id INTEGER NOT NULL, max_id INTEGER NOT NULL,
          source_hash_xor TEXT NOT NULL, source_hash_sum TEXT NOT NULL,
          parquet_bytes INTEGER NOT NULL, completed_at TEXT NOT NULL,
          source_deleted INTEGER NOT NULL CHECK(source_deleted = 0)
        )
    """)
    return db


def most_recent_completed_day(today: date | None = None) -> date:
    """The newest UTC day `archive_day` will accept.

    Exists so a timer can run this with no arguments: the alternative is computing
    yesterday's date in a shell wrapper, which puts the one rule that matters -- never
    archive a day still being written -- outside the module that enforces it.
    """
    current = today or datetime.now(timezone.utc).date()
    return current - timedelta(days=1)


def archive_day(database: Path, archive_root: Path, day: date, *, today: date | None = None) -> dict:
    current = today or datetime.now(timezone.utc).date()
    if day >= current:
        raise ValueError("only completed UTC days can be archived")
    partition = archive_root / f"date={day.isoformat()}"
    output = partition / "part-000.parquet"
    audit = partition / "audit.json"
    ledger_path = archive_root / "retention.sqlite"
    with _ledger(ledger_path) as db:
        prior = db.execute("SELECT * FROM partitions WHERE utc_date = ?", (day.isoformat(),)).fetchone()
    if prior:
        if not Path(prior["parquet_path"]).is_file() or not Path(prior["audit_path"]).is_file():
            raise RuntimeError("retention ledger references a missing partition artifact")
        return {"status": "already_archived", **dict(prior), "source_deleted": False}
    if output.exists() or audit.exists():
        raise RuntimeError("unledgered partition artifacts exist; inspect them before retrying")

    partition.mkdir(parents=True, exist_ok=True)
    partial = partition / f"part-000.parquet.partial-{os.getpid()}"
    try:
        report = parquet_offload.export_day(database, partial, day)
        report["retention_mode"] = "append_only_mirror"
        report["source_deleted"] = False
        os.replace(partial, output)
        report["parquet_path"] = str(output.resolve())
        audit_partial = partition / f"audit.json.partial-{os.getpid()}"
        audit_partial.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        os.replace(audit_partial, audit)
        fp = report["source_fingerprint"]
        completed = datetime.now(timezone.utc).isoformat()
        with _ledger(ledger_path) as db:
            db.execute(
                "INSERT INTO partitions VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (day.isoformat(), str(output.resolve()), str(audit.resolve()), fp["row_count"],
                 fp["min_id"], fp["max_id"], fp["hash_xor"], fp["hash_sum"],
                 report["parquet_bytes"], completed),
            )
        return {"status": "archived", **report, "audit_path": str(audit.resolve()), "completed_at": completed}
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def status(archive_root: Path) -> dict:
    ledger_path = archive_root / "retention.sqlite"
    if not ledger_path.exists():
        return {"partitions": [], "count": 0, "source_pruning_enabled": False}
    with _ledger(ledger_path) as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM partitions ORDER BY utc_date")]
    return {"partitions": rows, "count": len(rows), "source_pruning_enabled": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=parquet_offload.DEFAULT_DATABASE)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="UTC day to archive; defaults to the most recent completed day.",
    )
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    result = status(args.archive_root) if args.status else archive_day(
        args.database, args.archive_root, args.date or most_recent_completed_day()
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
