"""Rate-conscious Finviz candidate discovery.

Finviz is an optional, delayed enrichment source.  Every symbol returned here
must still pass Cipher's Alpaca SIP/OPRA validation.  The third-party
``finvizfinance`` package parses public HTML, so failures and schema drift are
normal and never remove Cipher's configured fallback universe.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "finviz_discovery"
DEFAULT_TTL_SECONDS = 30 * 60
MAX_ROWS_PER_PRESET = 60

PRESETS: dict[str, dict] = {
    "liquid_options_momentum": {
        "filters": {"Option/Short": "Optionable", "Market Cap.": "+Mid (over $2bln)",
                    "Average Volume": "Over 1M", "Price": "Over $10",
                    "Relative Volume": "Over 1.5", "Volatility": "Week - Over 3%"},
        "order": "Relative Volume", "ascend": False,
    },
    "volatile_liquid": {
        "filters": {"Option/Short": "Optionable", "Market Cap.": "+Mid (over $2bln)",
                    "Average Volume": "Over 1M", "Price": "Over $10",
                    "Beta": "Over 1.5", "Average True Range": "Over 1"},
        "order": "Average True Range", "ascend": False,
    },
    "earnings_week": {
        "filters": {"Option/Short": "Optionable", "Earnings Date": "This Week",
                    "Average Volume": "Over 500K", "Price": "Over $10"},
        "order": "Earnings Date", "ascend": True,
    },
}


def _missing_to_none(value):
    """Map provider NaN/Infinity sentinels to an explicit unknown value."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _missing_to_none(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_missing_to_none(item) for item in value]
    return value


def normalize_ticker_rows(rows: list[dict]) -> list[dict]:
    """Repair finvizfinance's current logo/text first-character duplication.

    Apply the repair only when it is clearly a batch-wide parser signature. This
    avoids corrupting legitimate symbols such as AAPL if Finviz fixes its HTML.
    """
    raw = [str(row.get("Ticker") or row.get("ticker") or "").upper().strip() for row in rows]
    populated = [value for value in raw if value]
    duplicated = sum(len(value) > 1 and value[0] == value[1] for value in populated)
    # One legitimate symbol such as AAPL is not evidence of a batch parser bug.
    repair = len(populated) >= 3 and duplicated / len(populated) >= 0.8
    normalized = []
    for row, value in zip(rows, raw):
        item = _missing_to_none(dict(row))
        if repair and len(value) > 1 and value[0] == value[1]:
            value = value[1:]
        item["Ticker"] = value
        normalized.append(item)
    return normalized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_id(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or ch in "_-")


def _default_fetch(preset: dict) -> list[dict]:
    try:
        from finvizfinance.screener.overview import Overview
    except ImportError as exc:
        raise RuntimeError("finvizfinance is not installed; install requirements.txt") from exc
    screen = Overview()
    screen.set_filter(filters_dict=dict(preset["filters"]))
    frame = screen.screener_view(
        order=preset["order"], ascend=bool(preset.get("ascend", True)),
        limit=MAX_ROWS_PER_PRESET, verbose=0, sleep_sec=2,
    )
    if frame is None:
        return []
    return [dict(row) for row in frame.to_dict(orient="records")]


def run_preset(
    preset_id: str,
    *,
    cache_dir: Path = CACHE_DIR,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    fetch_fn: Callable[[dict], list[dict]] | None = None,
    force: bool = False,
) -> dict:
    if preset_id not in PRESETS:
        raise ValueError(f"unknown Finviz preset: {preset_id}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{_safe_id(preset_id)}.json"
    if not force and target.is_file() and time.time() - target.stat().st_mtime < ttl_seconds:
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["cache_hit"] = True
        return payload
    try:
        rows = normalize_ticker_rows((fetch_fn or _default_fetch)(PRESETS[preset_id]))
        normalized = []
        seen = set()
        for raw in rows[:MAX_ROWS_PER_PRESET]:
            symbol = str(raw.get("Ticker") or raw.get("ticker") or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            normalized.append({"ticker": symbol, "fields": raw})
        payload = {
            "schema_version": 1, "preset": preset_id, "observed_at": _now(),
            "provider": "finviz_public_html_via_finvizfinance", "delayed": True,
            "rows": normalized, "status": "AVAILABLE", "cache_hit": False,
            "filters": PRESETS[preset_id]["filters"], "read_only": True,
            "requires_alpaca_validation": True, "live_order_authority": False,
        }
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return payload
    except Exception as exc:
        if target.is_file():
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload.update({"status": "STALE_FALLBACK", "cache_hit": True,
                            "error": f"{type(exc).__name__}: {exc}"})
            return payload
        return {"schema_version": 1, "preset": preset_id, "observed_at": _now(),
                "provider": "finviz_public_html_via_finvizfinance", "delayed": True,
                "status": "UNAVAILABLE", "rows": [], "error": f"{type(exc).__name__}: {exc}",
                "read_only": True, "requires_alpaca_validation": True, "live_order_authority": False}


def discover(preset_ids: list[str] | None = None, *, limit: int = 75, **kwargs) -> dict:
    ids = preset_ids or list(PRESETS)
    screens = [run_preset(preset_id, **kwargs) for preset_id in ids]
    symbols, seen = [], set()
    for screen in screens:
        for row in screen.get("rows") or []:
            ticker = str(row.get("ticker") or "").upper()
            if ticker and ticker not in seen:
                seen.add(ticker)
                symbols.append(ticker)
                if len(symbols) >= limit:
                    break
    return {"generated_at": _now(), "symbols": symbols, "screens": screens,
            "read_only": True, "requires_alpaca_validation": True,
            "caveat": "Delayed discovery only; Cipher revalidates price and option liquidity through Alpaca."}
