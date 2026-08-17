"""Revisioned local event context with an honest unavailable earnings seam."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "event_context"
LATEST = DATA_DIR / "latest.json"
LEDGER = DATA_DIR / "revisions.jsonl"
EVENT_DB = DATA_DIR / "event_calendar.sqlite"


def snapshot(symbols: list[str], corporate_actions: list[dict], *, observed_at: str | None = None,
             earnings: dict | None = None) -> dict:
    moment = observed_at or datetime.now(timezone.utc).isoformat()
    actions = [dict(row) for row in corporate_actions if isinstance(row, dict)]
    source_hash = hashlib.sha256(json.dumps(actions, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {
        "schema_version": 1, "observed_at": moment, "symbols": sorted(set(symbols)),
        "corporate_actions": actions,
        "corporate_action_source": {
            "provider": "alpaca_market_data", "sha256": source_hash,
            "point_in_time_ready": False,
            "limitation": "Provider action creation time is not guaranteed; events are context, not point-in-time backtest evidence.",
        },
        "earnings": earnings or {
            "status": "UNAVAILABLE", "provider": None, "events": [],
            "detail": "No authoritative upcoming-earnings provider is configured; Cipher does not infer dates from headlines.",
        },
        "read_only": True, "live_order_authority": False,
    }


def save(payload: dict, *, directory: Path = DATA_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    latest = directory / "latest.json"
    ledger = directory / "revisions.jsonl"
    tmp = directory / ".latest.tmp.json"
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(latest)
    revision = {
        "observed_at": payload["observed_at"],
        "source_sha256": payload["corporate_action_source"]["sha256"],
        "symbols": payload["symbols"], "actions": len(payload["corporate_actions"]),
    }
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(revision, sort_keys=True) + "\n")
    _record_event_revisions(payload, directory / "event_calendar.sqlite")
    return latest


def _record_event_revisions(payload: dict, db_path: Path = EVENT_DB) -> None:
    """Append a bitemporal observation without overwriting prior provider claims."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    observed_at = str(payload.get("observed_at") or datetime.now(timezone.utc).isoformat())
    rows = list((payload.get("earnings") or {}).get("events") or [])
    with sqlite3.connect(db_path) as db:
        db.executescript("""
        pragma journal_mode=WAL;
        create table if not exists event_revisions(
          fingerprint text primary key, symbol text not null, event_type text not null,
          scheduled_date text, timing text, status text not null, provider text not null,
          provider_event_id text, first_observed_at text not null, last_observed_at text not null,
          conflict integer not null default 0, payload_json text not null
        );
        create index if not exists idx_event_symbol_date on event_revisions(symbol,scheduled_date);
        """)
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            provider = str(row.get("provider") or "unknown")
            provider_id = str(row.get("provider_event_id") or "")
            scheduled = row.get("scheduled_date")
            if not symbol or not scheduled:
                continue
            fingerprint = hashlib.sha256(f"earnings|{symbol}|{provider}|{provider_id}|{scheduled}|{row.get('timing')}".encode()).hexdigest()
            db.execute("""
              insert into event_revisions values(?,?,?,?,?,?,?,?,?,?,?,?)
              on conflict(fingerprint) do update set last_observed_at=excluded.last_observed_at,
                conflict=excluded.conflict,payload_json=excluded.payload_json
            """, (fingerprint, symbol, "earnings", scheduled, row.get("timing"), row.get("status") or "ESTIMATED",
                  provider, provider_id or None, observed_at, observed_at, int(bool(row.get("conflict"))),
                  json.dumps(row, sort_keys=True, default=str)))


def for_ticker(ticker: str, *, latest: Path = LATEST) -> dict:
    if not latest.exists():
        return {
            "earnings": snapshot([], [])["earnings"],
            "corporate_actions": {"status": "UNAVAILABLE", "events": [],
                "detail": "The scheduled corporate-actions snapshot has not completed yet."},
        }
    payload = json.loads(latest.read_text(encoding="utf-8"))
    symbol = ticker.upper()
    rows = [row for row in payload.get("corporate_actions", []) if str(row.get("symbol") or (row.get("event") or {}).get("symbol") or "").upper() == symbol]
    earnings = dict(payload.get("earnings") or snapshot([], [])["earnings"])
    earnings["events"] = [row for row in earnings.get("events", []) if str(row.get("symbol") or "").upper() == symbol]
    if earnings.get("status") == "AVAILABLE" and not earnings["events"]:
        earnings["status"] = "AVAILABLE_NO_MATCHING_EVENTS"
    return {
        "earnings": earnings,
        "corporate_actions": {
            "status": "AVAILABLE" if rows else "AVAILABLE_NO_MATCHING_EVENTS",
            "events": rows, "observed_at": payload.get("observed_at"),
            "source": payload.get("corporate_action_source"),
            "detail": "Provider-observed actions; creation-time limitation prevents point-in-time backtest use.",
        },
    }
