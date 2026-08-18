"""Linked manual trader journal and chart-template storage.

Records user-authored research notes and references to existing manual/paper
positions or scanner signals.  It has no account or order capability.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "trader_journal.sqlite"
FIELDS = {"ticker", "title", "status", "direction", "setup", "thesis", "invalidation",
          "targets", "tags", "entry_at", "entry_price", "exit_at", "exit_price",
          "exit_reason", "position_id", "signal_id", "chart_state", "notes", "legs"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS journal_entries(
        id TEXT PRIMARY KEY,ticker TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,direction TEXT NOT NULL,
        setup TEXT,thesis TEXT,invalidation REAL,targets_json TEXT NOT NULL,tags_json TEXT NOT NULL,
        entry_at TEXT,entry_price REAL,exit_at TEXT,exit_price REAL,exit_reason TEXT,
        position_id TEXT,signal_id TEXT,chart_state_json TEXT,notes TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        legs_json TEXT NOT NULL DEFAULT '[]',chart_snapshot_svg TEXT
      );
      CREATE TABLE IF NOT EXISTS chart_templates(
        id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,state_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
      );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(journal_entries)")}
    if "legs_json" not in columns:
        db.execute("ALTER TABLE journal_entries ADD COLUMN legs_json TEXT NOT NULL DEFAULT '[]'")
    if "chart_snapshot_svg" not in columns:
        db.execute("ALTER TABLE journal_entries ADD COLUMN chart_snapshot_svg TEXT")
    for row in db.execute("SELECT id,ticker,title,chart_state_json FROM journal_entries WHERE chart_state_json IS NOT NULL AND chart_snapshot_svg IS NULL"):
        try:
            rendered = _snapshot_svg(row[1], row[2], json.loads(row[3]))
            db.execute("UPDATE journal_entries SET chart_snapshot_svg=? WHERE id=?", (rendered, row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            # A corrupt legacy chart blob stays unrendered; it is never replaced
            # with invented evidence.
            continue
    return db


def _legs(value: Any) -> list[dict]:
    rows = _json_list(value, "legs")
    result = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("each option leg must be an object")
        symbol = str(raw.get("contract_symbol") or "").strip().upper()
        side = str(raw.get("side") or "").lower()
        quantity = int(raw.get("quantity") or 0)
        multiplier = int(raw.get("multiplier") or 100)
        if not symbol or len(symbol) > 32 or not symbol.isalnum():
            raise ValueError("each option leg needs a valid OCC contract symbol")
        if side not in {"buy", "sell"} or not 1 <= quantity <= 10_000 or multiplier not in {1, 10, 100, 1000}:
            raise ValueError("option leg side/quantity/multiplier is invalid")
        mark = raw.get("entry_mark")
        result.append({
            "contract_symbol": symbol, "side": side, "quantity": quantity,
            "multiplier": multiplier, "entry_mark": float(mark) if mark not in (None, "") else None,
            "entry_mark_type": str(raw.get("entry_mark_type") or "manual")[:40],
        })
    return result


def _snapshot_svg(ticker: str, title: str, state: dict | None) -> str | None:
    if not state:
        return None
    timeframe = escape(str(state.get("timeframe") or "chart"))
    drawings = state.get("drawings") if isinstance(state.get("drawings"), list) else []
    labels = " · ".join(escape(str((row or {}).get("label") or (row or {}).get("type") or "level")) for row in drawings[:8] if isinstance(row, dict))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="240" viewBox="0 0 960 240" role="img">'
        '<rect width="960" height="240" fill="#0b1018"/><path d="M40 190 L160 150 L280 170 L400 90 L520 120 L640 70 L760 100 L920 45" fill="none" stroke="#65d5ff" stroke-width="3"/>'
        f'<text x="40" y="38" fill="#f1f5f9" font-family="monospace" font-size="20">{escape(ticker)} · {escape(title)}</text>'
        f'<text x="40" y="65" fill="#94a3b8" font-family="monospace" font-size="13">Saved {timeframe} research state · {labels or "no drawing labels"}</text>'
        '<text x="40" y="222" fill="#64748b" font-family="monospace" font-size="11">Rendered journal evidence; schematic state preview, not a historical price export.</text></svg>'
    )


def _symbol(value: object) -> str:
    ticker = str(value or "").strip().upper()
    if not ticker or len(ticker) > 12 or not ticker.replace(".", "").replace("-", "").isalnum():
        raise ValueError("ticker must be valid")
    return ticker


def _json_list(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"{field} must be a list with at most 100 values")
    return value


def _hosted_item(row: dict) -> dict:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    item = {
        "id": row.get("id"),
        "ticker": row.get("ticker") or metadata.get("ticker"),
        "title": row.get("title") or metadata.get("title"),
        "status": metadata.get("status", "planned"),
        "direction": metadata.get("direction", "neutral"),
        "setup": metadata.get("setup"),
        "thesis": metadata.get("thesis") or row.get("body"),
        "invalidation": metadata.get("invalidation"),
        "targets": metadata.get("targets", []),
        "tags": metadata.get("tags", []),
        "entry_at": metadata.get("entry_at"),
        "entry_price": metadata.get("entry_price"),
        "exit_at": metadata.get("exit_at"),
        "exit_price": metadata.get("exit_price"),
        "exit_reason": metadata.get("exit_reason"),
        "position_id": metadata.get("position_id"),
        "signal_id": metadata.get("signal_id"),
        "chart_state": metadata.get("chart_state"),
        "notes": metadata.get("notes"),
        "legs": metadata.get("legs", []),
        "chart_snapshot_svg": metadata.get("chart_snapshot_svg"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "excursion": {"mfe_pct": None, "mae_pct": None, "bars": 0, "status": "UNAVAILABLE_HOSTED"},
        "option_excursion": {"status": "UNAVAILABLE_HOSTED"},
    }
    return item


def _hosted_metadata(record: dict) -> dict:
    return {
        "status": record.get("status"), "direction": record.get("direction"),
        "setup": record.get("setup"), "thesis": record.get("thesis"),
        "invalidation": record.get("invalidation"),
        "targets": json.loads(record.get("targets_json") or "[]"),
        "tags": json.loads(record.get("tags_json") or "[]"),
        "entry_at": record.get("entry_at"), "entry_price": record.get("entry_price"),
        "exit_at": record.get("exit_at"), "exit_price": record.get("exit_price"),
        "exit_reason": record.get("exit_reason"), "position_id": record.get("position_id"),
        "signal_id": record.get("signal_id"),
        "chart_state": json.loads(record["chart_state_json"]) if record.get("chart_state_json") else None,
        "notes": record.get("notes"), "legs": json.loads(record.get("legs_json") or "[]"),
        "chart_snapshot_svg": record.get("chart_snapshot_svg"),
    }


def create_entry(raw: dict, path: Path = DEFAULT_DB, *, repository=None) -> dict:
    ticker = _symbol(raw.get("ticker"))
    title = str(raw.get("title") or "").strip()[:160]
    if not title:
        raise ValueError("title is required")
    direction = str(raw.get("direction") or "long").lower()
    if direction not in {"long", "short", "neutral"}:
        raise ValueError("direction must be long, short, or neutral")
    status = str(raw.get("status") or "planned").lower()
    if status not in {"planned", "open", "closed", "cancelled"}:
        raise ValueError("invalid journal status")
    now = _now()
    record = {
        "id": uuid.uuid4().hex, "ticker": ticker, "title": title, "status": status,
        "direction": direction, "setup": str(raw.get("setup") or "")[:120] or None,
        "thesis": str(raw.get("thesis") or "")[:8000] or None,
        "invalidation": float(raw["invalidation"]) if raw.get("invalidation") not in (None, "") else None,
        "targets_json": json.dumps(_json_list(raw.get("targets"), "targets")),
        "tags_json": json.dumps([str(x)[:60] for x in _json_list(raw.get("tags"), "tags")]),
        "entry_at": raw.get("entry_at") or None,
        "entry_price": float(raw["entry_price"]) if raw.get("entry_price") not in (None, "") else None,
        "exit_at": raw.get("exit_at") or None,
        "exit_price": float(raw["exit_price"]) if raw.get("exit_price") not in (None, "") else None,
        "exit_reason": str(raw.get("exit_reason") or "")[:500] or None,
        "position_id": str(raw.get("position_id") or "")[:80] or None,
        "signal_id": str(raw.get("signal_id") or "")[:120] or None,
        "chart_state_json": json.dumps(raw.get("chart_state")) if raw.get("chart_state") is not None else None,
        "notes": str(raw.get("notes") or "")[:8000] or None,
        "created_at": now, "updated_at": now,
        "legs_json": json.dumps(_legs(raw.get("legs"))),
        "chart_snapshot_svg": _snapshot_svg(ticker, title, raw.get("chart_state")),
    }
    if len(record["chart_state_json"] or "") > 500_000:
        raise ValueError("chart_state is limited to 500 KB")
    if repository is not None:
        rows = repository.insert_row(
            "journal_entries",
            {
                "ticker": record["ticker"],
                "title": record["title"],
                "body": record["notes"] or record["thesis"] or "",
                "metadata": _hosted_metadata(record),
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
            },
        ) or []
        if not rows:
            raise ValueError("journal entry was not saved")
        return _hosted_item(dict(rows[0]))
    with _connect(path) as db:
        db.execute(f"INSERT INTO journal_entries({','.join(record)}) VALUES({','.join('?' for _ in record)})", tuple(record.values()))
    return _decode(record)


def _decode(row: dict | sqlite3.Row) -> dict:
    value = dict(row)
    value["targets"] = json.loads(value.pop("targets_json") or "[]")
    value["tags"] = json.loads(value.pop("tags_json") or "[]")
    chart_state = value.pop("chart_state_json")
    value["chart_state"] = json.loads(chart_state) if chart_state else None
    value["legs"] = json.loads(value.pop("legs_json") or "[]")
    return value


def update_entry(entry_id: str, raw: dict, path: Path = DEFAULT_DB, *, repository=None) -> dict:
    if repository is not None:
        existing = repository.get_row("journal_entries", entry_id)
        if not existing:
            raise ValueError("unknown journal entry")
        metadata = dict(existing.get("metadata") or {})
        for key in FIELDS:
            if key in raw and key != "ticker":
                metadata[key] = raw[key]
        ticker = _symbol(raw.get("ticker", existing.get("ticker")))
        title = str(raw.get("title", existing.get("title")) or "").strip()[:160]
        if not title:
            raise ValueError("title is required")
        metadata["ticker"] = ticker
        metadata["title"] = title
        now = _now()
        rows = repository.update_row(
            "journal_entries",
            entry_id,
            {
                "ticker": ticker,
                "title": title,
                "body": str(metadata.get("notes") or metadata.get("thesis") or ""),
                "metadata": metadata,
                "updated_at": now,
            },
        ) or []
        if not rows:
            raise ValueError("journal entry was not updated")
        return _hosted_item(dict(rows[0]))
    updates = {key: value for key, value in raw.items() if key in FIELDS}
    with _connect(path) as db:
        current = db.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
        if not current:
            raise ValueError("unknown journal entry")
        if "ticker" in updates:
            updates["ticker"] = _symbol(updates["ticker"])
        if "title" in updates:
            updates["title"] = str(updates["title"] or "").strip()[:160]
            if not updates["title"]:
                raise ValueError("title is required")
        if "direction" in updates:
            updates["direction"] = str(updates["direction"] or "").lower()
            if updates["direction"] not in {"long", "short", "neutral"}:
                raise ValueError("direction must be long, short, or neutral")
        if "status" in updates:
            updates["status"] = str(updates["status"] or "").lower()
            if updates["status"] not in {"planned", "open", "closed", "cancelled"}:
                raise ValueError("invalid journal status")
        for key in ("invalidation", "entry_price", "exit_price"):
            if key in updates:
                updates[key] = float(updates[key]) if updates[key] not in (None, "") else None
        if "targets" in updates:
            updates["targets"] = _json_list(updates["targets"], "targets")
        if "tags" in updates:
            updates["tags"] = [str(x)[:60] for x in _json_list(updates["tags"], "tags")]
        if "legs" in updates:
            updates["legs"] = _legs(updates["legs"])
        for key, limit in (("setup", 120), ("thesis", 8000), ("exit_reason", 500),
                           ("position_id", 80), ("signal_id", 120), ("notes", 8000)):
            if key in updates:
                updates[key] = str(updates[key] or "")[:limit] or None
        if "chart_state" in updates:
            if updates["chart_state"] is not None and not isinstance(updates["chart_state"], dict):
                raise ValueError("chart_state must be an object or null")
            if len(json.dumps(updates["chart_state"])) > 500_000:
                raise ValueError("chart_state is limited to 500 KB")
        encoded = {}
        for key, value in updates.items():
            target = f"{key}_json" if key in {"targets", "tags", "chart_state"} else key
            encoded[target] = json.dumps(value) if key in {"targets", "tags", "chart_state"} else value
        encoded["updated_at"] = _now()
        if "chart_state" in updates:
            encoded["chart_snapshot_svg"] = _snapshot_svg(
                updates.get("ticker", current["ticker"]), updates.get("title", current["title"]), updates["chart_state"]
            )
        db.execute(f"UPDATE journal_entries SET {','.join(f'{key}=?' for key in encoded)} WHERE id=?", (*encoded.values(), entry_id))
        saved = db.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    return _decode(saved)


def delete_entry(entry_id: str, path: Path = DEFAULT_DB, *, repository=None) -> dict:
    if repository is not None:
        if not repository.get_row("journal_entries", entry_id):
            raise ValueError("unknown journal entry")
        repository.delete_row("journal_entries", entry_id)
        return {"deleted": entry_id}
    with _connect(path) as db:
        cursor = db.execute("DELETE FROM journal_entries WHERE id=?", (entry_id,))
    if cursor.rowcount != 1:
        raise ValueError("unknown journal entry")
    return {"deleted": entry_id}


def _excursion(entry: dict, bars_fn: Callable[..., dict] | None) -> dict:
    if not bars_fn or not entry.get("entry_at") or not entry.get("entry_price") or entry["direction"] == "neutral":
        return {"mfe_pct": None, "mae_pct": None, "bars": 0, "status": "UNAVAILABLE"}
    try:
        rows = (bars_fn(entry["ticker"], "5m", limit=1000, start=str(entry["entry_at"])[:10]) or {}).get("bars") or []
    except Exception as exc:
        return {"mfe_pct": None, "mae_pct": None, "bars": 0, "status": "UNAVAILABLE", "error": str(exc)}
    rows = [row for row in rows if str(row.get("time") or "") >= entry["entry_at"] and (not entry.get("exit_at") or str(row.get("time") or "") <= entry["exit_at"])]
    if not rows:
        return {"mfe_pct": None, "mae_pct": None, "bars": 0, "status": "NO_BARS"}
    price = float(entry["entry_price"])
    if entry["direction"] == "long":
        mfe, mae = (max(float(r["high"]) for r in rows) / price - 1) * 100, (min(float(r["low"]) for r in rows) / price - 1) * 100
    else:
        mfe, mae = (1 - min(float(r["low"]) for r in rows) / price) * 100, (1 - max(float(r["high"]) for r in rows) / price) * 100
    # Keep API output stable and human-readable; binary floating-point noise is
    # not meaningful at the precision of underlying 5-minute OHLC bars.
    return {"mfe_pct": round(mfe, 6), "mae_pct": round(mae, 6), "bars": len(rows), "status": "CALCULATED_FROM_UNDERLYING_5M"}


def list_entries(*, ticker: str | None = None, bars_fn: Callable[..., dict] | None = None, path: Path = DEFAULT_DB, repository=None) -> dict:
    if repository is not None:
        query = {"ticker": f"eq.{_symbol(ticker)}"} if ticker else {}
        rows = repository.list_rows("journal_entries", query=query) or []
        return {
            "entries": [_hosted_item(dict(row)) for row in rows],
            "as_of": _now(),
            "manual_journal": True,
            "execution_capability": False,
            "caveat": "User-authored research journal. Hosted entries use Supabase RLS; excursion analytics remain unavailable until a user-scoped bar source is attached.",
        }
    with _connect(path) as db:
        rows = db.execute("SELECT * FROM journal_entries WHERE (? IS NULL OR ticker=?) ORDER BY created_at DESC", (ticker, ticker)).fetchall()
    entries = []
    for row in rows:
        item = _decode(row)
        item["excursion"] = _excursion(item, bars_fn)
        try:
            from core import journal_option_analytics
        except ImportError:
            import journal_option_analytics
        item["option_excursion"] = journal_option_analytics.analyze(item)
        entries.append(item)
    return {"entries": entries, "as_of": _now(), "manual_journal": True, "execution_capability": False,
            "caveat": "User-authored research journal. Underlying and captured option marks are separate; bid/mid/ask paths are simulated valuations, never claimed fills."}


def save_template(name: object, state: object, path: Path = DEFAULT_DB, *, repository=None) -> dict:
    label = str(name or "").strip()[:80]
    if not label or not isinstance(state, dict):
        raise ValueError("template name and state object are required")
    raw = json.dumps(state)
    if len(raw) > 500_000:
        raise ValueError("template state is limited to 500 KB")
    now, template_id = _now(), uuid.uuid4().hex
    if repository is not None:
        existing = repository.list_rows("chart_templates", query={"name": f"eq.{label}"}) or []
        if existing:
            rows = repository.update_row(
                "chart_templates", str(existing[0]["id"]), {"name": label, "state": state, "updated_at": now},
            ) or []
        else:
            rows = repository.insert_row(
                "chart_templates", {"name": label, "state": state, "created_at": now, "updated_at": now},
            ) or []
        if not rows:
            raise ValueError("template was not saved")
        return {**dict(rows[0]), "state": rows[0].get("state") or state}
    with _connect(path) as db:
        db.execute("INSERT INTO chart_templates VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at", (template_id, label, raw, now, now))
        row = db.execute("SELECT * FROM chart_templates WHERE name=?", (label,)).fetchone()
    return {**dict(row), "state": json.loads(row["state_json"])}


def list_templates(path: Path = DEFAULT_DB, *, repository=None) -> dict:
    if repository is not None:
        rows = repository.list_rows("chart_templates", query={"order": "name.asc"}) or []
        return {"templates": [dict(row) for row in rows], "execution_capability": False}
    with _connect(path) as db:
        rows = db.execute("SELECT * FROM chart_templates ORDER BY name").fetchall()
    return {"templates": [{**dict(row), "state": json.loads(row["state_json"])} for row in rows], "execution_capability": False}
