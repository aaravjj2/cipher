"""Local-only market alert rule storage.

This module stores configuration only and has no broker or order capability.

Rules are evaluated in two places, and the difference matters:

* `scripts/evaluate_market_alerts.py`, run every five minutes by
  `cipher-market-alert.timer`, evaluates them server-side and pushes crossings to Telegram.
  That is what makes an alert useful when nobody is watching. It is edge-triggered, so a
  crossing notifies once rather than every pass, and it treats a stale quote as unknown
  rather than as clear so a gap in data cannot manufacture a crossing.
* The authenticated browser also evaluates them for live in-panel display. That path is a
  convenience; it is not the delivery mechanism, and it was the only one until the evaluator
  above existed, which meant an alert fired only while someone already had the tab open.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "alerts.sqlite"
KINDS = {
    "price_above", "price_below", "day_change_above", "day_change_below",
    "scanner_score_above", "flow_premium_above", "net_gex_above", "net_gex_below",
    "atm_iv_above", "atm_spread_above", "expiration_days_below",
    "portfolio_delta_abs_above", "data_stale_count_above",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id TEXT PRIMARY KEY, ticker TEXT NOT NULL, kind TEXT NOT NULL,
            threshold REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    db.execute("""CREATE TABLE IF NOT EXISTS alert_deliveries(
        delivery_key TEXT PRIMARY KEY, rule_id TEXT NOT NULL, observed_at TEXT,
        observed REAL, threshold REAL NOT NULL, channel TEXT NOT NULL,
        status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_alert_deliveries_rule_time ON alert_deliveries(rule_id,created_at DESC)")
    return db


def list_rules(db_path: Path = DEFAULT_DB) -> dict:
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT id,ticker,kind,threshold,enabled,created_at FROM alert_rules ORDER BY created_at DESC"
        ).fetchall()
        deliveries = db.execute("SELECT * FROM alert_deliveries ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"rules": [{**dict(row), "enabled": bool(row["enabled"])} for row in rows],
            "deliveries": [dict(row) for row in deliveries], "kinds": sorted(KINDS),
            "local_only": True, "execution_capability": False}


def add_rule(*, ticker: str, kind: str, threshold: object, db_path: Path = DEFAULT_DB) -> dict:
    symbol = ticker.upper().strip()
    if not symbol or len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
        raise ValueError("ticker must be a valid symbol")
    if kind not in KINDS:
        raise ValueError("unsupported alert kind")
    try:
        value = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("threshold must be numeric") from exc
    if not -1_000_000 < value < 1_000_000:
        raise ValueError("threshold is outside the supported range")
    rule = {"id": uuid.uuid4().hex, "ticker": symbol, "kind": kind,
            "threshold": value, "enabled": True, "created_at": _now()}
    with _connect(db_path) as db:
        db.execute("INSERT INTO alert_rules VALUES (?,?,?,?,?,?)",
                   (rule["id"], symbol, kind, value, 1, rule["created_at"]))
    return rule


def delete_rule(rule_id: str, db_path: Path = DEFAULT_DB) -> dict:
    with _connect(db_path) as db:
        cursor = db.execute("DELETE FROM alert_rules WHERE id = ?", (str(rule_id),))
    if cursor.rowcount != 1:
        raise ValueError("unknown alert rule")
    return {"deleted": str(rule_id)}


def record_delivery(*, rule_id: str, observed_at: str | None, observed: float | None,
                    threshold: float, channel: str, status: str, message: str,
                    db_path: Path = DEFAULT_DB) -> dict:
    """Append one idempotent delivery record for an observed crossing."""
    key = f"{rule_id}|{observed_at or 'unknown'}|{threshold:g}|{channel}"
    row = {"delivery_key": key, "rule_id": rule_id, "observed_at": observed_at,
           "observed": observed, "threshold": threshold, "channel": channel,
           "status": status, "message": message[:1000], "created_at": _now()}
    with _connect(db_path) as db:
        db.execute("INSERT OR IGNORE INTO alert_deliveries VALUES(?,?,?,?,?,?,?,?,?)", tuple(row.values()))
    return row
