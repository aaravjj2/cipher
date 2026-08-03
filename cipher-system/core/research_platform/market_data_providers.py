"""Read-only Alpaca and Tradier data adapters for local research provenance."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

from core.env import load_local_env

load_local_env()


class MarketDataProviderError(RuntimeError):
    """Raised for missing read-only provider credentials or invalid responses."""


def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise MarketDataProviderError("provider returned a non-object JSON response")
    return payload


def _alpaca_credentials() -> tuple[str, str]:
    key = os.environ.get("ALPACA_ALGO_PLUS_KEY") or os.environ.get("ALPACA_ALGO_KEY") or os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_ALGO_PLUS_SECRET") or os.environ.get("ALPACA_ALGO_SECRET") or os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise MarketDataProviderError("Alpaca read-only market-data credentials are not configured")
    return key, secret


def _massive_credentials() -> str:
    key = os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY")
    if not key:
        raise MarketDataProviderError("Massive/Polygon read-only market-data credential is not configured")
    return key


def fetch_alpaca_corporate_actions(
    symbols: Iterable[str], *, start: str, end: str,
    request_json: Callable[[str, dict[str, str]], dict[str, Any]] = _get_json,
) -> list[dict[str, Any]]:
    """Fetch corporate-action records; this method never accesses account/order APIs."""
    key, secret = _alpaca_credentials()
    normalized = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not normalized:
        raise ValueError("at least one symbol is required")
    query = urllib.parse.urlencode({"symbols": ",".join(normalized), "start": start, "end": end, "limit": "1000"})
    payload = request_json(
        "https://data.alpaca.markets/v1/corporate-actions?" + query,
        {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"},
    )
    actions = payload.get("corporate_actions") or {}
    if not isinstance(actions, dict):
        raise MarketDataProviderError("Alpaca corporate_actions must be an object")
    rows = []
    for action_type, events in actions.items():
        for event in events or []:
            if not isinstance(event, dict):
                continue
            rows.append({
                "provider": "alpaca_market_data",
                "action_type": action_type,
                "symbol": event.get("symbol"),
                "event": event,
                "point_in_time_ready": False,
                "availability_limitation": "provider documentation does not guarantee corporate-action creation time",
            })
    return rows


def fetch_tradier_daily_history(
    symbol: str, *, start: str, end: str,
    request_json: Callable[[str, dict[str, str]], dict[str, Any]] = _get_json,
) -> list[dict[str, Any]]:
    """Fetch read-only daily OHLCV bars for independent reconciliation."""
    token = os.environ.get("TRADIER_ACCESS_TOKEN")
    if not token:
        raise MarketDataProviderError("Tradier read-only market-data credential is not configured")
    query = urllib.parse.urlencode({"symbol": str(symbol).upper(), "interval": "daily", "start": start, "end": end})
    payload = request_json(
        "https://api.tradier.com/v1/markets/history?" + query,
        {"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    history = payload.get("history") or {}
    days = history.get("day") or [] if isinstance(history, dict) else []
    if isinstance(days, dict):
        days = [days]
    if not isinstance(days, list):
        raise MarketDataProviderError("Tradier history.day must be an array or object")
    return [dict(day) for day in days if isinstance(day, dict)]


def fetch_massive_minute_bars(
    symbol: str, *, start: str, end: str,
    adjusted: bool = False,
    request_json: Callable[[str, dict[str, str]], dict[str, Any]] = _get_json,
) -> list[dict[str, Any]]:
    """Fetch normalized read-only minute bars from Massive/Polygon.

    The caller must separately run the immutable source, session, continuity,
    and Holdout C cohort gates.  This helper deliberately has no fallback to a
    second vendor, no account endpoint, and no order capability.
    """
    normalized = str(symbol).strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    key = _massive_credentials()
    query = urllib.parse.urlencode({"adjusted": str(bool(adjusted)).lower(), "sort": "asc", "limit": "50000", "apiKey": key})
    payload = request_json(
        f"https://api.polygon.io/v2/aggs/ticker/{urllib.parse.quote(normalized, safe='')}/range/1/minute/{start}/{end}?{query}",
        {"Accept": "application/json"},
    )
    if payload.get("status") not in {None, "OK"}:
        raise MarketDataProviderError("Massive/Polygon minute-bar request was not authorized or did not complete")
    bars = payload.get("results") or []
    if not isinstance(bars, list):
        raise MarketDataProviderError("Massive/Polygon results must be an array")
    normalized_rows = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        try:
            normalized_rows.append({
                "provider": "massive_polygon", "ticker": normalized, "timestamp_ms": int(bar["t"]),
                "open": float(bar["o"]), "high": float(bar["h"]), "low": float(bar["l"]), "close": float(bar["c"]),
                "volume": bar.get("v"), "trade_count": bar.get("n"), "vwap": bar.get("vw"), "adjusted": bool(adjusted),
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataProviderError("Massive/Polygon minute bar has invalid OHLC/timestamp fields") from exc
    return normalized_rows
