"""Read-only local API for Strike Matrix, Night Vision, and Spyglass.

Exposes no order endpoints and never returns credentials.
GEX is a public-OI heuristic: Gamma × OI × 100 × spot² × 0.01.
It is not verified dealer positioning.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import math as _math
import sys

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
from scanner import SCAN_UNIVERSE, UNIVERSE_META, get_scan_job, run_scan, start_scan_job
import weight_lab
import cluster_backtest
import ranking_lab
import strategy_backtest
import historical_backtest
import price_backtest
import edge_backtest
import intraday_backtest
from exposure import (
    number, parse_contract, gex, vex, model_gamma, model_vanna,
    profile_summary, classify_aggressor, premium_tier,
    _depth_is_full_chain, _depth_to_points,
    _clamp_expiration_count, _matrix_chain_pages, _matrix_oi_horizon_days,
    MAX_MATRIX_EXPIRATIONS, DEFAULT_MATRIX_EXPIRATIONS,
)

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "app" / ".env"
DATA = "https://data.alpaca.markets"
CONTRACTS = "https://paper-api.alpaca.markets/v2/options/contracts"

OI_CACHE: dict = {}
CHAIN_CACHE: dict = {}
MATRIX_CACHE: dict = {}
BARS_CACHE: dict = {}
FLOW_CACHE: dict = {}
QUOTE_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def governance_status():
    """Read governance state without mutating or migrating the registry."""

    registry_path = ROOT / "data" / "governance" / "research_registry.sqlite"
    if not registry_path.exists():
        return {
            "initialized": False,
            "registry_path": str(registry_path),
            "read_only": True,
            "live_execution_present": False,
            "message": "Run scripts/run_research_platform.py init to initialize governance.",
            "as_of": utcnow(),
        }
    uri = f"file:{registry_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        count_tables = (
            "raw_objects",
            "datasets",
            "features",
            "feature_snapshots",
            "strategies",
            "experiments",
            "promotion_events",
            "audit_events",
            "prospective_tests",
            "anomaly_events",
            "evidence_reconciliations",
        )
        counts = {
            table: int(db.execute(f"select count(*) from {table}").fetchone()[0])
            for table in count_tables
            if table in tables
        }
        strategies = (
            [
                dict(row)
                for row in db.execute(
                    "select strategy_id, name, version, current_state from strategies order by name, version"
                ).fetchall()
            ]
            if "strategies" in tables
            else []
        )
        features = (
            [
                dict(row)
                for row in db.execute(
                    "select feature_id, name, version, allowed_use from features order by name, version"
                ).fetchall()
            ]
            if "features" in tables
            else []
        )
    return {
        "initialized": True,
        "registry_path": str(registry_path),
        "counts": counts,
        "strategies": strategies,
        "features": features,
        "read_only": True,
        "live_execution_present": False,
        "maximum_promotion_state": "LIVE_REVIEW_REQUIRED",
        "as_of": utcnow(),
    }


def local_settings():
    values = {}
    if ENV.is_file():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (
        "ALPACA_ALGO_KEY",
        "ALPACA_ALGO_SECRET",
        "ALPACA_ALGO_PLUS_KEY",
        "ALPACA_ALGO_PLUS_SECRET",
        "ALPACA_API_KEY",
        "ALPACA_API_SECRET",
        "ALPACA_DATA_FEED",
        "ALPACA_STOCK_FEED",
    ):
        if os.environ.get(key):
            values[key] = os.environ[key]
    key = values.get("ALPACA_ALGO_KEY") or values.get("ALPACA_ALGO_PLUS_KEY") or values.get("ALPACA_API_KEY")
    secret = (
        values.get("ALPACA_ALGO_SECRET")
        or values.get("ALPACA_ALGO_PLUS_SECRET")
        or values.get("ALPACA_API_SECRET")
    )
    if not key or not secret:
        raise ValueError("Alpaca market-data credentials are not configured locally.")
    options_feed = values.get("ALPACA_DATA_FEED", "opra").lower()
    stock_feed = values.get("ALPACA_STOCK_FEED", "sip").lower()
    return key, secret, options_feed, stock_feed


def alpaca(path, query=None, base=DATA):
    key, secret, _, _ = local_settings()
    url = base + path
    if query:
        url += "?" + urlencode(query)
    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = (
            "the selected feed may require an eligible subscription"
            if exc.code == 403
            else "check symbol and configured market-data access"
        )
        raise ValueError(f"Alpaca market-data request failed (HTTP {exc.code}); {detail}.") from exc
    except URLError as exc:
        raise ValueError("Unable to reach Alpaca market data. Check the network and retry.") from exc


def resolve_options_feed(requested: str | None) -> str:
    preferred = (requested or local_settings()[2] or "opra").lower()
    if preferred not in {"opra", "indicative", "auto"}:
        preferred = "opra"
    if preferred == "auto":
        preferred = local_settings()[2] if local_settings()[2] in {"opra", "indicative"} else "opra"
    return preferred


def option_open_interest(ticker, expiration_gte=None, expiration_lte=None):
    ticker = ticker.upper()
    cache_key = (ticker, expiration_gte, expiration_lte)
    with _CACHE_LOCK:
        cached = OI_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
    key, secret, _, _ = local_settings()
    query = {"underlying_symbols": ticker, "limit": 10000}
    if expiration_gte:
        query["expiration_date_gte"] = expiration_gte
    if expiration_lte:
        query["expiration_date_lte"] = expiration_lte
    merged = {}
    for _ in range(6):
        url = CONTRACTS + "?" + urlencode(query)
        request = Request(
            url,
            headers={
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ValueError(f"Alpaca option-contract metadata failed (HTTP {exc.code}).") from exc
        except URLError as exc:
            raise ValueError("Unable to reach Alpaca option-contract metadata. Check the network and retry.") from exc
        for item in raw.get("option_contracts") or raw.get("contracts") or []:
            symbol = str(item.get("symbol") or "").upper()
            if symbol:
                merged[symbol] = {
                    "open_interest": number(item.get("open_interest")),
                    "open_interest_date": item.get("open_interest_date"),
                }
        token = raw.get("next_page_token")
        if not token:
            break
        query = {"underlying_symbols": ticker, "limit": 10000, "page_token": token}
        if expiration_gte:
            query["expiration_date_gte"] = expiration_gte
        if expiration_lte:
            query["expiration_date_lte"] = expiration_lte
    with _CACHE_LOCK:
        OI_CACHE[cache_key] = (time.time(), merged)
    return merged


def _stock_quote(ticker, feed):
    raw = alpaca(f"/v2/stocks/{ticker.upper()}/quotes/latest", {"feed": feed})
    q = raw.get("quote", raw)
    bid, ask = number(q.get("bp", q.get("bid_price"))), number(q.get("ap", q.get("ask_price")))
    mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else None
    spread = (ask - bid) if bid is not None and ask is not None else None
    # Reject absurd after-hours crossed/wide quotes as unusable mid context.
    usable_mid = mid
    if mid is not None and spread is not None and mid > 0 and spread / mid > 0.02:
        usable_mid = None
    trade = alpaca(f"/v2/stocks/{ticker.upper()}/trades/latest", {"feed": feed})
    t = trade.get("trade", trade)
    last = number(t.get("p", t.get("price")))
    return {
        "ticker": ticker.upper(),
        "bid": bid,
        "ask": ask,
        "mid": usable_mid,
        "last": last,
        "price_context": usable_mid if usable_mid is not None else last,
        "price_context_kind": "mid" if usable_mid is not None else "latest_trade",
        "as_of": q.get("t", q.get("timestamp", t.get("t", t.get("timestamp", utcnow())))),
        "feed": feed,
        "day_change_pct": None,
    }


def quote(ticker):
    ticker = ticker.upper()
    with _CACHE_LOCK:
        cached = QUOTE_CACHE.get(ticker)
        if cached and time.time() - cached[0] < 3:
            return deepcopy(cached[1])
    _, _, _, preferred_stock = local_settings()
    feeds = []
    for feed in (preferred_stock, "sip", "iex"):
        if feed not in feeds:
            feeds.append(feed)
    last_error = None
    result = None
    for feed in feeds:
        try:
            candidate = _stock_quote(ticker, feed)
            if candidate["price_context"] is not None:
                result = candidate
                break
            result = candidate
        except ValueError as exc:
            last_error = exc
    if result is None:
        raise last_error or ValueError("Unable to load underlying quote.")
    # Attach prior close / day change from recent daily bars only.  Use the
    # same wide, newest-tail daily-bar retrieval as /api/bars; a tight
    # date-only start/end can return stale older bars from Alpaca and invert
    # the daily percent shown in the top quote pill.
    try:
        daily = bars(ticker, "1d", limit=6).get("bars") or []
        closes = [bar for bar in daily if number(bar.get("close")) is not None]
        if closes and result["price_context"] is not None:
            today_utc = datetime.now(timezone.utc).date().isoformat()
            latest_time = str(closes[-1].get("time") or "")
            if len(closes) >= 2 and latest_time.startswith(today_utc):
                prev = number(closes[-2].get("close"))
            else:
                prev = number(closes[-1].get("close"))
            if prev:
                result["prior_close"] = prev
                result["day_change_pct"] = (result["price_context"] - prev) / prev * 100.0
    except Exception:
        pass
    with _CACHE_LOCK:
        QUOTE_CACHE[ticker] = (time.time(), result)
    return deepcopy(result)


def option_chain(ticker, feed, force=False, max_pages=8, expiration_gte=None, expiration_lte=None):
    feed = resolve_options_feed(feed)
    if feed not in {"opra", "indicative"}:
        raise ValueError("feed must be 'opra' or 'indicative'.")
    cache_key = (ticker.upper(), feed, int(max_pages), expiration_gte, expiration_lte)
    with _CACHE_LOCK:
        cached = CHAIN_CACHE.get(cache_key)
        if not force and cached and time.time() - cached[0] < 15:
            return deepcopy(cached[1])

    def pull(active_feed):
        pages, query = [], {"feed": active_feed, "limit": 1000}
        for _ in range(max(1, int(max_pages))):
            raw = alpaca(f"/v1beta1/options/snapshots/{ticker.upper()}", query)
            pages.append(raw)
            token = raw.get("next_page_token")
            if not token:
                break
            query = {"feed": active_feed, "limit": 1000, "page_token": token}
        return pages

    try:
        pages = pull(feed)
        used_feed = feed
    except ValueError:
        if feed == "opra":
            pages = pull("indicative")
            used_feed = "indicative"
        else:
            raise

    contracts = []
    open_interest = option_open_interest(ticker, expiration_gte, expiration_lte)
    for raw in pages:
        for symbol, item in (raw.get("snapshots") or {}).items():
            meta = parse_contract(symbol)
            if meta["strike"] is None:
                continue
            q = item.get("latestQuote") or {}
            trade = item.get("latestTrade") or {}
            greeks = item.get("greeks") or {}
            day = item.get("dailyBar") or {}
            bid = number(q.get("bp", q.get("bid_price")))
            ask = number(q.get("ap", q.get("ask_price")))
            oi_meta = open_interest.get(meta["symbol"], {})
            meta.update(
                {
                    "bid": bid,
                    "ask": ask,
                    "mid": (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else None,
                    "last": number(trade.get("p", trade.get("price"))),
                    "size": int(number(trade.get("s", trade.get("size"))) or 0),
                    "trade_time": trade.get("t", trade.get("timestamp")),
                    "exchange": trade.get("x", trade.get("exchange")),
                    "volume": number(day.get("v", day.get("volume", item.get("volume")))),
                    "open_interest": number(
                        item.get("openInterest", item.get("open_interest", oi_meta.get("open_interest")))
                    ),
                    "open_interest_date": oi_meta.get("open_interest_date"),
                    "iv": number(item.get("impliedVolatility", item.get("implied_volatility"))),
                    "gamma": number(greeks.get("gamma")),
                    "delta": number(greeks.get("delta")),
                    "quote_time": q.get("t", q.get("timestamp")),
                    "feed": used_feed,
                }
            )
            contracts.append(meta)
    with _CACHE_LOCK:
        CHAIN_CACHE[cache_key] = (time.time(), contracts)
    return deepcopy(contracts)


def matrix(ticker, feed, depth, expiration_count, force=False, chain_pages=None):
    feed = resolve_options_feed(feed)
    expiration_count = _clamp_expiration_count(expiration_count)
    if chain_pages is None:
        chain_pages = _matrix_chain_pages(expiration_count)
    else:
        chain_pages = max(1, int(chain_pages))
    cache_key = (ticker.upper(), feed, str(depth), int(expiration_count), int(chain_pages))
    with _CACHE_LOCK:
        cached = MATRIX_CACHE.get(cache_key)
        if not force and cached and time.time() - cached[0] < 12:
            return deepcopy(cached[1])
    context = quote(ticker)
    spot = context["price_context"]
    if spot is None:
        raise ValueError("No usable underlying price is available to center the matrix.")
    depth_pts = _depth_to_points(spot, depth)
    full_chain = _depth_is_full_chain(depth)
    # Constrain OI query to the expiration range the matrix actually needs.
    oi_gte = datetime.now(timezone.utc).date().isoformat()
    oi_lte = (
        datetime.now(timezone.utc).date() + timedelta(days=_matrix_oi_horizon_days(expiration_count))
    ).isoformat()
    contracts = option_chain(ticker, feed, force=force, max_pages=chain_pages, expiration_gte=oi_gte, expiration_lte=oi_lte)
    used_feed = contracts[0]["feed"] if contracts else feed
    expirations = sorted({c["expiry"] for c in contracts if c["expiry"]})[:expiration_count]
    strikes = sorted(
        {
            c["strike"]
            for c in contracts
            if c["expiry"] in expirations
            and (full_chain or abs(c["strike"] - spot) <= depth_pts)
        }
    )
    by_cell = {
        (c["expiry"], c["strike"]): {
            "call": 0.0,
            "put": 0.0,
            "call_vex": 0.0,
            "put_vex": 0.0,
            "call_oi": 0.0,
            "put_oi": 0.0,
            "volume": 0.0,
            "call_mid": None,
            "put_mid": None,
            "listed": False,
            "oi_assumed_zero": False,
            "available": False,
        }
        for c in contracts
        if c["expiry"] in expirations and c["strike"] in strikes
    }
    missing_gamma = 0
    oi_assumed_zero = 0
    for contract in contracts:
        key = (contract["expiry"], contract["strike"])
        if key not in by_cell:
            continue
        cell = by_cell[key]
        side = contract["type"]
        cell["listed"] = True
        raw_oi = number(contract.get("open_interest"))
        if raw_oi is None:
            cell["oi_assumed_zero"] = True
            oi_assumed_zero += 1
        cell[side + "_oi"] += raw_oi or 0.0
        cell["volume"] += number(contract.get("volume")) or 0.0
        if contract.get("mid") is not None:
            cell[side + "_mid"] = contract["mid"]
        value = gex(contract, spot)
        if value is None:
            missing_gamma += 1
            # Listed contract without usable gamma/IV: still surface as a $0 cell
            # so the grid does not look like the strike/expiry is absent.
            if cell.get("call_mid") is not None or cell.get("put_mid") is not None or cell["listed"]:
                cell["available"] = True
            continue
        cell[side] += value
        vanna_value = vex(contract, spot)
        if vanna_value is not None:
            cell[side + "_vex"] += vanna_value
        cell["available"] = True
    rows = []
    for strike in strikes:
        values = []
        for expiry in expirations:
            cell = by_cell.get(
                (expiry, strike),
                {
                    "call": 0,
                    "put": 0,
                    "available": False,
                    "listed": False,
                    "oi_assumed_zero": False,
                },
            )
            net = cell["call"] + cell["put"]
            values.append(
                {
                    "expiration": expiry,
                    "call_gex": cell["call"],
                    "put_gex": cell["put"],
                    "net_gex": net,
                    "call_vex": cell.get("call_vex", 0.0),
                    "put_vex": cell.get("put_vex", 0.0),
                    "net_vex": cell.get("call_vex", 0.0) + cell.get("put_vex", 0.0),
                    "call_oi": cell.get("call_oi", 0.0),
                    "put_oi": cell.get("put_oi", 0.0),
                    "volume": cell.get("volume", 0.0),
                    "call_mid": cell.get("call_mid"),
                    "put_mid": cell.get("put_mid"),
                    "listed": bool(cell.get("listed")),
                    "oi_assumed_zero": bool(cell.get("oi_assumed_zero")),
                    "available": cell["available"],
                }
            )
        rows.append(
            {
                "strike": strike,
                "is_spot_band": abs(strike - spot) <= max(0.5, spot * 0.0015),
                "cells": values,
            }
        )
    result = {
        "ticker": ticker.upper(),
        "as_of": utcnow(),
        "feed": used_feed,
        "quote": context,
        "depth_points": None if full_chain else depth_pts,
        "depth_mode": "full_chain" if full_chain else "window",
        "expirations": expirations,
        "rows": rows,
        "formula": "Gamma × open interest × 100 × spot² × 0.01; puts receive a negative sign. Null OI remains unknown.",
        "caveat": "This is a public-OI heuristic, not verified dealer positioning.",
        "coverage": {
            "contracts": len(contracts),
            "contracts_missing_gamma": missing_gamma,
            # Back-compat alias for older UI/debug consumers.
            "contracts_missing_gamma_or_oi": missing_gamma,
            "contracts_oi_assumed_zero": oi_assumed_zero,
            "open_interest_source": "Alpaca option-contract metadata",
            "open_interest_as_of": max((c.get("open_interest_date") or "" for c in contracts), default=None),
            "calculated_cells": sum(1 for row in rows for cell in row["cells"] if cell["available"]),
            "listed_cells": sum(1 for row in rows for cell in row["cells"] if cell.get("listed")),
            "per_expiration": {
                e: {
                    "contracts_in_window": sum(1 for c in contracts if c["expiry"] == e and c["strike"] in strikes),
                    "with_oi": sum(1 for c in contracts if c["expiry"] == e and c["strike"] in strikes and c.get("open_interest") is not None and c["open_interest"] > 0),
                    "available": sum(1 for r in rows if r["cells"][expirations.index(e)]["available"]),
                    "total_rows": len(rows),
                }
                for e in expirations
            },
        },
        "summary": profile_summary(rows),
    }
    with _CACHE_LOCK:
        MATRIX_CACHE[cache_key] = (time.time(), result)
    return deepcopy(result)


def heatmap(ticker, feed, depth, expiration_count):
    payload = matrix(ticker, feed, depth, expiration_count)
    rows = list(reversed(payload["rows"]))
    expirations = payload["expirations"]

    def surface(field, unavailable_null=False):
        return [
            [(cell.get(field) if cell.get("available") or not unavailable_null else None) for cell in row["cells"]]
            for row in rows
        ]

    return {
        "schema_version": "local-heatmap-v1",
        "ticker": payload["ticker"],
        "spot": payload["quote"]["price_context"],
        "day_change_pct": payload["quote"].get("day_change_pct"),
        "updated": payload["as_of"],
        "expirations": expirations,
        "strikes": [row["strike"] for row in rows],
        "gex": surface("net_gex", True),
        "vex": surface("net_vex", True),
        "oi": [[cell.get("call_oi", 0.0) + cell.get("put_oi", 0.0) for cell in row["cells"]] for row in rows],
        "vol": surface("volume"),
        "call_oi": surface("call_oi"),
        "put_oi": surface("put_oi"),
        "call_mid": surface("call_mid"),
        "put_mid": surface("put_mid"),
        "totals": {
            "gex_by_expiration": [
                sum((row["cells"][i].get("net_gex") or 0.0) for row in rows) for i in range(len(expirations))
            ],
            "vex_by_expiration": [
                sum((row["cells"][i].get("net_vex") or 0.0) for row in rows) for i in range(len(expirations))
            ],
        },
        "summary": payload["summary"],
        "contracts": payload["coverage"]["contracts"],
        "formula": {
            "gex": payload["formula"],
            "vex": "Black-Scholes vanna × OI × 100 × spot × 0.01; a local estimate with call/put sign convention.",
        },
        "caveat": "This is a transparent local estimate, not a reproduction of a proprietary exposure model.",
    }


def night_vision(ticker, feed, depth, expiration_count, force=False):
    payload = matrix(ticker, feed, depth, expiration_count, force=force)
    levels = []
    xray = []
    for row in payload["rows"]:
        if not any(cell["available"] for cell in row["cells"]):
            continue
        net_gex = sum(cell["net_gex"] for cell in row["cells"])
        net_vex = sum(cell.get("net_vex") or 0.0 for cell in row["cells"])
        levels.append({"price": row["strike"], "net_gex": net_gex, "abs_gex": abs(net_gex), "net_vex": net_vex, "abs_vex": abs(net_vex)})
        xray.append(
            {
                "strike": row["strike"],
                "net_gex": net_gex,
                "net_vex": net_vex,
                "abs_gex": abs(net_gex),
                "abs_vex": abs(net_vex),
            }
        )
    levels.sort(key=lambda item: item["abs_gex"], reverse=True)
    top = levels[:12]
    peak = top[0] if top else None
    spot = payload["quote"]["price_context"] or 0
    visible = sorted(top, key=lambda item: item["price"], reverse=True)
    for level in visible:
        if peak is not None and level["price"] == peak["price"]:
            level["kind"] = "global"
        elif level["price"] >= spot:
            level["kind"] = "above_spot"
        else:
            level["kind"] = "below_spot"

    # Ghost: synthetic next-~15min path from net GEX pressure around spot.
    # Positive net GEX below spot + negative above → mean-reverting pin; opposite → trend.
    ghost = []
    if spot and visible:
        below = [l for l in visible if l["price"] < spot]
        above = [l for l in visible if l["price"] > spot]
        pull_below = sum(l["net_gex"] for l in below[:3])
        pull_above = sum(l["net_gex"] for l in above[:3])
        # Dealer hedge heuristic: large positive GEX below acts as support (buy dips).
        drift = 0.0
        if pull_below > 0 and pull_above < 0:
            drift = 0.0  # pin
        elif abs(pull_above) > abs(pull_below):
            drift = -0.0015 if pull_above > 0 else 0.0012
        else:
            drift = 0.0015 if pull_below < 0 else -0.0010
        magnet = peak["price"] if peak else spot
        magnet_pull = (magnet - spot) / spot * 0.25
        px = float(spot)
        for step in range(16):
            px = px * (1.0 + drift + magnet_pull * 0.08)
            # Soft attraction to nearest level
            nearest = min(visible, key=lambda l: abs(l["price"] - px))
            px = px * 0.92 + nearest["price"] * 0.08
            ghost.append({"step": step, "price": round(px, 4)})

    xray_sorted = sorted(xray, key=lambda r: r["abs_gex"], reverse=True)[:40]
    for row in xray_sorted:
        if peak is not None and row["strike"] == peak["price"]:
            row["kind"] = "global"
        elif row["strike"] >= spot:
            row["kind"] = "above_spot"
        else:
            row["kind"] = "below_spot"
    xray_sorted.sort(key=lambda r: r["strike"], reverse=True)

    payload["levels"] = visible
    payload["peak"] = peak
    payload["xray"] = xray_sorted
    payload["ghost"] = ghost
    payload["ghost_note"] = (
        "Ghost projects the next ~15 minutes from the dealer-hedging surface (GEX magnets). "
        "Heuristic only — not a forecast. Most useful on SPY/QQQ."
    )
    return payload


def _local_bars(ticker, timeframe="1Day", limit=250):
    """Load bars from local historical data SQLite if available.
    
    Filters by timeframe:
    - Daily bars: timestamps at 00:00:00 or 04:00:00 UTC
    - Intraday bars: timestamps with intraday times (5Min, 1Min, etc.)
    """
    import sqlite3
    db_path = ROOT / "data" / "historical_bars.sqlite"
    if not db_path.exists():
        return bars(ticker, timeframe, limit)
    
    # Determine if intraday or daily
    tf_lower = timeframe.lower()
    is_intraday = tf_lower in ("1min", "5min", "15min", "1hour", "4hour", "1m", "5m", "15m", "1h", "4h")
    
    try:
        conn = sqlite3.connect(str(db_path))
        
        if is_intraday:
            # Intraday bars: exclude midnight/4am timestamps (daily bars)
            query = """
                SELECT timestamp, open, high, low, close, volume, vwap, trades
                FROM historical_bars
                WHERE symbol = ?
                  AND timestamp NOT LIKE '%T00:00:00%'
                  AND timestamp NOT LIKE '%T04:00:00%'
                ORDER BY timestamp DESC
                LIMIT ?
            """
        else:
            # Daily bars: only midnight/4am timestamps
            query = """
                SELECT timestamp, open, high, low, close, volume, vwap, trades
                FROM historical_bars
                WHERE symbol = ?
                  AND (timestamp LIKE '%T00:00:00%' OR timestamp LIKE '%T04:00:00%')
                ORDER BY timestamp DESC
                LIMIT ?
            """
        
        rows = conn.execute(query, (ticker.upper(), limit)).fetchall()
        conn.close()
        
        if not rows:
            return bars(ticker, timeframe, limit)
        
        # Reverse to chronological order
        rows.reverse()
        
        bar_list = [
            {
                "time": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
            for row in rows
        ]
        
        return {"bars": bar_list}
    except Exception:
        return bars(ticker, timeframe, limit)


def bars(ticker, timeframe, limit=200):
    allowed = {
        "1m": "1Min",
        "5m": "5Min",
        "15m": "15Min",
        "1h": "1Hour",
        "4h": "4Hour",
        "1d": "1Day",
        "1w": "1Week",
    }
    normalized = timeframe.lower()
    tf = allowed.get(normalized, "5Min")
    want = min(int(limit), 1000)
    cache_key = (ticker.upper(), normalized, want)
    with _CACHE_LOCK:
        cached = BARS_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < 20:
            return deepcopy(cached[1])

    # Span wide enough to contain `want` newest bars; over-fetch then slice the tail.
    # Alpaca returns ascending from `start`, so a tight start+small limit yields the oldest bars.
    calendar_days = {
        "1m": max(3, want // 200 + 2),
        "5m": max(5, want // 60 + 3),
        "15m": max(8, want // 20 + 4),
        "1h": max(14, want // 5 + 5),
        "4h": max(30, want // 2 + 10),
        "1d": max(want + 20, 40),
        "1w": max(want * 10, 120),
    }.get(normalized, 7)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=calendar_days)
    _, _, _, preferred_stock = local_settings()
    feeds = []
    for feed in (preferred_stock, "sip", "iex"):
        if feed not in feeds:
            feeds.append(feed)

    output, used_feed = [], preferred_stock
    last_error = None
    for feed in feeds:
        try:
            collected = []
            query = {
                "timeframe": tf,
                "limit": 1000,
                "feed": feed,
                "adjustment": "raw",
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            for _ in range(6):
                raw = alpaca(f"/v2/stocks/{ticker.upper()}/bars", query)
                for bar in raw.get("bars") or []:
                    collected.append(
                        {
                            "time": bar.get("t"),
                            "open": number(bar.get("o")),
                            "high": number(bar.get("h")),
                            "low": number(bar.get("l")),
                            "close": number(bar.get("c")),
                            "volume": number(bar.get("v")),
                        }
                    )
                token = raw.get("next_page_token")
                if not token:
                    break
                query = {
                    "timeframe": tf,
                    "limit": 1000,
                    "feed": feed,
                    "adjustment": "raw",
                    "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "page_token": token,
                }
            used_feed = feed
            output = collected[-want:]
            if output:
                break
        except ValueError as exc:
            last_error = exc
    if not output and last_error:
        raise last_error
    result = {"ticker": ticker.upper(), "timeframe": normalized, "feed": used_feed, "bars": output}
    with _CACHE_LOCK:
        BARS_CACHE[cache_key] = (time.time(), result)
    return deepcopy(result)


def flow(ticker, feed, min_premium=5000, max_price=None, option_type="all", side="all", moneyness="all", force=False):
    """Build a Spyglass tape from latest option trades on the chain snapshots."""
    feed = resolve_options_feed(feed)
    context = quote(ticker)
    spot = context["price_context"]
    contracts = option_chain(ticker, feed, force=force)
    prints = []
    for contract in contracts:
        last = number(contract.get("last"))
        size = int(contract.get("size") or 0)
        if last is None or last <= 0 or size <= 0:
            continue
        if max_price is not None and last > max_price:
            continue
        premium = last * size * 100.0
        if premium < float(min_premium):
            continue
        kind = contract.get("type")
        if option_type == "calls" and kind != "call":
            continue
        if option_type == "puts" and kind != "put":
            continue
        aggressor = classify_aggressor(last, contract.get("bid"), contract.get("ask"))
        if side == "ask" and aggressor != "buy":
            continue
        if side == "bid" and aggressor != "sell":
            continue
        strike = contract.get("strike")
        if spot and strike is not None:
            otm_pct = (strike / spot - 1.0) * 100.0
            if kind == "put":
                otm_pct = -otm_pct
            is_otm = (kind == "call" and strike > spot) or (kind == "put" and strike < spot)
        else:
            otm_pct = None
            is_otm = None
        if moneyness == "otm" and not is_otm:
            continue
        if moneyness == "itm" and is_otm is not False:
            continue
        prints.append(
            {
                "ticker": ticker.upper(),
                "contract": contract["symbol"],
                "time": contract.get("trade_time"),
                "premium": premium,
                "size": size,
                "price": last,
                "strike": strike,
                "expiration": contract.get("expiry"),
                "type": kind,
                "bid": contract.get("bid"),
                "ask": contract.get("ask"),
                "side": aggressor,
                "tier": premium_tier(premium),
                "otm_pct": otm_pct,
                "exchange": contract.get("exchange"),
                "feed": contract.get("feed") or feed,
            }
        )
    prints.sort(key=lambda item: item.get("time") or "", reverse=True)
    # Keep a rolling buffer so repeated polls feel like a live tape.
    with _CACHE_LOCK:
        prior = FLOW_CACHE.get(ticker.upper(), [])
        seen = {f"{p['contract']}|{p['time']}|{p['size']}|{p['price']}" for p in prints}
        merged = prints[:]
        for item in prior:
            key = f"{item['contract']}|{item['time']}|{item['size']}|{item['price']}"
            if key not in seen:
                merged.append(item)
                seen.add(key)
        merged.sort(key=lambda item: item.get("time") or "", reverse=True)
        FLOW_CACHE[ticker.upper()] = merged[:400]
        tape = FLOW_CACHE[ticker.upper()]
    # Re-apply filters on the merged buffer for response.
    filtered = []
    for item in tape:
        if item["premium"] < float(min_premium):
            continue
        if max_price is not None and item["price"] > max_price:
            continue
        if option_type == "calls" and item["type"] != "call":
            continue
        if option_type == "puts" and item["type"] != "put":
            continue
        if side == "ask" and item["side"] != "buy":
            continue
        if side == "bid" and item["side"] != "sell":
            continue
        if moneyness == "otm":
            if item.get("otm_pct") is None:
                continue
            kind = item["type"]
            spot_now = spot or 0
            is_otm = (kind == "call" and item["strike"] > spot_now) or (kind == "put" and item["strike"] < spot_now)
            if not is_otm:
                continue
        if moneyness == "itm":
            kind = item["type"]
            spot_now = spot or 0
            is_otm = (kind == "call" and item["strike"] > spot_now) or (kind == "put" and item["strike"] < spot_now)
            if is_otm:
                continue
        filtered.append(item)
    return {
        "ticker": ticker.upper(),
        "as_of": utcnow(),
        "feed": feed,
        "quote": context,
        "min_premium": float(min_premium),
        "count": len(filtered),
        "prints": filtered[:150],
        "caveat": "Aggressor side is inferred from trade vs bid/ask; not a verified buyer/seller label.",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CipherCore/0.2"

    def log_message(self, _format, *_args):
        return

    def send_json(self, status, data):
        body = json.dumps(data, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def write_event(self, event, data):
        payload = json.dumps(data, separators=(",", ":"), default=str)
        chunk = f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
        try:
            self.wfile.write(chunk)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return False

    def stream_live(self, params):
        def pget(name, default=None):
            values = params.get(name)
            if not values:
                return default
            return values[-1]

        ticker = (pget("ticker") or "SPY").upper()
        feed = resolve_options_feed(pget("feed"))
        depth = pget("depth", "0.06")
        expirations = int(pget("expirations", str(DEFAULT_MATRIX_EXPIRATIONS)))
        min_premium = float(pget("min", "5000"))
        interval = max(5, int(pget("interval", "15")))
        self.send_sse_headers()
        if not self.write_event("hello", {"ticker": ticker, "interval": interval, "read_only": True}):
            return
        cycles = 0
        try:
            while True:
                cycles += 1
                force = cycles == 1
                try:
                    q = quote(ticker)
                    if not self.write_event("quote", q):
                        return
                except Exception as exc:
                    if not self.write_event("error", {"scope": "quote", "error": str(exc)}):
                        return
                if cycles == 1 or cycles % max(1, interval // 5) == 0:
                    try:
                        nv = night_vision(ticker, feed, depth, expirations, force=force)
                        if not self.write_event(
                            "matrix",
                            {
                                "ticker": nv["ticker"],
                                "as_of": nv["as_of"],
                                "feed": nv["feed"],
                                "quote": nv["quote"],
                                "expirations": nv["expirations"],
                                "rows": nv["rows"],
                                "summary": nv["summary"],
                                "coverage": nv["coverage"],
                                "levels": nv.get("levels"),
                                "peak": nv.get("peak"),
                                "xray": nv.get("xray"),
                                "ghost": nv.get("ghost"),
                                "ghost_note": nv.get("ghost_note"),
                                "formula": nv["formula"],
                                "caveat": nv["caveat"],
                            },
                        ):
                            return
                    except Exception as exc:
                        if not self.write_event("error", {"scope": "matrix", "error": str(exc)}):
                            return
                    try:
                        tape = flow(ticker, feed, min_premium=min_premium, force=force)
                        if not self.write_event("flow", {"as_of": tape["as_of"], "count": tape["count"], "prints": tape["prints"][:40]}):
                            return
                    except Exception as exc:
                        if not self.write_event("error", {"scope": "flow", "error": str(exc)}):
                            return
                if not self.write_event("heartbeat", {"ts": utcnow(), "cycle": cycles}):
                    return
                time.sleep(5)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        def pget(name, default=None):
            values = params.get(name)
            if not values:
                return default
            return values[-1]

        ticker = (pget("ticker") or pget("symbol") or "SPY").upper()
        feed = pget("feed")
        try:
            if parsed.path in {"/health", "/api/health"}:
                key, secret, default_feed, stock_feed = local_settings()
                data = {
                    "status": "ok",
                    "service": "cipher-core",
                    "market_data_configured": bool(key and secret),
                    "default_options_feed": default_feed,
                    "default_stock_feed": stock_feed,
                    "read_only": True,
                    "as_of": utcnow(),
                }
            elif parsed.path == "/api/governance":
                data = governance_status()
            elif parsed.path == "/api/quote":
                data = quote(ticker)
            elif parsed.path == "/debug/caches":
                now = time.time()
                def _cache_stats(name, cache, ttl):
                    with _CACHE_LOCK:
                        n = len(cache)
                        ages = [now - v[0] for v in cache.values()] if cache else []
                    return {
                        "name": name,
                        "entries": n,
                        "ttl_seconds": ttl,
                        "oldest_age_s": round(max(ages), 1) if ages else None,
                        "newest_age_s": round(min(ages), 1) if ages else None,
                        "avg_age_s": round(sum(ages) / len(ages), 1) if ages else None,
                    }
                data = {
                    "as_of": utcnow(),
                    "caches": [
                        _cache_stats("quote", QUOTE_CACHE, 3),
                        _cache_stats("chain", CHAIN_CACHE, 15),
                        _cache_stats("matrix", MATRIX_CACHE, 12),
                        _cache_stats("bars", BARS_CACHE, 20),
                        _cache_stats("flow", FLOW_CACHE, 10),
                        _cache_stats("oi", OI_CACHE, 600),
                    ],
                }
            elif parsed.path == "/api/matrix":
                data = matrix(
                    ticker,
                    feed,
                    pget("depth", "0.06"),
                    int(pget("expirations", str(DEFAULT_MATRIX_EXPIRATIONS))),
                    force=str(pget("fresh", "0")).lower() in {"1", "true", "yes"},
                )
            elif parsed.path == "/api/heatmap":
                data = heatmap(
                    ticker,
                    feed,
                    pget("depth", "0.06"),
                    int(pget("expirations", "36")),
                )
            elif parsed.path == "/api/night-vision":
                data = night_vision(
                    ticker,
                    feed,
                    pget("depth", "0.06"),
                    int(pget("expirations", str(DEFAULT_MATRIX_EXPIRATIONS))),
                    force=str(pget("fresh", "0")).lower() in {"1", "true", "yes"},
                )
            elif parsed.path == "/api/bars":
                data = bars(ticker, pget("timeframe", "5m"), limit=int(pget("limit", "200")))
            elif parsed.path in {"/api/flow", "/api/spyglass"}:
                max_price = pget("pmax")
                data = flow(
                    ticker,
                    feed,
                    min_premium=float(pget("min") or pget("premium") or "5000"),
                    max_price=float(max_price) if max_price not in (None, "", "all") else None,
                    option_type=str(pget("type", "all")).lower(),
                    side=str(pget("side", "all")).lower(),
                    moneyness=str(pget("money") or pget("moneyness") or "all").lower(),
                    force=str(pget("fresh", "0")).lower() in {"1", "true", "yes"},
                )
            elif parsed.path in {"/api/stream", "/api/live"}:
                return self.stream_live(params)
            elif parsed.path in {"/api/scan", "/api/scanner"}:
                mode = str(pget("mode", "short")).lower()
                strategy = str(pget("strategy", "cipher")).lower()
                limit = int(pget("limit", "30"))
                # Setup Scanner defaults to nearest (lowest DTE); UI always sends this.
                cluster_exp = pget("cluster_exp") or pget("exp") or "nearest"
                if str(cluster_exp).lower() in {"", "all"}:
                    cluster_exp = "nearest"
                custom = pget("tickers") or pget("universe")
                universe = None
                if custom:
                    universe = [part.strip().upper() for part in custom.replace(";", ",").split(",") if part.strip()]
                from scanner import FLASH_INDEX_UNIVERSE, FLASH_UNIVERSE

                if universe is None and strategy == "flash":
                    universe = list(FLASH_UNIVERSE)
                elif universe is None and strategy == "flash_index":
                    universe = list(FLASH_INDEX_UNIVERSE)
                elif universe is None and strategy == "flash_agentic":
                    universe = list(FLASH_UNIVERSE)
                async_flag = str(pget("async", "0")).lower() in {"1", "true", "yes"}
                # Serial only (workers clamped to 1 in run_scan) — avoids Alpaca 429s.
                workers = 1
                if async_flag:
                    job_id = start_scan_job(
                        matrix,
                        mode=mode,
                        strategy=strategy,
                        feed=feed or local_settings()[2],
                        limit=limit,
                        universe=universe,
                        workers=workers,
                        cluster_exp=cluster_exp,
                    )
                    data = {"job_id": job_id, "status": "queued", "universe_size": len(universe or SCAN_UNIVERSE)}
                else:
                    data = run_scan(
                        matrix,
                        mode=mode,
                        strategy=strategy,
                        feed=feed or local_settings()[2],
                        limit=limit,
                        universe=universe,
                        workers=workers,
                        cluster_exp=cluster_exp,
                    )
            elif parsed.path == "/api/scan/job":
                job_id = pget("id")
                job = get_scan_job(job_id) if job_id else None
                if not job:
                    self.send_json(404, {"error": "Unknown scan job"})
                    return
                data = job
            elif parsed.path == "/api/scan/universe":
                data = {
                    "count": len(SCAN_UNIVERSE),
                    "tickers": SCAN_UNIVERSE,
                    "raw_count": UNIVERSE_META.get("raw_count"),
                    "filtered_count": UNIVERSE_META.get("filtered_count"),
                    "cutoff": UNIVERSE_META.get("cutoff"),
                    "source": UNIVERSE_META.get("source"),
                    "included_tiers": UNIVERSE_META.get("included_tiers"),
                    "excluded_tiers": UNIVERSE_META.get("excluded_tiers"),
                    "tier_counts": UNIVERSE_META.get("tier_counts"),
                    "excluded_counts": UNIVERSE_META.get("excluded_counts"),
                    "thresholds_usd": UNIVERSE_META.get("thresholds_usd"),
                    "as_of": UNIVERSE_META.get("as_of"),
                    "concurrency": 1,
                    "modes": ["short", "long", "leap"],
                    "strategies": ["cipher", "cluster", "liquidity", "flash", "flash_index", "flash_agentic"],
                    "hints": {
                        "short": "Short term scans the nearest/lowest-DTE expiration only.",
                        "long": "Long term weighs multi-expiration structure over near-dated noise.",
                        "leap": "LEAP scans options expiring within ~120 market days.",
                    },
                }
            elif parsed.path == "/api/ranking-lab":
                force = str(pget("fresh", "0")).lower() in {"1", "true", "yes"}
                data = ranking_lab.status(force=force)
            elif parsed.path == "/api/weight-lab":
                action = (pget("action") or "status").lower()
                if action == "status":
                    data = weight_lab.status()
                elif action == "seed":
                    data = weight_lab.seed_audit_commercial()
                    data["status"] = weight_lab.status()
                elif action == "fit":
                    use_local = str(pget("local", "1")).lower() not in {"0", "false", "no"}
                    rank_loss = str(pget("rank_loss", "0")).lower() in {"1", "true", "yes"}
                    data = weight_lab.fit_weights(use_local_features=use_local, rank_loss=rank_loss)
                elif action in {"fit-flash", "fit_flash"}:
                    use_local = str(pget("local", "1")).lower() not in {"0", "false", "no"}
                    rank_loss = str(pget("rank_loss", "0")).lower() in {"1", "true", "yes"}
                    data = weight_lab.fit_flash_weights(use_local_features=use_local, rank_loss=rank_loss)
                elif action in {"fit-liq", "fit_liq"}:
                    use_local = str(pget("local", "1")).lower() not in {"0", "false", "no"}
                    rank_loss = str(pget("rank_loss", "0")).lower() in {"1", "true", "yes"}
                    data = weight_lab.fit_liq_weights(use_local_features=use_local, rank_loss=rank_loss)
                elif action in {"fit-cluster", "fit_cluster"}:
                    use_local = str(pget("local", "1")).lower() not in {"0", "false", "no"}
                    rank_loss = str(pget("rank_loss", "0")).lower() in {"1", "true", "yes"}
                    data = weight_lab.fit_cluster_weights(use_local_features=use_local, rank_loss=rank_loss)
                elif action == "activate":
                    data = weight_lab.set_active(True)
                elif action == "deactivate":
                    data = weight_lab.set_active(False)
                elif action in {"activate-flash", "activate_flash"}:
                    data = weight_lab.set_flash_active(True)
                elif action in {"deactivate-flash", "deactivate_flash"}:
                    data = weight_lab.set_flash_active(False)
                elif action == "show":
                    data = weight_lab.load_weights() or {"error": "no weights fitted"}
                elif action in {"show-flash", "show_flash"}:
                    data = weight_lab.load_flash_weights() or {"error": "no flash weights fitted"}
                elif action in {"show-cluster", "show_cluster"}:
                    data = weight_lab.load_cluster_score_weights()
                    data["path"] = str(weight_lab.CLUSTER_SCORE_WEIGHTS_PATH)
                    data["hint"] = (
                        "Cluster ranking: hard tier quad > triple > battle > … "
                        "then weighted factors. Edit cluster_score_weights.json to tune."
                    )
                elif action == "dump":
                    custom = pget("tickers")
                    if custom:
                        tickers = [part.strip().upper() for part in custom.replace(";", ",").split(",") if part.strip()]
                    else:
                        tickers = sorted({r["ticker"] for r in weight_lab.load_all_commercial()})
                    data = weight_lab.dump_features_for_tickers(
                        matrix,
                        tickers[:80],
                        feed=feed or local_settings()[2],
                        mode=(pget("mode") or "short").lower(),
                    )
                else:
                    self.send_json(400, {"error": f"Unknown weight-lab action: {action}"})
                    return
            elif parsed.path == "/api/backtest":
                action = (pget("action") or "run").lower()
                kind = (pget("kind") or "").strip() or None
                if action == "list":
                    data = {
                        "snapshots": cluster_backtest.list_snapshots(),
                        "forward": cluster_backtest.list_forward_reports(),
                    }
                elif action == "capture":
                    custom = pget("tickers")
                    if custom:
                        tickers = [
                            part.strip().upper()
                            for part in custom.replace(";", ",").split(",")
                            if part.strip()
                        ]
                    else:
                        tickers = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "AMD", "TSLA", "META", "AMZN", "MSFT"]
                    min_contracts = max(0, min(int(pget("min_contracts") or 500), 5000))
                    min_cells = max(0, min(int(pget("min_coverage_cells") or 20), 500))
                    data = cluster_backtest.capture_cluster_snapshot(
                        matrix,
                        tickers[:40],
                        feed=feed or local_settings()[2],
                        mode=(pget("mode") or "short").lower(),
                        cluster_exp=pget("cluster_exp") or "nearest",
                        limit=max(5, min(int(pget("limit") or 30), 40)),
                        kind=kind,
                        min_contracts=min_contracts,
                        min_coverage_cells=min_cells,
                        skip_weak_depth=(pget("keep_weak_depth") or "").lower() not in {"1", "true", "yes"},
                    )
                elif action in {"ingest-scan", "ingest_scan"}:
                    self.send_json(
                        405,
                        {
                            "error": "POST JSON body with picks[] required",
                            "hint": "POST /api/backtest?action=ingest-scan with {picks:[…]} or GET action=capture",
                        },
                    )
                    return
                elif action in {"run", "cluster"}:
                    custom = pget("tickers")
                    if custom:
                        tickers = [
                            part.strip().upper()
                            for part in custom.replace(";", ",").split(",")
                            if part.strip()
                        ]
                    else:
                        tickers = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "AMD", "TSLA", "META", "AMZN", "MSFT"]
                    min_contracts = max(0, min(int(pget("min_contracts") or 500), 5000))
                    min_cells = max(0, min(int(pget("min_coverage_cells") or 20), 500))
                    data = cluster_backtest.run_cluster_backtest(
                        matrix,
                        bars,
                        tickers[:40],
                        feed=feed or local_settings()[2],
                        mode=(pget("mode") or "short").lower(),
                        cluster_exp=pget("cluster_exp") or "nearest",
                        horizon=max(1, min(int(pget("horizon") or 5), 20)),
                        limit=max(5, min(int(pget("limit") or 30), 40)),
                        snapshot_path=pget("snapshot") or None,
                        kind=kind,
                        min_contracts=min_contracts,
                        min_coverage_cells=min_cells,
                        skip_weak_depth=(pget("keep_weak_depth") or "").lower() not in {"1", "true", "yes"},
                    )
                elif action == "score":
                    snaps = cluster_backtest.list_snapshots(1)
                    path = pget("snapshot") or (snaps[0]["path"] if snaps else None)
                    if not path:
                        data = {"error": "No cluster snapshots yet — run a backtest first"}
                    else:
                        snap = json.loads(Path(path).read_text(encoding="utf-8"))
                        data = cluster_backtest.score_snapshot(
                            bars,
                            snap,
                            horizon=max(1, min(int(pget("horizon") or 5), 20)),
                            kind=kind,
                        )
                        data["snapshot_path"] = path
                elif action in {"rescore-due", "rescore_due", "forward"}:
                    raw_h = pget("horizons") or "1,3,5"
                    horizons = []
                    for part in str(raw_h).replace(";", ",").split(","):
                        part = part.strip()
                        if part.isdigit():
                            horizons.append(max(1, min(int(part), 20)))
                    data = cluster_backtest.rescore_due(
                        bars,
                        horizons=horizons or (1, 3, 5),
                        kind=kind,
                    )
                else:
                    self.send_json(400, {"error": f"Unknown backtest action: {action}"})
                    return
            elif parsed.path == "/api/strategy":
                action = (pget("action") or "run").lower()
                if action == "list":
                    data = {
                        "strategies": list(strategy_backtest.STRATEGIES.keys()),
                        "descriptions": {
                            "wall_bounce": "Buy at put wall support, target call wall resistance",
                            "gamma_squeeze": "Long calls when squeeze probability is high",
                            "vacuum_breakout": "Enter when price enters GEX vacuum zone",
                            "divergence_reversal": "Fade when VEX-GEX divergence is extreme",
                            "gex_momentum": "Follow delta-GEX momentum direction",
                            "cluster_magnet": "Trade toward cluster center levels",
                            "term_aligned": "Only trade when term structure is aligned",
                            "flow_confluence": "Trade when flow and GEX agree",
                        },
                    }
                elif action == "run":
                    custom = pget("tickers")
                    if custom:
                        tickers = [
                            part.strip().upper()
                            for part in custom.replace(";", ",").split(",")
                            if part.strip()
                        ]
                    else:
                        tickers = [
                            "SPY", "QQQ", "IWM", "DIA",
                            "NVDA", "AAPL", "AMD", "TSLA", "META", "AMZN", "MSFT", "GOOGL",
                            "NFLX", "CRM", "PLTR", "SOFI", "COIN", "MARA", "RIOT",
                            "XOM", "JPM", "GS", "BA", "CAT",
                        ]
                    strats = pget("strategies")
                    strat_list = (
                        [s.strip().lower() for s in strats.replace(";", ",").split(",") if s.strip()]
                        if strats
                        else None
                    )
                    data = strategy_backtest.run_strategy_backtest(
                        matrix,
                        bars,
                        tickers[:20],
                        feed=feed or local_settings()[2],
                        strategies=strat_list,
                        iv=float(pget("iv") or 0.25),
                        dte=float(pget("dte") or 30),
                    )
                else:
                    self.send_json(400, {"error": f"Unknown strategy action: {action}"})
                    return
            elif parsed.path == "/api/historical-backtest":
                action = (pget("action") or "run").lower()
                if action == "run":
                    custom = pget("tickers")
                    if custom:
                        tickers = [
                            part.strip().upper()
                            for part in custom.replace(";", ",").split(",")
                            if part.strip()
                        ]
                    else:
                        tickers = None  # Use all available tickers
                    strats = pget("strategies")
                    strat_list = (
                        [s.strip().lower() for s in strats.replace(";", ",").split(",") if s.strip()]
                        if strats
                        else None
                    )
                    data = historical_backtest.run_historical_backtest(
                        bars,
                        tickers=tickers,
                        strategies=strat_list,
                        iv=float(pget("iv") or 0.25),
                        dte=float(pget("dte") or 30),
                    )
                else:
                    self.send_json(400, {"error": f"Unknown historical-backtest action: {action}"})
                    return
            elif parsed.path == "/api/price-backtest":
                action = (pget("action") or "run").lower()
                if action == "list":
                    data = {
                        "strategies": list(price_backtest.PRICE_STRATEGIES.keys()),
                        "descriptions": {
                            "long_straddle": "Buy ATM straddle — profits from large moves either direction",
                            "long_strangle": "Buy OTM strangle — cheaper, needs bigger move",
                            "iron_condor": "Sell OTM strangle + wings — profits from range-bound price",
                            "covered_call": "Hold stock + sell OTM call — income strategy",
                            "bull_call_spread": "Buy ATM + sell OTM call — bullish defined risk",
                            "bear_put_spread": "Buy ATM + sell OTM put — bearish defined risk",
                            "momentum_long": "Go long on N-day high breakout",
                            "mean_reversion": "Buy RSI < 30, sell RSI > 70",
                            "bollinger_squeeze": "Trade Bollinger Band breakouts",
                            "gap_fill": "Fade overnight gaps",
                            "trend_follow": "Follow 20/50 day MA crossover",
                        },
                    }
                elif action == "run":
                    custom = pget("tickers")
                    if custom:
                        tickers = [
                            part.strip().upper()
                            for part in custom.replace(";", ",").split(",")
                            if part.strip()
                        ]
                    else:
                        tickers = [
                            "SPY", "QQQ", "IWM", "DIA",
                            "NVDA", "AAPL", "AMD", "TSLA", "META", "AMZN", "MSFT", "GOOGL",
                            "NFLX", "CRM", "PLTR", "SOFI", "COIN",
                            "XOM", "JPM", "GS", "BA", "CAT",
                        ]
                    strats = pget("strategies")
                    strat_list = (
                        [s.strip().lower() for s in strats.replace(";", ",").split(",") if s.strip()]
                        if strats
                        else None
                    )
                    data = price_backtest.run_price_backtest(
                        bars,
                        tickers[:25],
                        strategies=strat_list,
                        iv=float(pget("iv") or 0.25),
                        dte=float(pget("dte") or 30),
                        bars_limit=int(pget("bars_limit") or 200),
                    )
                else:
                    self.send_json(400, {"error": f"Unknown price-backtest action: {action}"})
                    return
            elif parsed.path == "/api/edge-backtest":
                action = (pget("action") or "run").lower()
                if action == "list":
                    data = {
                        "strategies": list(edge_backtest.EDGE_STRATEGIES.keys()),
                        "descriptions": {
                            "vol_risk_premium": "Sell when IV/RV > 1.3 — IV overstates realized vol",
                            "overnight_harvest": "Buy close, sell open — 70% of returns happen overnight",
                            "vol_mean_reversion": "Fade extreme vol spikes — vol mean reverts",
                            "skew_harvest": "Sell OTM put spreads when skew extreme — puts overpriced",
                            "pead_drift": "Post-earnings drift — stocks drift in earnings direction 60+ days",
                            "weekend_theta": "Sell Friday, buy Monday — capture weekend decay",
                            "vol_regime_switch": "Switch strategies by vol regime — buy low vol, sell high vol",
                            "momentum_vol_filter": "Only trade momentum in low vol — cleaner trends",
                            "iv_rv_spread": "Sell when IV-RV > 2σ — spread mean reverts",
                            "gap_and_go": "Gap up + volume = continuation — momentum after gap",
                        },
                    }
                elif action == "run":
                    custom = pget("tickers")
                    if custom:
                        tickers = [
                            part.strip().upper()
                            for part in custom.replace(";", ",").split(",")
                            if part.strip()
                        ]
                    else:
                        tickers = [
                            "SPY", "QQQ", "IWM", "DIA",
                            "NVDA", "AAPL", "AMD", "TSLA", "META", "AMZN", "MSFT", "GOOGL",
                            "NFLX", "CRM", "PLTR", "SOFI", "COIN",
                            "XOM", "JPM", "GS", "BA", "CAT",
                        ]
                    strats = pget("strategies")
                    strat_list = (
                        [s.strip().lower() for s in strats.replace(";", ",").split(",") if s.strip()]
                        if strats
                        else None
                    )
                    # Use local historical data if available, otherwise fall back to live
                    use_local = pget("source") != "live"
                    bars_fn = _local_bars if use_local else bars
                    
                    # Filter by date range if provided
                    start_date = pget("start_date")  # YYYY-MM-DD
                    
                    data = edge_backtest.run_edge_backtest(
                        bars_fn,
                        tickers[:25],
                        strategies=strat_list,
                        bars_limit=int(pget("bars_limit") or 250),
                        start_date=start_date,
                    )
                else:
                    self.send_json(400, {"error": f"Unknown edge-backtest action: {action}"})
                    return
            elif parsed.path == "/api/intraday-backtest":
                action = (pget("action") or "run").lower()
                if action == "list":
                    data = {
                        "strategies": list(intraday_backtest.INTRADAY_STRATEGIES.keys()),
                        "descriptions": {
                            "orb_15min": "Opening Range Breakout — 15 min range breakout",
                            "vwap_momentum": "VWAP cross with volume surge",
                            "intraday_rsi_momentum": "RSI(14) > 70 continuation on 5-min",
                            "momentum_ignition": "Volume spike + price breakout",
                            "pullback_to_vwap": "Buy pullbacks to VWAP in uptrend",
                        },
                    }
                elif action == "run":
                    custom = pget("tickers")
                    if custom:
                        tickers = [
                            part.strip().upper()
                            for part in custom.replace(";", ",").split(",")
                            if part.strip()
                        ]
                    else:
                        tickers = ["SPY", "QQQ", "IWM", "TSLA", "NVDA", "AAPL"]
                    
                    strat_param = pget("strategies")
                    strat_list = (
                        [s.strip() for s in strat_param.split(",") if s.strip()]
                        if strat_param
                        else None
                    )
                    start_date = pget("start_date")
                    
                    # Use local 5-min bars
                    def _intraday_bars(ticker, timeframe="5Min", limit=5000):
                        return _local_bars(ticker, timeframe, limit)
                    
                    data = intraday_backtest.run_intraday_backtest(
                        _intraday_bars,
                        tickers[:25],
                        strategies=strat_list,
                        bars_limit=int(pget("bars_limit") or 5000),
                        start_date=start_date,
                    )
                else:
                    self.send_json(400, {"error": f"Unknown intraday-backtest action: {action}"})
                    return
            else:
                self.send_json(
                    404,
                    {
                        "error": "Not found",
                        "routes": [
                            "/health",
                            "/api/health",
                            "/api/quote",
                            "/api/governance",
                            "/api/matrix",
                            "/api/heatmap",
                            "/api/night-vision",
                            "/api/bars",
                            "/api/flow",
                            "/api/stream",
                            "/api/scan",
                            "/api/scan/job",
                            "/api/scan/universe",
                            "/api/ranking-lab",
                            "/api/weight-lab",
                            "/api/backtest",
                            "/api/strategy",
                            "/api/historical-backtest",
                            "/api/price-backtest",
                            "/api/edge-backtest",
                            "/debug/caches",
                        ],
                    },
                )
                return
            self.send_json(200, data)
        except ValueError as exc:
            self.send_json(422, {"error": str(exc), "read_only": True})
        except Exception as exc:
            self.send_json(
                500,
                {
                    "error": "Local research service failed unexpectedly. Check its console output.",
                    "detail": str(exc),
                    "read_only": True,
                },
            )


    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("Invalid JSON body")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        def pget(name, default=None):
            values = params.get(name)
            if not values:
                return default
            return values[-1]

        try:
            if parsed.path == "/api/backtest":
                action = (pget("action") or "").lower()
                body = self._read_json_body()
                if action in {"ingest-scan", "ingest_scan"}:
                    picks = body.get("picks") if isinstance(body, dict) else None
                    if not isinstance(picks, list):
                        picks = body if isinstance(body, list) else []
                    data = cluster_backtest.ingest_scan_snapshot(
                        picks,
                        mode=(body.get("mode") if isinstance(body, dict) else None)
                        or (pget("mode") or "short"),
                        feed=(body.get("feed") if isinstance(body, dict) else None)
                        or pget("feed")
                        or local_settings()[2],
                        cluster_exp=(body.get("cluster_exp") if isinstance(body, dict) else None)
                        or pget("cluster_exp")
                        or "nearest",
                        meta=(body.get("meta") if isinstance(body, dict) else None) or {},
                    )
                    status = 400 if data.get("error") else 200
                    self.send_json(status, data)
                    return
                self.send_json(400, {"error": f"Unknown backtest POST action: {action or '(none)'}"})
                return
            self.send_json(404, {"error": "Not found", "routes": ["/api/backtest?action=ingest-scan"]})
        except ValueError as exc:
            self.send_json(422, {"error": str(exc), "read_only": True})
        except Exception as exc:
            self.send_json(
                500,
                {
                    "error": "Local research service failed unexpectedly. Check its console output.",
                    "detail": str(exc),
                    "read_only": True,
                },
            )


def main():
    host, port = "127.0.0.1", int(os.getenv("CIPHER_CORE_PORT", "8282"))
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Cipher Core running at http://{host}:{port} (read-only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
