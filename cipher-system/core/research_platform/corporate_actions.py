"""Provenance-first corporate-action snapshots for research pilots.

Snapshots intentionally do not adjust OHLCV. Public-source retrieval time is
not evidence of when a historical action was known, so all rows retain an
explicit availability limitation until a point-in-time vendor is selected.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable


def normalize_actions(symbol: str, actions: Any, *, retrieved_at: datetime | None = None) -> list[dict[str, Any]]:
    """Normalize a yfinance-style actions table into explicit research-only rows."""
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    retrieved = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows: list[dict[str, Any]] = []
    if actions is None or getattr(actions, "empty", True):
        return rows
    for index, row in actions.iterrows():
        day = index.date().isoformat()
        for column, action_type in (("Stock Splits", "split"), ("Dividends", "cash_dividend")):
            value = float(row.get(column) or 0.0)
            if value == 0.0:
                continue
            rows.append({
                "symbol": symbol,
                "effective_date": day,
                "action_type": action_type,
                "value": value,
                "source": "yfinance_yahoo_actions",
                "retrieved_at": retrieved.isoformat(),
                "availability_at": None,
                "allowed_use": "research_reference_only",
                "adjustment_authorized": False,
                "availability_limitation": "historical point-in-time availability is not established",
            })
    return rows


def capture_actions(
    symbols: Iterable[str],
    *,
    fetch_actions: Callable[[str], Any],
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    """Capture a source snapshot for a small registered pilot universe."""
    rows: list[dict[str, Any]] = []
    normalized_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    for symbol in normalized_symbols:
        rows.extend(normalize_actions(symbol, fetch_actions(symbol), retrieved_at=retrieved_at))
    return {
        "schema_version": 1,
        "source": "yfinance_yahoo_actions",
        "symbols": normalized_symbols,
        "rows": rows,
        "adjustment_authorized": False,
        "point_in_time_ready": False,
        "notes": [
            "Snapshot is for source evaluation and spot checks only.",
            "Do not use it to adjust prices until availability and symbol mapping are independently validated.",
        ],
    }
