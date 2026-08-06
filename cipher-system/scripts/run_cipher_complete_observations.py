#!/usr/bin/env python3
"""Build complete Flash, Agentic, and expiry-aware Cluster observations.

Flash and Agentic retain next-open fixed-horizon diagnostics. Cluster is treated
as an expiration-defined options setup: the scanner's documented second listed
expiration is reconstructed from point-in-time contract metadata, and the
underlying, ATM directional option, target-strike option, and ATM/target debit
spread are measured from the alert through that expiration (or the latest
available mark when the expiration is still pending).

This is read-only market-data research. It contains no account or order calls.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from core.historical_options_download import alpaca_credentials  # noqa: E402
from core.research_platform.cipher_signal_overlay import (  # noqa: E402
    SCAN_TYPES,
    capture_inventory,
    eligible_episode,
    load_signal_episodes,
    signal_file_manifest,
)
from core.research_platform.hashing import stable_id  # noqa: E402
from run_cipher_signal_only_research import (  # noqa: E402
    agreement_context,
    daily_latest_states,
    latest_dataset,
    score_states,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
OUTPUT_ROOT = ROOT / "data" / "governance" / "cipher_signal_only"
OUTPUT = OUTPUT_ROOT / "latest_complete_observations.json"
CACHE_ROOT = OUTPUT_ROOT / "provider_cache"
CONTRACT_CACHE = CACHE_ROOT / "contracts"
OPTION_MINUTE_CACHE = CACHE_ROOT / "option_1min"
OPTION_DAILY_CACHE = CACHE_ROOT / "option_1day"
STOCK_MINUTE_CACHE = CACHE_ROOT / "stock_1min"
STOCK_DAILY_CACHE = CACHE_ROOT / "stock_1day"
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
GEX_ROOT = ROOT / "data" / "gex_snapshots"
OPTION_CONTRACTS_URL = "https://paper-api.alpaca.markets/v2/options/contracts"
OPTION_BARS_URL = "https://data.alpaca.markets/v1beta1/options/bars"
STOCK_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def utc_timestamp(value: Any) -> pd.Timestamp | None:
    result = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(result) else pd.Timestamp(result)


def iso_z(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def provider_headers() -> dict[str, str]:
    key, secret, _ = alpaca_credentials()
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "User-Agent": "Cipher-Complete-Signal-Research/1.0",
    }


def request_json(
    url: str,
    query: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    retries: int = 7,
    timeout: int = 90,
) -> dict[str, Any]:
    clean = {str(key): value for key, value in query.items() if value not in (None, "")}
    request_url = f"{url}?{urllib.parse.urlencode(clean)}"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(request_url, headers=dict(headers))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("provider response was not a JSON object")
            return payload
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt >= retries:
                raise last_error from exc
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(1.5 * (2**attempt), 30.0)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt >= retries:
                raise RuntimeError(f"provider request failed: {exc}") from exc
            time.sleep(min(1.5 * (2**attempt), 30.0))
    raise RuntimeError(f"provider request failed: {last_error}")


def paged_rows(
    url: str,
    query: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    data_key: str,
) -> list[dict[str, Any]]:
    current = dict(query)
    output: list[dict[str, Any]] = []
    while True:
        payload = request_json(url, current, headers)
        rows = payload.get(data_key) or []
        if isinstance(rows, list):
            output.extend(row for row in rows if isinstance(row, dict))
        token = payload.get("next_page_token")
        if not token:
            break
        current["page_token"] = token
    return output


def target_ranges(cluster_states: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in cluster_states:
        ticker = str(row.get("ticker") or "").upper()
        for key in ("spot", "target"):
            value = finite(row.get(key))
            if ticker and value is not None and value > 0:
                grouped[ticker].append(value)
    output: dict[str, dict[str, float]] = {}
    for ticker, values in grouped.items():
        low = min(values)
        high = max(values)
        padding = max(5.0, high * 0.15)
        output[ticker] = {
            "strike_gte": max(0.5, low - padding),
            "strike_lte": high + padding,
        }
    return output


def compact_contract(row: Mapping[str, Any]) -> dict[str, Any] | None:
    symbol = str(row.get("symbol") or "").upper()
    expiry = str(row.get("expiration_date") or "")[:10]
    option_type = str(row.get("type") or "").lower()
    strike = finite(row.get("strike_price"))
    underlying = str(row.get("underlying_symbol") or "").upper()
    if not symbol or not expiry or strike is None or option_type not in {"call", "put"}:
        return None
    return {
        "symbol": symbol,
        "underlying_symbol": underlying,
        "expiration_date": expiry,
        "strike_price": strike,
        "type": option_type,
        "status": row.get("status"),
    }


def contract_cache_path(ticker: str) -> Path:
    return CONTRACT_CACHE / f"{ticker.upper()}.json"


def fetch_contracts_for_ticker(
    ticker: str,
    strike_range: Mapping[str, float],
    start: str,
    end: str,
    headers: Mapping[str, str],
    *,
    force: bool,
) -> dict[str, Any]:
    path = contract_cache_path(ticker)
    cached = read_json(path)
    query_identity = {
        "ticker": ticker,
        "start": start,
        "end": end,
        "strike_gte": round(float(strike_range["strike_gte"]), 4),
        "strike_lte": round(float(strike_range["strike_lte"]), 4),
    }
    if not force and isinstance(cached, dict) and cached.get("query") == query_identity and cached.get("complete"):
        return cached
    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for status in ("inactive", "active"):
        query = {
            "underlying_symbols": ticker,
            "status": status,
            "expiration_date_gte": start,
            "expiration_date_lte": end,
            "strike_price_gte": query_identity["strike_gte"],
            "strike_price_lte": query_identity["strike_lte"],
            "limit": 1000,
        }
        try:
            rows = paged_rows(OPTION_CONTRACTS_URL, query, headers, data_key="option_contracts")
        except Exception as exc:
            errors.append(f"{status}:{type(exc).__name__}:{exc}")
            continue
        for row in rows:
            compact = compact_contract(row)
            if compact:
                merged[compact["symbol"]] = compact
    payload = {
        "schema_version": 1,
        "downloaded_at": datetime.now(UTC).isoformat(),
        "query": query_identity,
        "complete": not errors,
        "errors": errors,
        "contracts": sorted(merged.values(), key=lambda row: (row["expiration_date"], row["strike_price"], row["type"])),
    }
    atomic_json(path, payload)
    return payload


def fetch_contract_universe(
    cluster_states: Sequence[Mapping[str, Any]],
    *,
    start: str,
    end: str,
    workers: int,
    force: bool,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    ranges = target_ranges(cluster_states)
    headers = provider_headers()
    contracts: dict[str, list[dict[str, Any]]] = {}
    diagnostics: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(fetch_contracts_for_ticker, ticker, value, start, end, headers, force=force): ticker
            for ticker, value in sorted(ranges.items())
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                payload = future.result()
            except Exception as exc:
                diagnostics.append({"ticker": ticker, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
                contracts[ticker] = []
                continue
            rows = payload.get("contracts") if isinstance(payload, dict) else []
            contracts[ticker] = [row for row in rows or [] if isinstance(row, dict)]
            diagnostics.append(
                {
                    "ticker": ticker,
                    "status": "complete" if payload.get("complete") else "partial",
                    "contracts": len(contracts[ticker]),
                    "errors": payload.get("errors") or [],
                }
            )
    return contracts, sorted(diagnostics, key=lambda row: row["ticker"])


def nearest_contract(
    contracts: Sequence[Mapping[str, Any]],
    *,
    expiry: str,
    option_type: str,
    strike_target: float,
) -> dict[str, Any] | None:
    candidates = [
        dict(row)
        for row in contracts
        if str(row.get("expiration_date")) == expiry and str(row.get("type")) == option_type
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: (abs(float(row["strike_price"]) - strike_target), float(row["strike_price"])))


def expiry_for_state(row: Mapping[str, Any], contracts: Sequence[Mapping[str, Any]]) -> tuple[str | None, str]:
    declared = str(row.get("declared_expiration") or "")[:10]
    if declared:
        return declared, "declared_on_source_card"
    session = str(row.get("market_session") or "")[:10]
    expiries = sorted(
        {
            str(contract.get("expiration_date"))
            for contract in contracts
            if str(contract.get("expiration_date") or "") >= session
        }
    )
    if len(expiries) >= 2:
        return expiries[1], "provider_second_listed_expiration"
    return None, "second_listed_expiration_unavailable"


def enrich_cluster_contracts(
    cluster_states: Sequence[Mapping[str, Any]],
    contracts_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for state in cluster_states:
        row = dict(state)
        ticker = str(row.get("ticker") or "").upper()
        direction = str(row.get("direction") or "").upper()
        option_type = "call" if direction == "BULLISH" else "put" if direction == "BEARISH" else ""
        contracts = list(contracts_by_ticker.get(ticker) or [])
        expiry, expiry_method = expiry_for_state(row, contracts)
        spot = finite(row.get("spot"))
        target = finite(row.get("target"))
        atm = None
        target_contract = None
        if expiry and option_type and spot is not None:
            atm = nearest_contract(contracts, expiry=expiry, option_type=option_type, strike_target=spot)
        if expiry and option_type and target is not None:
            target_contract = nearest_contract(contracts, expiry=expiry, option_type=option_type, strike_target=target)
        output.append(
            {
                **row,
                "cluster_expiration": expiry,
                "expiration_reconstruction_method": expiry_method,
                "option_type": option_type or None,
                "atm_contract": atm,
                "target_contract": target_contract,
            }
        )
    return output


def chunked(values: Sequence[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield list(values[index : index + size])


def bar_cache_path(root: Path, symbol: str, partition: str | None = None) -> Path:
    safe = symbol.replace("/", "_")
    return root / partition / f"{safe}.json" if partition else root / f"{safe}.json"


def fetch_multi_bars(
    *,
    url: str,
    symbols: Sequence[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    headers: Mapping[str, str],
    stock: bool,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    query: dict[str, Any] = {
        "symbols": ",".join(symbols),
        "timeframe": timeframe,
        "start": iso_z(start),
        "end": iso_z(end),
        "limit": 10000,
        "sort": "asc",
    }
    if stock:
        query.update({"feed": "sip", "adjustment": "raw"})
    while True:
        payload = request_json(url, query, headers)
        bars = payload.get("bars") or {}
        if isinstance(bars, dict):
            for symbol, rows in bars.items():
                if isinstance(rows, list):
                    output[str(symbol).upper()].extend(row for row in rows if isinstance(row, dict))
        token = payload.get("next_page_token")
        if not token:
            break
        query["page_token"] = token
    return dict(output)


def load_or_fetch_partitioned_minute_bars(
    requirements: Mapping[str, set[str]],
    *,
    root: Path,
    url: str,
    headers: Mapping[str, str],
    stock: bool,
    workers: int,
    force: bool,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    jobs: list[tuple[str, list[str]]] = []
    for session, symbols in sorted(requirements.items()):
        missing: list[str] = []
        for symbol in sorted(symbols):
            path = bar_cache_path(root, symbol, session)
            cached = read_json(path)
            if not force and isinstance(cached, dict) and cached.get("complete"):
                result[(session, symbol)] = list(cached.get("bars") or [])
            else:
                missing.append(symbol)
        for group in chunked(missing, 75 if stock else 40):
            jobs.append((session, group))

    def run(job: tuple[str, list[str]]) -> tuple[str, list[str], dict[str, list[dict[str, Any]]], str | None]:
        session, symbols = job
        local_day = date.fromisoformat(session)
        start = datetime.combine(local_day, dt_time(9, 30), tzinfo=NY).astimezone(UTC)
        end = datetime.combine(local_day, dt_time(16, 1), tzinfo=NY).astimezone(UTC)
        try:
            rows = fetch_multi_bars(
                url=url,
                symbols=symbols,
                timeframe="1Min",
                start=start,
                end=end,
                headers=headers,
                stock=stock,
            )
            return session, symbols, rows, None
        except Exception as exc:
            return session, symbols, {}, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for session, symbols, rows, error in executor.map(run, jobs):
            for symbol in symbols:
                values = rows.get(symbol, [])
                payload = {
                    "schema_version": 1,
                    "session": session,
                    "symbol": symbol,
                    "timeframe": "1Min",
                    "downloaded_at": datetime.now(UTC).isoformat(),
                    "complete": error is None,
                    "error": error,
                    "bars": values,
                }
                atomic_json(bar_cache_path(root, symbol, session), payload)
                result[(session, symbol)] = values
    return result


def load_or_fetch_daily_bars(
    symbols: Sequence[str],
    *,
    root: Path,
    url: str,
    headers: Mapping[str, str],
    stock: bool,
    start: datetime,
    end: datetime,
    workers: int,
    force: bool,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    end_day = end.date().isoformat()
    refresh_cutoff = pd.Timestamp(end).tz_convert(UTC) - pd.Timedelta(minutes=45)
    for symbol in sorted(set(symbols)):
        path = bar_cache_path(root, symbol)
        cached = read_json(path)
        downloaded_at = utc_timestamp((cached or {}).get("downloaded_at")) if isinstance(cached, dict) else None
        fresh_enough = downloaded_at is not None and downloaded_at >= refresh_cutoff
        if (
            not force
            and isinstance(cached, dict)
            and cached.get("complete")
            and str(cached.get("through") or "") >= end_day
            and fresh_enough
        ):
            result[symbol] = list(cached.get("bars") or [])
        else:
            missing.append(symbol)
    groups = list(chunked(missing, 150 if stock else 75))

    def run(group: list[str]) -> tuple[list[str], dict[str, list[dict[str, Any]]], str | None]:
        try:
            rows = fetch_multi_bars(
                url=url,
                symbols=group,
                timeframe="1Day",
                start=start,
                end=end,
                headers=headers,
                stock=stock,
            )
            return group, rows, None
        except Exception as exc:
            return group, {}, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for group, rows, error in executor.map(run, groups):
            for symbol in group:
                values = rows.get(symbol, [])
                payload = {
                    "schema_version": 1,
                    "symbol": symbol,
                    "timeframe": "1Day",
                    "from": start.date().isoformat(),
                    "through": end_day,
                    "downloaded_at": datetime.now(UTC).isoformat(),
                    "complete": error is None,
                    "error": error,
                    "bars": values,
                }
                atomic_json(bar_cache_path(root, symbol), payload)
                result[symbol] = values
    return result


def bar_time(row: Mapping[str, Any]) -> pd.Timestamp | None:
    return utc_timestamp(row.get("t") or row.get("timestamp"))


def bar_value(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = finite(row.get(key))
        if value is not None:
            return value
    return None


def latest_completed_market_session(
    daily_bars: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    now: datetime,
    fallback: str,
) -> str:
    current_et = now.astimezone(NY)
    today = current_et.date().isoformat()
    current_day_complete = current_et.weekday() >= 5 or (current_et.hour, current_et.minute) >= (16, 10)
    sessions: set[str] = set()
    for rows in daily_bars.values():
        for row in rows or []:
            timestamp = bar_time(row)
            if timestamp is None:
                continue
            session = timestamp.tz_convert(NY).date().isoformat()
            if session < today or (session == today and current_day_complete):
                sessions.add(session)
    return max(sessions) if sessions else fallback


def provider_open_matrix(
    daily_bars: Mapping[str, Sequence[Mapping[str, Any]]],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for symbol, rows in daily_bars.items():
        for row in rows or []:
            timestamp = bar_time(row)
            opening = bar_value(row, "o", "vw", "c")
            if timestamp is None or opening is None or opening <= 0:
                continue
            records.append(
                {
                    "session": pd.Timestamp(timestamp.tz_convert(NY).date()),
                    "ticker": str(symbol).upper(),
                    "open": opening,
                }
            )
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records).drop_duplicates(["session", "ticker"], keep="last")
    return frame.pivot(index="session", columns="ticker", values="open").sort_index()


def option_leg_metrics(
    *,
    symbol: str | None,
    signal_at: pd.Timestamp,
    expiry: str | None,
    minute_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    daily_bars: Mapping[str, Sequence[Mapping[str, Any]]],
    latest_market_session: str,
) -> dict[str, Any]:
    if not symbol or not expiry:
        return {"status": "contract_unavailable"}
    session = signal_at.tz_convert(NY).date().isoformat()
    intraday = []
    for row in minute_bars.get((session, symbol), []) or []:
        timestamp = bar_time(row)
        if timestamp is not None and timestamp >= signal_at:
            intraday.append((timestamp, row))
    if not intraday:
        return {"status": "entry_bar_unavailable", "symbol": symbol}
    intraday.sort(key=lambda item: item[0])
    entry_row = intraday[0][1]
    entry = bar_value(entry_row, "vw", "c", "o")
    if entry is None or entry <= 0:
        return {"status": "entry_price_unavailable", "symbol": symbol}
    cutoff = min(expiry, latest_market_session)
    later_daily: list[Mapping[str, Any]] = []
    for row in daily_bars.get(symbol, []) or []:
        timestamp = bar_time(row)
        if timestamp is None:
            continue
        day = timestamp.tz_convert(NY).date().isoformat()
        if session < day <= cutoff:
            later_daily.append(row)
    final_row: Mapping[str, Any] | None = later_daily[-1] if later_daily else intraday[-1][1]
    final_timestamp = bar_time(final_row) if later_daily else intraday[-1][0]
    final_price = bar_value(final_row, "c", "vw") if final_row else None
    mark_session = final_timestamp.tz_convert(NY).date().isoformat() if final_timestamp is not None else None
    mark_basis = "daily_close" if later_daily else "same_session_intraday"
    highs = [bar_value(row, "h", "c") for _, row in intraday]
    lows = [bar_value(row, "l", "c") for _, row in intraday]
    highs.extend(bar_value(row, "h", "c") for row in later_daily)
    lows.extend(bar_value(row, "l", "c") for row in later_daily)
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    return {
        "status": "matured_at_expiry" if expiry <= latest_market_session else "pending_expiry_marked_to_latest",
        "symbol": symbol,
        "entry_at": intraday[0][0].isoformat(),
        "entry_price": entry,
        "mark_at": final_timestamp.isoformat() if final_timestamp is not None else None,
        "mark_session": mark_session,
        "mark_basis": mark_basis,
        "mark_price": final_price,
        "end_return_pct": (final_price / entry - 1.0) * 100.0 if final_price is not None else None,
        "maximum_return_pct": (max(highs) / entry - 1.0) * 100.0 if highs else None,
        "minimum_return_pct": (min(lows) / entry - 1.0) * 100.0 if lows else None,
        "profitable_at_mark": final_price > entry if final_price is not None else None,
        "minute_bars_after_signal": len(intraday),
        "later_daily_bars": len(later_daily),
    }


def underlying_metrics(
    row: Mapping[str, Any],
    *,
    signal_at: pd.Timestamp,
    expiry: str,
    minute_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    daily_bars: Mapping[str, Sequence[Mapping[str, Any]]],
    latest_market_session: str,
) -> dict[str, Any]:
    ticker = str(row["ticker"])
    session = str(row["market_session"])
    spot = finite(row.get("spot"))
    target = finite(row.get("target"))
    direction = str(row.get("direction"))
    if spot is None or spot <= 0:
        return {"status": "scan_spot_unavailable"}
    intraday = []
    for bar in minute_bars.get((session, ticker), []) or []:
        timestamp = bar_time(bar)
        if timestamp is not None and timestamp >= signal_at:
            intraday.append((timestamp, bar))
    cutoff = min(expiry, latest_market_session)
    later_daily = []
    for bar in daily_bars.get(ticker, []) or []:
        timestamp = bar_time(bar)
        if timestamp is None:
            continue
        day = timestamp.tz_convert(NY).date().isoformat()
        if session < day <= cutoff:
            later_daily.append(bar)
    final_bar: Mapping[str, Any] | None = later_daily[-1] if later_daily else (intraday[-1][1] if intraday else None)
    final_timestamp = bar_time(final_bar) if later_daily else (intraday[-1][0] if intraday else None)
    final_price = bar_value(final_bar, "c", "vw") if final_bar else None
    mark_session = final_timestamp.tz_convert(NY).date().isoformat() if final_timestamp is not None else None
    mark_basis = "daily_close" if later_daily else "same_session_intraday" if intraday else "unavailable"
    highs = [bar_value(bar, "h", "c") for _, bar in intraday]
    lows = [bar_value(bar, "l", "c") for _, bar in intraday]
    highs.extend(bar_value(bar, "h", "c") for bar in later_daily)
    lows.extend(bar_value(bar, "l", "c") for bar in later_daily)
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    favorable = None
    adverse = None
    target_hit = None
    if highs and lows:
        if direction == "BULLISH":
            favorable = (max(highs) / spot - 1.0) * 100.0
            adverse = (min(lows) / spot - 1.0) * 100.0
            target_hit = target is not None and max(highs) >= target
        else:
            favorable = (1.0 - min(lows) / spot) * 100.0
            adverse = (1.0 - max(highs) / spot) * 100.0
            target_hit = target is not None and min(lows) <= target
    raw_return = (final_price / spot - 1.0) * 100.0 if final_price is not None else None
    directional_return = raw_return if direction == "BULLISH" else -raw_return if raw_return is not None else None
    return {
        "status": "matured_at_expiry" if expiry <= latest_market_session else "pending_expiry_marked_to_latest",
        "scan_spot": spot,
        "target": target,
        "mark_at": final_timestamp.isoformat() if final_timestamp is not None else None,
        "mark_session": mark_session,
        "mark_basis": mark_basis,
        "mark_price": final_price,
        "raw_return_pct": raw_return,
        "directional_return_pct": directional_return,
        "direction_correct": directional_return > 0 if directional_return is not None else None,
        "maximum_favorable_move_pct": favorable,
        "maximum_adverse_move_pct": adverse,
        "target_hit_by_mark": target_hit,
        "minute_bars_after_signal": len(intraday),
        "later_daily_bars": len(later_daily),
    }


def spread_metrics(atm: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    if atm.get("status") in {"contract_unavailable", "entry_bar_unavailable", "entry_price_unavailable"}:
        return {"status": "atm_leg_unavailable"}
    if target.get("status") in {"contract_unavailable", "entry_bar_unavailable", "entry_price_unavailable"}:
        return {"status": "target_leg_unavailable"}
    if atm.get("symbol") == target.get("symbol"):
        return {"status": "same_strike_no_spread"}
    atm_entry = finite(atm.get("entry_price"))
    target_entry = finite(target.get("entry_price"))
    atm_mark = finite(atm.get("mark_price"))
    target_mark = finite(target.get("mark_price"))
    if None in {atm_entry, target_entry, atm_mark, target_mark}:
        return {"status": "leg_mark_unavailable"}
    entry_debit = max(float(atm_entry) - float(target_entry), 0.0)
    mark_value = max(float(atm_mark) - float(target_mark), 0.0)
    if entry_debit <= 0:
        return {"status": "nonpositive_entry_debit", "entry_debit": entry_debit, "mark_value": mark_value}
    return {
        "status": "matured_at_expiry" if atm.get("status") == "matured_at_expiry" else "pending_expiry_marked_to_latest",
        "long_symbol": atm.get("symbol"),
        "short_symbol": target.get("symbol"),
        "entry_debit": entry_debit,
        "mark_value": mark_value,
        "end_return_pct": (mark_value / entry_debit - 1.0) * 100.0,
        "profitable_at_mark": mark_value > entry_debit,
    }


def strength_bucket(value: Any) -> str:
    number = finite(value)
    if number is None:
        return "unscored"
    if number < 200:
        return "below_200"
    if number < 250:
        return "200_249"
    if number < 300:
        return "250_299"
    if number < 350:
        return "300_349"
    return "350_plus"


def rank_bucket(value: Any) -> str:
    number = finite(value)
    if number is None:
        return "unranked"
    if number <= 5:
        return "rank_1_5"
    if number <= 10:
        return "rank_6_10"
    if number <= 20:
        return "rank_11_20"
    return "rank_21_plus"


def directional_target_distance_pct(row: Mapping[str, Any]) -> float | None:
    """Return target distance in the signaled direction as a positive percentage."""
    spot = finite(row.get("spot"))
    target = finite(row.get("target"))
    direction = str(row.get("direction") or "").upper()
    if spot is None or target is None or spot <= 0:
        return None
    if direction == "BULLISH":
        return (target / spot - 1.0) * 100.0
    if direction == "BEARISH":
        return (1.0 - target / spot) * 100.0
    return None


def target_distance_bucket(value: Any) -> str:
    number = finite(value)
    if number is None:
        return "missing"
    if number <= 0:
        return "nonpositive"
    if number < 2:
        return "under_2_pct"
    if number < 5:
        return "2_to_5_pct"
    if number <= 10:
        return "5_to_10_pct"
    return "over_10_pct"


def signal_time_bucket(value: Any) -> str:
    timestamp = utc_timestamp(value)
    if timestamp is None:
        return "unknown"
    local = timestamp.tz_convert(NY)
    minutes = int(local.hour) * 60 + int(local.minute)
    if 9 * 60 + 30 <= minutes < 10 * 60 + 30:
        return "0930_1029_et"
    if 10 * 60 + 30 <= minutes < 12 * 60:
        return "1030_1159_et"
    if 12 * 60 <= minutes < 14 * 60:
        return "1200_1359_et"
    if 14 * 60 <= minutes < 15 * 60 + 30:
        return "1400_1529_et"
    if 15 * 60 + 30 <= minutes <= 16 * 60:
        return "1530_1600_et"
    return "outside_regular_hours"


def summarize_numeric(
    rows: Sequence[Mapping[str, Any]],
    group_fields: Sequence[str],
    value_fields: Sequence[str],
    *,
    status_field: str | None = None,
    status_value: str | None = None,
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if status_field and row.get(status_field) != status_value:
            continue
        groups[tuple(row.get(field) for field in group_fields)].append(row)
    output: list[dict[str, Any]] = []
    for key, group in groups.items():
        record = {field: value for field, value in zip(group_fields, key)}
        record["observations"] = len(group)
        for field in value_fields:
            values = [finite(row.get(field)) for row in group]
            clean = [value for value in values if value is not None]
            record[f"{field}_available"] = len(clean)
            record[f"average_{field}"] = mean(clean) if clean else None
            record[f"median_{field}"] = median(clean) if clean else None
            record[f"positive_{field}_fraction"] = sum(value > 0 for value in clean) / len(clean) if clean else None
        output.append(record)
    return sorted(output, key=lambda row: tuple(str(row.get(field)) for field in group_fields))


def cluster_population_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    value_fields = (
        "underlying_directional_return_pct",
        "underlying_maximum_favorable_move_pct",
        "underlying_maximum_adverse_move_pct",
        "atm_option_end_return_pct",
        "atm_option_maximum_return_pct",
        "target_option_end_return_pct",
        "target_option_maximum_return_pct",
        "debit_spread_end_return_pct",
    )
    metrics: dict[str, Any] = {"observations": len(rows)}
    for field in value_fields:
        values = [finite(row.get(field)) for row in rows]
        clean = [value for value in values if value is not None]
        metrics[field] = {
            "available": len(clean),
            "average_pct": mean(clean) if clean else None,
            "median_pct": median(clean) if clean else None,
            "positive_fraction": sum(value > 0 for value in clean) / len(clean) if clean else None,
        }
    target_hits = [row.get("target_hit_by_expiry") for row in rows if row.get("target_hit_by_expiry") is not None]
    metrics["target_hit"] = {
        "available": len(target_hits),
        "fraction": sum(bool(value) for value in target_hits) / len(target_hits) if target_hits else None,
    }
    metrics["option_coverage"] = {
        "atm_entry_available": sum(finite(row.get("atm_option_end_return_pct")) is not None for row in rows),
        "target_entry_available": sum(finite(row.get("target_option_end_return_pct")) is not None for row in rows),
        "debit_spread_available": sum(finite(row.get("debit_spread_end_return_pct")) is not None for row in rows),
        "atm_status_counts": dict(Counter(str(row.get("atm_option_status")) for row in rows)),
        "target_status_counts": dict(Counter(str(row.get("target_option_status")) for row in rows)),
        "spread_status_counts": dict(Counter(str(row.get("debit_spread_status")) for row in rows)),
    }
    return metrics


def option_path_diagnostics(rows: Sequence[Mapping[str, Any]], prefix: str) -> dict[str, Any]:
    """Quantify how often an option's favorable peak was surrendered by the mark."""
    peak_field = f"{prefix}_maximum_return_pct"
    end_field = f"{prefix}_end_return_pct"
    pairs = [
        (finite(row.get(peak_field)), finite(row.get(end_field)))
        for row in rows
    ]
    scored = [(peak, end) for peak, end in pairs if peak is not None and end is not None]
    payload: dict[str, Any] = {
        "available": len(scored),
        "positive_peak_fraction": (
            sum(peak > 0 for peak, _ in scored) / len(scored) if scored else None
        ),
        "positive_end_fraction": (
            sum(end > 0 for _, end in scored) / len(scored) if scored else None
        ),
        "thresholds": {},
    }
    for threshold in (25.0, 50.0, 100.0):
        reached = [(peak, end) for peak, end in scored if peak >= threshold]
        gave_back = [(peak, end) for peak, end in reached if end <= 0]
        payload["thresholds"][str(int(threshold))] = {
            "reached_count": len(reached),
            "gave_back_to_nonpositive_count": len(gave_back),
            "gave_back_to_nonpositive_fraction": (
                len(gave_back) / len(reached) if reached else None
            ),
        }
    return payload


def cluster_candidate_hypotheses(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Persist descriptive filters without converting post-hoc findings into validation."""
    definitions = (
        (
            "bullish_rank_1_10",
            "post_hoc_descriptive_frozen_after_2026_08_05",
            lambda row: str(row.get("direction")) == "BULLISH" and (finite(row.get("rank")) or math.inf) <= 10,
        ),
        (
            "bullish_rank_1_10_strength_200_299",
            "post_hoc_descriptive_frozen_after_2026_08_05",
            lambda row: (
                str(row.get("direction")) == "BULLISH"
                and (finite(row.get("rank")) or math.inf) <= 10
                and 200 <= (finite(row.get("strength")) or -math.inf) < 300
            ),
        ),
        (
            "bullish_rank_1_10_target_2_10_pct",
            "post_hoc_descriptive_frozen_after_2026_08_05",
            lambda row: (
                str(row.get("direction")) == "BULLISH"
                and (finite(row.get("rank")) or math.inf) <= 10
                and 2 <= (finite(row.get("target_distance_pct")) or -math.inf) <= 10
            ),
        ),
        (
            "bullish_rank_1_10_strength_200_299_target_2_10_pct",
            "post_hoc_descriptive_frozen_after_2026_08_05",
            lambda row: (
                str(row.get("direction")) == "BULLISH"
                and (finite(row.get("rank")) or math.inf) <= 10
                and 200 <= (finite(row.get("strength")) or -math.inf) < 300
                and 2 <= (finite(row.get("target_distance_pct")) or -math.inf) <= 10
            ),
        ),
        (
            "bullish_cross_source_confirmed",
            "predefined_structural_confirmation",
            lambda row: (
                str(row.get("direction")) == "BULLISH"
                and str(row.get("agreement_status")) == "all_agree_bullish"
            ),
        ),
    )
    output: list[dict[str, Any]] = []
    for name, origin, predicate in definitions:
        cohort = [row for row in rows if predicate(row)]
        output.append(
            {
                "hypothesis": name,
                "origin": origin,
                "observations": len(cohort),
                "market_sessions": sorted({str(row.get("market_session")) for row in cohort}),
                "unique_tickers": len({str(row.get("ticker")) for row in cohort}),
                "status_counts": dict(Counter(str(row.get("status")) for row in cohort)),
                "expiration_counts": dict(Counter(str(row.get("cluster_expiration")) for row in cohort)),
                "metrics": cluster_population_metrics(cohort),
            }
        )
    return output


def episode_summary(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, Any] = {}
    for source in SCAN_TYPES:
        rows = [row for row in episodes if row.get("scan_type") == source]
        eligible = [row for row in rows if eligible_episode(row)]
        by_source[source] = {
            "episodes": len(rows),
            "eligible_regular_session_episodes": len(eligible),
            "unique_tickers": len({row.get("ticker") for row in rows if row.get("ticker")}),
            "market_sessions": len({row.get("market_session") for row in rows}),
            "first_received_at": min((row.get("first_seen_at") for row in rows), default=None),
            "last_received_at": max((row.get("first_seen_at") for row in rows), default=None),
            "directions": dict(Counter(str(row.get("direction")) for row in rows)),
            "setup_families": dict(Counter(str(row.get("setup_family")) for row in rows)),
        }
    return {"total": len(episodes), "by_source": by_source}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--force-provider-refresh", action="store_true")
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()

    created_at = datetime.now(UTC)
    episodes = load_signal_episodes(CAPTURE_ROOT)
    states = daily_latest_states(episodes)
    contexts = agreement_context(states)
    dataset = latest_dataset()
    canonical_latest_session = str(dataset.get("latest_session") or "")
    if not canonical_latest_session:
        raise RuntimeError("canonical dataset has no latest market session")

    cluster_states = [dict(row) for row in states if row.get("scan_type") == "cluster"]
    first_session = min(str(row["market_session"]) for row in cluster_states)
    contract_end = (date.fromisoformat(max(str(row["market_session"]) for row in cluster_states)) + timedelta(days=75)).isoformat()
    contracts_by_ticker, contract_diagnostics = fetch_contract_universe(
        cluster_states,
        start=first_session,
        end=contract_end,
        workers=args.workers,
        force=args.force_provider_refresh,
    )
    enriched = enrich_cluster_contracts(cluster_states, contracts_by_ticker)

    if args.metadata_only:
        payload = {
            "status": "metadata_complete",
            "created_at": created_at.isoformat(),
            "cluster_states": len(enriched),
            "expiration_reconstructed": sum(bool(row.get("cluster_expiration")) for row in enriched),
            "atm_contracts": sum(bool(row.get("atm_contract")) for row in enriched),
            "target_contracts": sum(bool(row.get("target_contract")) for row in enriched),
            "contract_diagnostics": contract_diagnostics,
            "execution_authority": False,
        }
        atomic_json(OUTPUT_ROOT / "latest_cluster_contract_reconstruction.json", payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    option_symbols: set[str] = set()
    option_minute_requirements: dict[str, set[str]] = defaultdict(set)
    stock_minute_requirements: dict[str, set[str]] = defaultdict(set)
    for row in enriched:
        session = str(row["market_session"])
        stock_minute_requirements[session].add(str(row["ticker"]))
        for key in ("atm_contract", "target_contract"):
            contract = row.get(key) or {}
            symbol = str(contract.get("symbol") or "")
            if symbol:
                option_symbols.add(symbol)
                option_minute_requirements[session].add(symbol)

    headers = provider_headers()
    option_minute = load_or_fetch_partitioned_minute_bars(
        option_minute_requirements,
        root=OPTION_MINUTE_CACHE,
        url=OPTION_BARS_URL,
        headers=headers,
        stock=False,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    stock_minute = load_or_fetch_partitioned_minute_bars(
        stock_minute_requirements,
        root=STOCK_MINUTE_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    bars_start = datetime.combine(date.fromisoformat(first_session), dt_time(0, 0), tzinfo=UTC)
    bars_end = created_at
    option_daily = load_or_fetch_daily_bars(
        sorted(option_symbols),
        root=OPTION_DAILY_CACHE,
        url=OPTION_BARS_URL,
        headers=headers,
        stock=False,
        start=bars_start,
        end=bars_end,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    stock_daily_symbols = sorted({str(row["ticker"]) for row in states} | {"SPY"})
    stock_daily = load_or_fetch_daily_bars(
        stock_daily_symbols,
        root=STOCK_DAILY_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        start=bars_start,
        end=bars_end,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    latest_market_session = latest_completed_market_session(
        stock_daily,
        now=created_at,
        fallback=canonical_latest_session,
    )
    provider_opens = provider_open_matrix(stock_daily)
    fixed_horizon = score_states(
        [row for row in states if row.get("scan_type") != "cluster"],
        provider_opens,
    )

    cluster_observations: list[dict[str, Any]] = []
    for row in enriched:
        signal_at = utc_timestamp(row.get("first_seen_at"))
        expiry = str(row.get("cluster_expiration") or "")
        base = {
            key: row.get(key)
            for key in (
                "signal_id",
                "market_session",
                "first_seen_at",
                "ticker",
                "direction",
                "rank",
                "strength",
                "spot",
                "target",
                "cluster_expiration",
                "expiration_reconstruction_method",
                "option_type",
                "atm_contract",
                "target_contract",
            )
        }
        base["strength_bucket"] = strength_bucket(row.get("strength"))
        base["rank_bucket"] = rank_bucket(row.get("rank"))
        base["target_distance_pct"] = directional_target_distance_pct(base)
        base["target_distance_bucket"] = target_distance_bucket(base["target_distance_pct"])
        base["signal_time_bucket"] = signal_time_bucket(base.get("first_seen_at"))
        base.update(contexts.get((str(row["market_session"]), str(row["ticker"]))) or {})
        if signal_at is None or not expiry:
            cluster_observations.append({**base, "status": "unscorable_missing_signal_time_or_expiration"})
            continue
        atm_contract = row.get("atm_contract") or {}
        target_contract = row.get("target_contract") or {}
        atm = option_leg_metrics(
            symbol=atm_contract.get("symbol"),
            signal_at=signal_at,
            expiry=expiry,
            minute_bars=option_minute,
            daily_bars=option_daily,
            latest_market_session=latest_market_session,
        )
        target_leg = option_leg_metrics(
            symbol=target_contract.get("symbol"),
            signal_at=signal_at,
            expiry=expiry,
            minute_bars=option_minute,
            daily_bars=option_daily,
            latest_market_session=latest_market_session,
        )
        underlying = underlying_metrics(
            row,
            signal_at=signal_at,
            expiry=expiry,
            minute_bars=stock_minute,
            daily_bars=stock_daily,
            latest_market_session=latest_market_session,
        )
        spread = spread_metrics(atm, target_leg)
        status = "matured_at_expiry" if expiry <= latest_market_session else "pending_expiry_marked_to_latest"
        cluster_observations.append(
            {
                **base,
                "status": status,
                "underlying": underlying,
                "atm_option": atm,
                "target_option": target_leg,
                "debit_spread": spread,
            }
        )

    flat_cluster = []
    for row in cluster_observations:
        flat_cluster.append(
            {
                **{key: value for key, value in row.items() if key not in {"underlying", "atm_option", "target_option", "debit_spread"}},
                "underlying_directional_return_pct": (row.get("underlying") or {}).get("directional_return_pct"),
                "underlying_maximum_favorable_move_pct": (row.get("underlying") or {}).get("maximum_favorable_move_pct"),
                "underlying_maximum_adverse_move_pct": (row.get("underlying") or {}).get("maximum_adverse_move_pct"),
                "target_hit_by_expiry": (row.get("underlying") or {}).get("target_hit_by_mark"),
                "atm_option_status": (row.get("atm_option") or {}).get("status"),
                "target_option_status": (row.get("target_option") or {}).get("status"),
                "debit_spread_status": (row.get("debit_spread") or {}).get("status"),
                "atm_option_end_return_pct": (row.get("atm_option") or {}).get("end_return_pct"),
                "atm_option_maximum_return_pct": (row.get("atm_option") or {}).get("maximum_return_pct"),
                "target_option_end_return_pct": (row.get("target_option") or {}).get("end_return_pct"),
                "target_option_maximum_return_pct": (row.get("target_option") or {}).get("maximum_return_pct"),
                "debit_spread_end_return_pct": (row.get("debit_spread") or {}).get("end_return_pct"),
            }
        )

    complete_states = []
    for row in states:
        context = contexts.get((str(row["market_session"]), str(row["ticker"]))) or {}
        complete_states.append({**row, **context})

    matured_cluster = [row for row in flat_cluster if row.get("status") == "matured_at_expiry"]
    pending_cluster = [row for row in flat_cluster if row.get("status") == "pending_expiry_marked_to_latest"]
    completed_session_cluster = [
        row for row in flat_cluster if str(row.get("market_session") or "") <= latest_market_session
    ]
    current_partial_cluster = [
        row for row in flat_cluster if str(row.get("market_session") or "") > latest_market_session
    ]
    cluster_value_fields = (
        "underlying_directional_return_pct",
        "underlying_maximum_favorable_move_pct",
        "underlying_maximum_adverse_move_pct",
        "atm_option_end_return_pct",
        "atm_option_maximum_return_pct",
        "target_option_end_return_pct",
        "target_option_maximum_return_pct",
        "debit_spread_end_return_pct",
    )
    matured_fixed_horizon = [row for row in fixed_horizon if row.get("status") == "matured"]
    fixed_horizon_summary = {
        "records": len(fixed_horizon),
        "matured": len(matured_fixed_horizon),
        "pending": sum(row.get("status") == "pending_future_opens" for row in fixed_horizon),
        "unscorable": sum(str(row.get("status", "")).startswith("unscorable") for row in fixed_horizon),
        "status_counts": dict(Counter(str(row.get("status")) for row in fixed_horizon)),
        "by_source_direction_horizon": summarize_numeric(
            matured_fixed_horizon,
            ("source", "direction", "horizon_sessions"),
            ("directional_return_pct", "directional_excess_vs_spy_pct"),
        ),
        "by_source_setup_horizon": summarize_numeric(
            matured_fixed_horizon,
            ("source", "setup_family", "horizon_sessions"),
            ("directional_return_pct", "directional_excess_vs_spy_pct"),
        ),
        "by_ticker_source_horizon": summarize_numeric(
            matured_fixed_horizon,
            ("ticker", "source", "horizon_sessions"),
            ("directional_return_pct", "directional_excess_vs_spy_pct"),
        ),
    }

    cluster_summary = {
        "states": len(cluster_observations),
        "expiration_reconstructed": sum(bool(row.get("cluster_expiration")) for row in cluster_observations),
        "matured_at_expiry": len(matured_cluster),
        "pending_expiry": sum(row.get("status") == "pending_expiry_marked_to_latest" for row in cluster_observations),
        "unscorable": sum(str(row.get("status", "")).startswith("unscorable") for row in cluster_observations),
        "latest_completed_market_session": latest_market_session,
        "finalized_at_expiry": cluster_population_metrics(matured_cluster),
        "pending_mark_to_latest": cluster_population_metrics(pending_cluster),
        "all_final_and_pending": cluster_population_metrics(flat_cluster),
        "completed_sessions_only": cluster_population_metrics(completed_session_cluster),
        "current_partial_sessions": cluster_population_metrics(current_partial_cluster),
        "underlying_direction_correct_fraction": (
            sum(finite(row.get("underlying_directional_return_pct")) > 0 for row in matured_cluster if finite(row.get("underlying_directional_return_pct")) is not None)
            / sum(finite(row.get("underlying_directional_return_pct")) is not None for row in matured_cluster)
            if any(finite(row.get("underlying_directional_return_pct")) is not None for row in matured_cluster)
            else None
        ),
        "target_hit_fraction": (
            sum(bool(row.get("target_hit_by_expiry")) for row in matured_cluster if row.get("target_hit_by_expiry") is not None)
            / sum(row.get("target_hit_by_expiry") is not None for row in matured_cluster)
            if any(row.get("target_hit_by_expiry") is not None for row in matured_cluster)
            else None
        ),
        "by_market_session": summarize_numeric(matured_cluster, ("market_session",), cluster_value_fields),
        "by_expiration": summarize_numeric(matured_cluster, ("cluster_expiration",), cluster_value_fields),
        "by_direction": summarize_numeric(matured_cluster, ("direction",), cluster_value_fields),
        "by_rank_bucket": summarize_numeric(matured_cluster, ("rank_bucket",), cluster_value_fields),
        "by_strength_bucket": summarize_numeric(matured_cluster, ("strength_bucket",), cluster_value_fields),
        "by_target_distance_bucket": summarize_numeric(matured_cluster, ("target_distance_bucket",), cluster_value_fields),
        "by_signal_time_bucket": summarize_numeric(matured_cluster, ("signal_time_bucket",), cluster_value_fields),
        "by_ticker": summarize_numeric(matured_cluster, ("ticker",), cluster_value_fields),
        "by_cross_source_state": summarize_numeric(matured_cluster, ("agreement_status",), cluster_value_fields),
        "completed_sessions_by_target_distance_bucket": summarize_numeric(completed_session_cluster, ("direction", "target_distance_bucket"), cluster_value_fields),
        "completed_sessions_by_signal_time_bucket": summarize_numeric(completed_session_cluster, ("direction", "signal_time_bucket"), cluster_value_fields),
        "current_partial_by_target_distance_bucket": summarize_numeric(current_partial_cluster, ("direction", "target_distance_bucket"), cluster_value_fields),
        "current_partial_by_signal_time_bucket": summarize_numeric(current_partial_cluster, ("direction", "signal_time_bucket"), cluster_value_fields),
        "option_path_diagnostics_completed_sessions": {
            "atm_option": option_path_diagnostics(completed_session_cluster, "atm_option"),
            "target_option": option_path_diagnostics(completed_session_cluster, "target_option"),
        },
        "option_path_diagnostics_current_partial": {
            "atm_option": option_path_diagnostics(current_partial_cluster, "atm_option"),
            "target_option": option_path_diagnostics(current_partial_cluster, "target_option"),
        },
        "candidate_hypotheses_completed_sessions": cluster_candidate_hypotheses(completed_session_cluster),
        "candidate_hypotheses_current_partial": cluster_candidate_hypotheses(current_partial_cluster),
        "pending_by_market_session": summarize_numeric(pending_cluster, ("market_session",), cluster_value_fields),
        "pending_by_expiration": summarize_numeric(pending_cluster, ("cluster_expiration",), cluster_value_fields),
        "pending_by_direction": summarize_numeric(pending_cluster, ("direction",), cluster_value_fields),
        "pending_by_rank_bucket": summarize_numeric(pending_cluster, ("rank_bucket",), cluster_value_fields),
        "pending_by_strength_bucket": summarize_numeric(pending_cluster, ("strength_bucket",), cluster_value_fields),
        "pending_by_target_distance_bucket": summarize_numeric(pending_cluster, ("target_distance_bucket",), cluster_value_fields),
        "pending_by_signal_time_bucket": summarize_numeric(pending_cluster, ("signal_time_bucket",), cluster_value_fields),
        "pending_by_ticker": summarize_numeric(pending_cluster, ("ticker",), cluster_value_fields),
        "pending_by_cross_source_state": summarize_numeric(pending_cluster, ("agreement_status",), cluster_value_fields),
    }

    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "mode": "complete_flash_agentic_cluster_observations",
        "active_sources": list(SCAN_TYPES),
        "dataset": dataset,
        "capture_manifest": signal_file_manifest(CAPTURE_ROOT),
        "capture_inventory": capture_inventory(episodes),
        "population_counts": {
            "all_unique_episodes": len(episodes),
            "eligible_regular_session_episodes": sum(eligible_episode(row) for row in episodes),
            "all_daily_terminal_source_ticker_states": len(states),
            "flash_agentic_fixed_horizon_records": len(fixed_horizon),
            "cluster_expiry_records": len(cluster_observations),
        },
        "episode_summary": episode_summary(episodes),
        "fixed_horizon_flash_agentic": {
            "definition": "latest eligible source/ticker/session state; next-session-open entry; 1/5/21 session exits",
            "latest_completed_market_session": latest_market_session,
            "summary": fixed_horizon_summary,
            "records": fixed_horizon,
        },
        "cluster_expiry_research": {
            "primary_horizon": "scanner_second_listed_option_expiration",
            "entry_rule": "first one-minute option bar at or after signal timestamp",
            "underlying_entry": "captured scan spot",
            "contract_selection": {
                "directional_type": "call for bullish, put for bearish",
                "atm_leg": "listed strike nearest captured spot",
                "target_leg": "listed strike nearest cluster target",
                "debit_spread": "long ATM directional option, short target-strike directional option",
            },
            "summary": cluster_summary,
            "contract_download_diagnostics": contract_diagnostics,
            "records": cluster_observations,
        },
        "all_episode_observations": episodes,
        "all_daily_terminal_states": complete_states,
        "research_limits": {
            "cluster_source_cards_omit_expiration": True,
            "expiration_reconstructed_from_provider_contract_calendar": True,
            "scanner_contract": "cluster scanner skips nearest column and uses the second listed expiration",
            "historical_option_bid_ask_unavailable": True,
            "entry_uses_first_traded_one_minute_bar": True,
            "illiquid_contracts_without_a_post_signal_bar_are_unscorable": True,
            "pending_expirations_are_marked_to_latest_available_session": True,
            "results_descriptive_not_confirmatory": True,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload["report_id"] = stable_id(
        "cipher_complete_observations",
        {
            "dataset_checksum": dataset.get("checksum"),
            "capture_manifest": payload["capture_manifest"],
            "population_counts": payload["population_counts"],
            "cluster_primary_horizon": payload["cluster_expiry_research"]["primary_horizon"],
        },
        length=64,
    )
    atomic_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "population_counts": payload["population_counts"],
                "cluster_summary": {
                    key: cluster_summary.get(key)
                    for key in (
                        "states",
                        "expiration_reconstructed",
                        "matured_at_expiry",
                        "pending_expiry",
                        "unscorable",
                        "underlying_direction_correct_fraction",
                        "target_hit_fraction",
                    )
                },
                "output": str(OUTPUT),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
