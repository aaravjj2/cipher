"""Deduplicated Discord alert input for blocking local-paper failures."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Callable


BLOCKING_REASONS = {"SKIPPED_MARKET_DATA_UNAVAILABLE", "SKIPPED_DATA_FEED_DEGRADED"}


def latest_failure(db_path: Path) -> dict | None:
    if not db_path.is_file():
        return None
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """select id,event_time,event_type,payload_json from system_events
               where event_type in ('WORKER_ERROR','MARKET_DATA_PROBE_FAILED','ENTRY_BLOCKED')
               order by event_time desc limit 50"""
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if row["event_type"] != "ENTRY_BLOCKED" or payload.get("reason") in BLOCKING_REASONS:
            return {"id": row["id"], "event_time": row["event_time"], "event_type": row["event_type"], **payload}
    return None


def format_failure(event: dict) -> str:
    reason = str(event.get("reason") or event.get("error") or "unknown failure")[:300]
    ticker = f" · {event['ticker']}" if event.get("ticker") else ""
    return (
        "⚠️ **Cipher paper autopilot blocked**\n"
        f"`{event.get('event_time')}`{ticker} · {event.get('event_type')}\n"
        f"Reason: `{reason}`\nNo simulated fill was created."
    )


def deliver_latest_failure(
    sender: Callable[[str], None], *, db_path: Path, state_path: Path,
    now: datetime | None = None, maximum_age_seconds: int = 600,
) -> dict:
    event = latest_failure(db_path)
    if not event:
        return {"status": "healthy"}
    moment = now or datetime.now(timezone.utc)
    try:
        event_time = datetime.fromisoformat(str(event["event_time"]).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return {"status": "invalid_event", "event_id": event.get("id")}
    if (moment.astimezone(timezone.utc) - event_time).total_seconds() > maximum_age_seconds:
        return {"status": "stale", "event_id": event["id"]}
    try:
        prior = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prior = {}
    if prior.get("last_failure_id") == event["id"]:
        return {"status": "already_delivered", "event_id": event["id"]}
    sender(format_failure(event))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "last_failure_id": event["id"], "delivered_at": moment.astimezone(timezone.utc).isoformat(),
    }), encoding="utf-8")
    temporary.replace(state_path)
    return {"status": "delivered", "event_id": event["id"]}
