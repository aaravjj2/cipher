"""User-owned chart snapshots for hosted mode.

The local browser build keeps its existing localStorage fallback. Hosted mode
uses this repository path so a Supabase user never inherits another browser's
saved charts.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ticker(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol or len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
        raise ValueError("ticker must be valid")
    return symbol


def _levels(value: Any) -> list[dict]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("top_levels must be a list with at most 20 values")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each chart level must be an object")
        try:
            level = float(item.get("level"))
            score = float(item.get("score"))
        except (TypeError, ValueError):
            raise ValueError("chart level and score must be numeric") from None
        if not math.isfinite(level) or not math.isfinite(score):
            raise ValueError("chart level and score must be finite")
        result.append({"level": level, "score": score})
    return result


def _decode(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "ticker": row.get("ticker"),
        "price": row.get("price"),
        "view": row.get("view"),
        "dateAdded": row.get("date_added"),
        "topLevels": row.get("top_levels") or [],
        "imageUrl": row.get("image_url") or "",
    }


def list_saves(*, repository) -> dict:
    rows = repository.list_rows("chart_saves", query={"order": "created_at.desc"}) or []
    return {"saves": [_decode(dict(row)) for row in rows], "read_only": True, "execution_capability": False}


def create_save(raw: dict, *, repository) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("chart save must be an object")
    try:
        price = float(raw.get("price"))
    except (TypeError, ValueError):
        raise ValueError("price must be numeric") from None
    if not math.isfinite(price) or price < 0:
        raise ValueError("price must be a finite non-negative number")
    view = str(raw.get("view") or "").strip()[:80]
    date_added = str(raw.get("dateAdded") or "").strip()[:40]
    if not view or not date_added:
        raise ValueError("view and dateAdded are required")
    rows = repository.insert_row("chart_saves", {
        "ticker": _ticker(raw.get("ticker")), "price": price, "view": view,
        "date_added": date_added, "top_levels": _levels(raw.get("topLevels")),
        "image_url": str(raw.get("imageUrl") or "")[:500], "created_at": _now(),
    }) or []
    if not rows:
        raise ValueError("chart save was not saved")
    return _decode(dict(rows[0]))


def delete_save(save_id: str, *, repository) -> dict:
    if not repository.get_row("chart_saves", str(save_id)):
        raise ValueError("unknown chart save")
    repository.delete_row("chart_saves", str(save_id))
    return {"deleted": str(save_id), "execution_capability": False}
