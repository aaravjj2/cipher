"""Deduplicated Discord alert input for blocking local-paper failures."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Callable


BLOCKING_REASONS = {"SKIPPED_MARKET_DATA_UNAVAILABLE", "SKIPPED_DATA_FEED_DEGRADED"}


def latest_failure(db_path: Path, *, acknowledged_exits=()) -> dict | None:
    if not db_path.is_file():
        return None
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        # Position state is authoritative: an unresolved exit is not made
        # healthy by old timestamps, busy logs, or a later startup event.
        for position in db.execute("select id,ticker,payload_json from paper_positions where status in ('OPEN','SHADOW_OPEN') order by opened_at,id"):
            try:
                payload = json.loads(position['payload_json'])
                pending = payload.get('pending_exit') if isinstance(payload, dict) else None
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(pending, dict) or not pending.get('since') or not pending.get('reason'):
                continue
            identity = f"exit:{position['id']}:{pending['since']}"
            if identity not in acknowledged_exits:
                return {**pending, 'id': identity, 'position_id': position['id'],
                        'ticker': position['ticker'], 'event_time': pending['since'], 'event_type': 'EXIT_PENDING'}
        rows = db.execute(
            """select id,event_time,event_type,payload_json from system_events
               where event_type in ('WORKER_ERROR','MARKET_DATA_PROBE_FAILED','AUTO_PAPER_PROMOTION','BROKER_RECONCILIATION_FAILED')
                  or (event_type='ENTRY_BLOCKED' and json_valid(payload_json)
                      and json_extract(payload_json,'$.reason') in ('SKIPPED_MARKET_DATA_UNAVAILABLE','SKIPPED_DATA_FEED_DEGRADED'))
               order by event_time desc limit 50"""
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if row["event_type"] == "AUTO_PAPER_PROMOTION":
            # A later successful promotion is the recovery boundary for older
            # startup/reconciliation failures; do not keep reporting history as
            # the current incident after the executor has recovered.
            if payload.get("ok") is not False:
                return None
            return {**payload, "id": row["id"], "event_time": row["event_time"], "event_type": row["event_type"]}
        if row["event_type"] != "ENTRY_BLOCKED" or payload.get("reason") in BLOCKING_REASONS:
            return {**payload, "id": row["id"], "event_time": row["event_time"], "event_type": row["event_type"]}
    return None


def format_failure(event: dict) -> str:
    reason = str(event.get("reason") or event.get("error") or "unknown failure")[:300]
    ticker = f" · {event['ticker']}" if event.get("ticker") else ""
    if event.get("event_type") == "EXIT_PENDING":
        return ("⚠️ **Cipher paper exit pending**\n"
                f"Position: `{event.get('position_id')}` · since `{event.get('since')}`\n"
                f"Reason: `{reason}` · {event.get('error', 'quote unavailable')}\n"
                "Position remains open; the worker will retry with fresh quotes.")
    return (
        "⚠️ **Cipher paper autopilot blocked**\n"
        f"`{event.get('event_time')}`{ticker} · {event.get('event_type')}\n"
        f"Reason: `{reason}`\nNo simulated fill was created."
    )


def deliver_latest_failure(
    sender: Callable[[str], None], *, db_path: Path, state_path: Path,
    now: datetime | None = None, maximum_age_seconds: int = 600,
) -> dict:
    try:
        prior = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prior = {}
    if not isinstance(prior, dict):
        prior = {}
    acknowledged = prior.get('acknowledged_exits', [])
    if not isinstance(acknowledged, list):
        acknowledged = []
    # Preserve the previous notifier's receipt during an additive upgrade.
    if str(prior.get('last_failure_id', '')).startswith('exit:'):
        acknowledged = [*acknowledged, prior['last_failure_id']]
    event = latest_failure(db_path, acknowledged_exits=acknowledged)
    if not event:
        return {"status": "already_delivered" if latest_failure(db_path) else "healthy"}
    moment = now or datetime.now(timezone.utc)
    try:
        event_time = datetime.fromisoformat(str(event["event_time"]).replace("Z", "+00:00"))
        if event_time.tzinfo is None or moment.tzinfo is None:
            raise ValueError('timezone_required')
        event_time = event_time.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return {"status": "invalid_event", "event_id": event.get("id")}
    age = (moment.astimezone(timezone.utc) - event_time).total_seconds()
    if age < 0:
        return {"status": "invalid_event", "event_id": event.get("id")}
    if event['event_type'] != 'EXIT_PENDING' and age > maximum_age_seconds:
        return {"status": "stale", "event_id": event["id"]}
    if prior.get("last_failure_id") == event["id"]:
        return {"status": "already_delivered", "event_id": event["id"]}
    sender(format_failure(event) + '\nEvent: ' + str(event['id']))
    if event['event_type'] == 'EXIT_PENDING':
        acknowledged = sorted(set([*acknowledged, event['id']]))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "last_failure_id": event["id"], "delivered_at": moment.astimezone(timezone.utc).isoformat(),
        "acknowledged_exits": acknowledged,
    }), encoding="utf-8")
    temporary.replace(state_path)
    return {"status": "delivered", "event_id": event["id"]}
