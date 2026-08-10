#!/usr/bin/env python3
"""Pilot a lossless, read-only Parquet snapshot of one Tradier event day.

This tool never deletes or mutates source data. It uses the monotonically increasing
event id to bound a completed UTC day, exports every stored column with Zstandard
compression, and compares strong logical fingerprints across SQLite and Parquet.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data" / "tradier_stream.sqlite"
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "parquet_pilots"
TABLE = "tradier_stream_events"
COLUMNS = (
    "id", "run_id", "captured_at", "provider_ts", "event_type", "symbol",
    "bid", "ask", "last", "price", "size", "raw_json", "asset_class",
    "underlying", "option_expiration", "option_type", "strike",
)


def _quote_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _next_existing(connection: sqlite3.Connection, candidate: int) -> tuple[int, str] | None:
    row = connection.execute(
        f"select id, captured_at from {TABLE} where id >= ? order by id limit 1",
        (candidate,),
    ).fetchone()
    return (int(row[0]), str(row[1])) if row else None


def lower_id_bound(connection: sqlite3.Connection, timestamp: str) -> int:
    """Find the first id at or after a timestamp without a 100M-row time scan."""
    maximum = int(connection.execute(f"select coalesce(max(id), 0) from {TABLE}").fetchone()[0])
    low, high = 1, maximum + 1
    while low < high:
        middle = (low + high) // 2
        row = _next_existing(connection, middle)
        if row is None:
            high = middle
        elif row[1] < timestamp:
            low = row[0] + 1
        else:
            high = row[0]
    row = _next_existing(connection, low)
    return row[0] if row else maximum + 1


def _fingerprint(connection: Any, relation: str, where: str = "") -> dict[str, Any]:
    columns = ", ".join(COLUMNS)
    started = time.perf_counter()
    row = connection.execute(
        f"""
        select count(*), min(id), max(id), min(captured_at), max(captured_at),
               bit_xor(hash({columns})), sum(hash({columns})::hugeint)
        from {relation} {where}
        """
    ).fetchone()
    return {
        "row_count": int(row[0]),
        "min_id": int(row[1]) if row[1] is not None else None,
        "max_id": int(row[2]) if row[2] is not None else None,
        "min_captured_at": row[3],
        "max_captured_at": row[4],
        "hash_xor": str(row[5]),
        "hash_sum": str(row[6]),
        "query_seconds": round(time.perf_counter() - started, 3),
    }


def export_day(database: Path, output: Path, day: date) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("duckdb is required; use the research Python environment") from exc

    database = database.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing pilot: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    start = day.isoformat()
    stop = (day + timedelta(days=1)).isoformat()

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as source:
        source.execute("pragma query_only=on")
        first_id = lower_id_bound(source, start)
        stop_id = lower_id_bound(source, stop)
    if stop_id <= first_id:
        raise RuntimeError(f"no events found for {day.isoformat()}")

    connection = duckdb.connect()
    try:
        connection.execute("set memory_limit='8GB'")
        connection.execute("set threads=2")
        connection.execute("LOAD sqlite")
        connection.execute(
            f"ATTACH {_quote_sql(str(database))} AS source (TYPE sqlite, READ_ONLY)"
        )
        where = f"where id >= {first_id} and id < {stop_id}"
        source_fp = _fingerprint(connection, f"source.{TABLE}", where)
        if not (
            str(source_fp["min_captured_at"]) >= start
            and str(source_fp["max_captured_at"]) < stop
        ):
            raise RuntimeError("event ids are not chronological across the requested UTC day")

        started = time.perf_counter()
        connection.execute(
            f"""
            copy (
                select {', '.join(COLUMNS)} from source.{TABLE} {where} order by id
            ) to {_quote_sql(str(output))}
            (format parquet, compression zstd, row_group_size 100000)
            """
        )
        export_seconds = round(time.perf_counter() - started, 3)
        parquet_relation = f"read_parquet({_quote_sql(str(output))})"
        parquet_fp = _fingerprint(connection, parquet_relation)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    comparable = ("row_count", "min_id", "max_id", "min_captured_at", "max_captured_at", "hash_xor", "hash_sum")
    logical_match = all(source_fp[key] == parquet_fp[key] for key in comparable)
    if not logical_match:
        output.unlink(missing_ok=True)
        raise RuntimeError("Parquet round-trip fingerprint does not match SQLite source")

    source_bytes = database.stat().st_size
    parquet_bytes = output.stat().st_size
    # The source database contains many days; per-day SQLite bytes are estimated by
    # row share and explicitly labeled rather than presented as an exact partition size.
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as source:
        maximum_id = int(source.execute(f"select max(id) from {TABLE}").fetchone()[0])
    estimated_source_bytes = round(source_bytes * source_fp["row_count"] / maximum_id)
    return {
        "schema_version": 1,
        "read_only_source": True,
        "source_database": str(database),
        "utc_date": day.isoformat(),
        "id_range": [first_id, stop_id],
        "source_database_bytes": source_bytes,
        "estimated_source_partition_bytes": estimated_source_bytes,
        "parquet_path": str(output),
        "parquet_bytes": parquet_bytes,
        "estimated_compression_ratio": round(estimated_source_bytes / parquet_bytes, 3),
        "export_seconds": export_seconds,
        "source_fingerprint": source_fp,
        "parquet_fingerprint": parquet_fp,
        "round_trip_logical_match": True,
        "caveat": "SQLite partition bytes are estimated by event-id share; all row values are fingerprint-verified.",
        "source_deleted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="completed UTC date")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    output = args.output or DEFAULT_OUTPUT_ROOT / f"tradier_stream_events_{args.date:%Y%m%d}.parquet"
    report = export_day(args.database, output, args.date)
    report_path = args.report or output.with_suffix(".audit.json")
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
