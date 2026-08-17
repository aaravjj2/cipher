"""Named server-side watchlists and reproducible saved quote/scanner screens."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "watchlists.sqlite"
ALLOWED_CRITERIA = {"price_min", "price_max", "day_change_min", "day_change_max", "scanner_score_min", "optionable"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS watchlists(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS watchlist_members(watchlist_id TEXT NOT NULL,ticker TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,added_at TEXT NOT NULL,PRIMARY KEY(watchlist_id,ticker),FOREIGN KEY(watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE);
      CREATE TABLE IF NOT EXISTS saved_screens(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,watchlist_id TEXT,criteria_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    """)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _ticker(raw: object) -> str:
    value = str(raw or "").strip().upper().lstrip("$")
    if not value or len(value) > 12 or not value.replace(".", "").replace("-", "").isalnum():
        raise ValueError("ticker must be a valid symbol")
    return value


def _name(raw: object) -> str:
    value = str(raw or "").strip()
    if not 1 <= len(value) <= 80:
        raise ValueError("name must contain 1 to 80 characters")
    return value


def list_all(path: Path = DEFAULT_DB) -> dict:
    with _connect(path) as db:
        lists = []
        for row in db.execute("SELECT * FROM watchlists ORDER BY name COLLATE NOCASE"):
            members = [m["ticker"] for m in db.execute("SELECT ticker FROM watchlist_members WHERE watchlist_id=? ORDER BY position,added_at", (row["id"],))]
            lists.append({**dict(row), "tickers": members})
        screens = [{**dict(row), "criteria": json.loads(row["criteria_json"])} for row in db.execute("SELECT * FROM saved_screens ORDER BY name COLLATE NOCASE")]
        for screen in screens:
            screen.pop("criteria_json", None)
    return {"watchlists": lists, "screens": screens, "server_side": True, "execution_capability": False}


def create_watchlist(name: object, path: Path = DEFAULT_DB) -> dict:
    now, item = _now(), {"id": uuid.uuid4().hex, "name": _name(name), "created_at": _now(), "updated_at": _now(), "tickers": []}
    with _connect(path) as db:
        try:
            db.execute("INSERT INTO watchlists VALUES(?,?,?,?)", (item["id"], item["name"], now, now))
        except sqlite3.IntegrityError:
            raise ValueError("a watchlist with that name already exists") from None
    return item


def add_member(watchlist_id: str, ticker: object, path: Path = DEFAULT_DB) -> dict:
    symbol = _ticker(ticker)
    with _connect(path) as db:
        if not db.execute("SELECT 1 FROM watchlists WHERE id=?", (watchlist_id,)).fetchone():
            raise ValueError("unknown watchlist")
        position = db.execute("SELECT COALESCE(MAX(position),-1)+1 n FROM watchlist_members WHERE watchlist_id=?", (watchlist_id,)).fetchone()["n"]
        db.execute("INSERT OR IGNORE INTO watchlist_members VALUES(?,?,?,?)", (watchlist_id, symbol, position, _now()))
        db.execute("UPDATE watchlists SET updated_at=? WHERE id=?", (_now(), watchlist_id))
    return {"watchlist_id": watchlist_id, "ticker": symbol}


def remove_member(watchlist_id: str, ticker: object, path: Path = DEFAULT_DB) -> dict:
    symbol = _ticker(ticker)
    with _connect(path) as db:
        cursor = db.execute("DELETE FROM watchlist_members WHERE watchlist_id=? AND ticker=?", (watchlist_id, symbol))
    if cursor.rowcount != 1:
        raise ValueError("ticker is not in that watchlist")
    return {"watchlist_id": watchlist_id, "ticker": symbol, "deleted": True}


def delete_watchlist(watchlist_id: str, path: Path = DEFAULT_DB) -> dict:
    with _connect(path) as db:
        cursor = db.execute("DELETE FROM watchlists WHERE id=?", (watchlist_id,))
    if cursor.rowcount != 1:
        raise ValueError("unknown watchlist")
    return {"deleted": watchlist_id}


def save_screen(name: object, criteria: object, watchlist_id: str | None = None, path: Path = DEFAULT_DB) -> dict:
    if not isinstance(criteria, dict) or not criteria:
        raise ValueError("criteria must be a non-empty object")
    unknown = set(criteria) - ALLOWED_CRITERIA
    if unknown:
        raise ValueError(f"unsupported criteria: {', '.join(sorted(unknown))}")
    normalized = {}
    for key, value in criteria.items():
        normalized[key] = bool(value) if key == "optionable" else float(value)
    item = {"id": uuid.uuid4().hex, "name": _name(name), "watchlist_id": watchlist_id or None, "criteria": normalized,
            "created_at": _now(), "updated_at": _now()}
    with _connect(path) as db:
        if watchlist_id and not db.execute("SELECT 1 FROM watchlists WHERE id=?", (watchlist_id,)).fetchone():
            raise ValueError("unknown watchlist")
        try:
            db.execute("INSERT INTO saved_screens VALUES(?,?,?,?,?,?)", (item["id"], item["name"], item["watchlist_id"], json.dumps(normalized, sort_keys=True), item["created_at"], item["updated_at"]))
        except sqlite3.IntegrityError:
            raise ValueError("a screen with that name already exists") from None
    return item


def delete_screen(screen_id: str, path: Path = DEFAULT_DB) -> dict:
    with _connect(path) as db:
        cursor = db.execute("DELETE FROM saved_screens WHERE id=?", (screen_id,))
    if cursor.rowcount != 1:
        raise ValueError("unknown saved screen")
    return {"deleted": screen_id}


def run_screen(screen_id: str, *, quote_fn: Callable[[str], dict], universe: set[str], scanner_scores: dict[str, float], path: Path = DEFAULT_DB) -> dict:
    with _connect(path) as db:
        screen = db.execute("SELECT * FROM saved_screens WHERE id=?", (screen_id,)).fetchone()
        if not screen:
            raise ValueError("unknown saved screen")
        if screen["watchlist_id"]:
            symbols = [r["ticker"] for r in db.execute("SELECT ticker FROM watchlist_members WHERE watchlist_id=? ORDER BY position", (screen["watchlist_id"],))]
        else:
            symbols = sorted(universe)[:100]
    criteria = json.loads(screen["criteria_json"])
    results, errors = [], []
    for ticker in symbols:
        try:
            quote = quote_fn(ticker)
            row = {"ticker": ticker, "price": quote.get("price_context"), "day_change_pct": quote.get("day_change_pct"),
                   "as_of": quote.get("as_of"), "scanner_score": scanner_scores.get(ticker), "optionable": ticker in universe}
            checks = [
                criteria.get("price_min") is None or row["price"] is not None and row["price"] >= criteria["price_min"],
                criteria.get("price_max") is None or row["price"] is not None and row["price"] <= criteria["price_max"],
                criteria.get("day_change_min") is None or row["day_change_pct"] is not None and row["day_change_pct"] >= criteria["day_change_min"],
                criteria.get("day_change_max") is None or row["day_change_pct"] is not None and row["day_change_pct"] <= criteria["day_change_max"],
                criteria.get("scanner_score_min") is None or row["scanner_score"] is not None and row["scanner_score"] >= criteria["scanner_score_min"],
                not criteria.get("optionable") or row["optionable"],
            ]
            if all(checks):
                results.append(row)
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
    return {"id": screen_id, "name": screen["name"], "criteria": criteria, "evaluated": len(symbols), "matches": results,
            "errors": errors, "generated_at": _now(), "reproducible_inputs": {"watchlist_id": screen["watchlist_id"], "tickers": symbols},
            "execution_capability": False}
