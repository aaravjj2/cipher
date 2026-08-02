#!/usr/bin/env python3
"""Read-only health audit for the Tradier underlying/option stream database."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "tradier_stream.sqlite"


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--max-age-minutes", type=float, default=30.0)
    parser.add_argument("--ignore-age", action="store_true")
    args = parser.parse_args()

    if not args.db.is_file():
        print(json.dumps({"status": "FAIL", "error": f"database not found: {args.db}"}, indent=2))
        return 1

    with sqlite3.connect(f"file:{args.db}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        integrity = str(db.execute("pragma integrity_check").fetchone()[0])
        latest = db.execute(
            """
            select id, started_at, completed_at, event_count, error, stop_reason,
                   resolved_symbol_count, option_contract_count, last_event_at,
                   requested_underlyings
            from tradier_stream_runs
            order by id desc limit 1
            """
        ).fetchone()
        if latest is None:
            print(json.dumps({"status": "FAIL", "integrity": integrity, "error": "no stream runs"}, indent=2))
            return 1

        run_id = int(latest["id"])
        stored_events = int(
            db.execute(
                "select count(*) from tradier_stream_events where run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )
        option_events, option_symbols, latest_option_at = db.execute(
            """
            select count(*), count(distinct symbol), max(captured_at)
            from tradier_stream_events
            where run_id = ? and asset_class = 'option'
            """,
            (run_id,),
        ).fetchone()
        underlying_events = int(
            db.execute(
                """
                select count(*) from tradier_stream_events
                where run_id = ? and asset_class = 'underlying'
                """,
                (run_id,),
            ).fetchone()[0]
        )
        parse_errors = int(
            db.execute(
                """
                select count(*) from tradier_stream_events
                where run_id = ? and event_type = 'parse_error'
                """,
                (run_id,),
            ).fetchone()[0]
        )
        mismatched_runs = int(
            db.execute(
                """
                select count(*) from (
                    select r.id
                    from tradier_stream_runs r
                    left join tradier_stream_events e on e.run_id = r.id
                    group by r.id
                    having r.event_count != count(e.id)
                )
                """
            ).fetchone()[0]
        )
        stale_incomplete = int(
            db.execute(
                """
                select count(*) from tradier_stream_runs
                where completed_at is null
                  and julianday('now') - julianday(started_at) > (30.0 / 1440.0)
                """
            ).fetchone()[0]
        )

    last_event = parse_time(latest["last_event_at"])
    age_minutes = None
    if last_event is not None:
        age_minutes = (datetime.now(timezone.utc) - last_event).total_seconds() / 60.0

    failures: list[str] = []
    warnings: list[str] = []
    if integrity.lower() != "ok":
        failures.append(f"SQLite integrity check returned {integrity!r}")
    if int(latest["option_contract_count"] or 0) <= 0:
        failures.append("latest run resolved zero option contracts")
    if int(option_events or 0) <= 0:
        failures.append("latest run stored zero option events")
    if stored_events != int(latest["event_count"] or 0):
        failures.append("latest run event_count does not match stored events")
    if latest["error"]:
        failures.append(f"latest run error: {latest['error']}")
    if parse_errors:
        warnings.append(f"latest run contains {parse_errors} parse-error events")
    if mismatched_runs:
        warnings.append(f"{mismatched_runs} historical runs have mismatched event counters")
    if stale_incomplete:
        warnings.append(f"{stale_incomplete} stream runs are incomplete for more than 30 minutes")
    if not args.ignore_age and (age_minutes is None or age_minutes > args.max_age_minutes):
        warnings.append(
            "latest event is stale or missing; this may be expected outside configured market hours"
        )

    status = "FAIL" if failures else ("WARN" if warnings else "PASS")
    report = {
        "status": status,
        "database": str(args.db),
        "integrity": integrity,
        "latest_run": {
            "id": run_id,
            "started_at": latest["started_at"],
            "completed_at": latest["completed_at"],
            "stop_reason": latest["stop_reason"],
            "requested_underlyings": latest["requested_underlyings"],
            "resolved_symbol_count": int(latest["resolved_symbol_count"] or 0),
            "option_contract_count": int(latest["option_contract_count"] or 0),
            "declared_event_count": int(latest["event_count"] or 0),
            "stored_event_count": stored_events,
            "underlying_events": underlying_events,
            "option_events": int(option_events or 0),
            "option_symbols_seen": int(option_symbols or 0),
            "latest_option_event_at": latest_option_at,
            "last_event_at": latest["last_event_at"],
            "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        },
        "historical_metadata": {
            "mismatched_run_counts": mismatched_runs,
            "stale_incomplete_runs": stale_incomplete,
        },
        "failures": failures,
        "warnings": warnings,
        "read_only": True,
    }
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
