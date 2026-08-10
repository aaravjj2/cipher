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
import uuid
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
import disk_cache
from scanner import SCAN_UNIVERSE, UNIVERSE_META, get_scan_job, run_scan, start_scan_job
import weight_lab
import cluster_backtest
import ranking_lab
import session_levels
import evidence_status
import backtest_jobs
import holdings
import ask_cipher
import chat_jobs
import workspace_layouts
from zoneinfo import ZoneInfo

# Exchange local time. Session boundaries and trading dates are ET facts, not UTC
# ones — see the prior_close note in quote().
ET_ZONE = ZoneInfo("America/New_York")
from exposure import (
    number, parse_contract, gex, vex, model_gamma, model_vanna, oi_is_proxy,
    iv_is_ill_conditioned,
    profile_summary, classify_aggressor, premium_tier,
    _depth_is_full_chain, _depth_to_points,
    _clamp_expiration_count, _matrix_chain_pages, _matrix_oi_horizon_days,
    MAX_MATRIX_EXPIRATIONS, DEFAULT_MATRIX_EXPIRATIONS,
)

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
DATA = "https://data.alpaca.markets"
CONTRACTS = "https://paper-api.alpaca.markets/v2/options/contracts"

# core/flash_agentic_live_loop.py writes here — a continuous background loop that
# captures the real AccessObsidian Flash Agentic panel via Kimi WebBridge (browser
# automation on the user's own logged-in session) and records newly spotted signals.
FLASH_AGENTIC_DATA_DIR = ROOT / "data" / "flash_agentic"
FLASH_AGENTIC_LIVE_STATUS = FLASH_AGENTIC_DATA_DIR / "live_status.json"
ACCESSOBSIDIAN_SCANS_DIR = ROOT / "data" / "accessobsidian_scans"


def _latest_flash_agentic_capture() -> dict | None:
    """Most recently captured flash_agentic.json under data/accessobsidian_scans/."""
    if not ACCESSOBSIDIAN_SCANS_DIR.is_dir():
        return None
    candidates = list(ACCESSOBSIDIAN_SCANS_DIR.glob("*/*/flash_agentic.json"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    payload["_captured_file_mtime"] = latest.stat().st_mtime
    return payload


def _flash_agentic_live_status() -> dict | None:
    if not FLASH_AGENTIC_LIVE_STATUS.is_file():
        return None
    try:
        return json.loads(FLASH_AGENTIC_LIVE_STATUS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

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


def _open_prospective_registrations() -> list[dict]:
    """Prospective tests not yet closed — registered, running, or awaiting locked
    analysis. PASSED/FAILED/CLOSED are settled, not open commitments."""
    registry_path = ROOT / "data" / "governance" / "research_registry.sqlite"
    if not registry_path.exists():
        return []
    uri = f"file:{registry_path.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "select prospective_test_id, strategy_id, minimum_sample, scored_count, "
                "status, created_at, updated_at from prospective_tests "
                "where status not in ('PASSED', 'FAILED', 'CLOSED') "
                "order by created_at desc"
            ).fetchall()
            names = {
                str(r["strategy_id"]): r["name"]
                for r in db.execute("select strategy_id, name from strategies").fetchall()
            }
    except sqlite3.Error:
        return []
    return [
        {
            "prospective_test_id": row["prospective_test_id"],
            "strategy_id": row["strategy_id"],
            "name": names.get(row["strategy_id"], row["strategy_id"]),
            "status": row["status"],
            "minimum_sample": row["minimum_sample"],
            "scored_count": row["scored_count"],
            "progress_pct": (
                round(min(100.0, 100.0 * row["scored_count"] / row["minimum_sample"]), 1)
                if row["minimum_sample"] else None
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def _open_shadow_positions() -> list[dict]:
    """Open SHADOW_OPEN rows from the paper executor. No live orders are ever
    placed; SHADOW is this system's default and only currently-used mode."""
    try:
        import paper_executor.config as pe_config
        db_path = pe_config.ExecutorConfig().database_path
    except Exception:
        return []
    if not Path(db_path).exists():
        return []
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "select id, ticker, direction, symbol, quantity, entry_price, "
                "opened_at, status from paper_positions where status = 'SHADOW_OPEN' "
                "order by opened_at desc"
            ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def standing_status():
    """What is currently open and how far each accrual clock has run — no P&L,
    no headline number. Every row here is read straight from the table the rest
    of the system already writes; nothing is computed for display."""
    return {
        "as_of": utcnow(),
        "read_only": True,
        "prospective_registrations": _open_prospective_registrations(),
        "shadow_positions": _open_shadow_positions(),
        "clocks": evidence_status.status().get("clocks", []),
    }


def _list_strategies_for_chat(family: str | None = None) -> dict:
    """Catalog metadata for Ask Cipher's list_strategies tool — separate from
    the /api/strategies?action=list GET handler because that route has no
    family filter and this one needs one; duplicating the small dict-shape
    rather than adding an unused parameter to an already-shipped route."""
    import strategy_catalog as _catalog

    specs = _catalog.CATALOG.values()
    if family:
        specs = [s for s in specs if s.family == family]
    return {
        "summary": _catalog.summary(),
        "strategies": [
            {
                "strategy_id": spec.strategy_id,
                "name": spec.name,
                "family": spec.family,
                "source": spec.source,
                "data_requirement": spec.data_requirement,
                "bar_timeframe": spec.bar_timeframe,
                "evaluable": spec.evaluable,
                "blocked_reason": spec.blocked_reason,
            }
            for spec in specs
        ],
    }


def research_status():
    """Return the consolidated, read-only end-state and recent event evidence."""

    status_path = ROOT / "data" / "governance" / "master_end_state_status.json"
    event_path = ROOT / "data" / "events" / "latest_public_event_ingestion.json"
    cache_path = ROOT / "data" / "cache" / "public_event_summary.json"
    if not status_path.is_file():
        return {
            "initialized": False,
            "read_only": True,
            "live_execution_present": False,
            "message": "Run scripts/update_master_end_state_status.py to initialize the operator status.",
            "as_of": utcnow(),
        }
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "initialized": False,
            "read_only": True,
            "live_execution_present": False,
            "error": f"invalid master status artifact: {type(exc).__name__}",
            "as_of": utcnow(),
        }
    events = []
    if event_path.is_file():
        try:
            event_payload = json.loads(event_path.read_text(encoding="utf-8"))
            events = [item.get("record", {}) for item in event_payload.get("processed_events", [])][-50:]
        except (OSError, json.JSONDecodeError):
            events = []
    event_summary = {}
    if cache_path.is_file():
        try:
            event_summary = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            event_summary = {}
    return {
        "initialized": True,
        "status": status,
        "event_summary": event_summary,
        "recent_events": events,
        "read_only": True,
        "live_execution_present": False,
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


DEFAULT_SCAN_WORKERS = 4
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF_SECONDS = 1.5


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
    # Alpaca answers 429 when the account's request budget is exceeded, which a long
    # Setup Scanner run can reach. Previously a single 429 aborted the whole call and
    # the ticker was recorded as a hard error, so a transient throttle silently cost
    # results. Honour Retry-After when present, otherwise back off exponentially.
    last_exc = None
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            with urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_exc = exc
            if exc.code == 429 and attempt < RATE_LIMIT_RETRIES - 1:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after else RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
                except (TypeError, ValueError):
                    delay = RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
                time.sleep(min(delay, 20.0))
                continue
            detail = (
                "the selected feed may require an eligible subscription"
                if exc.code == 403
                else "rate limited — retries exhausted"
                if exc.code == 429
                else "check symbol and configured market-data access"
            )
            raise ValueError(f"Alpaca market-data request failed (HTTP {exc.code}); {detail}.") from exc
        except URLError as exc:
            raise ValueError("Unable to reach Alpaca market data. Check the network and retry.") from exc
    raise ValueError(f"Alpaca market-data request failed (HTTP {getattr(last_exc, 'code', '?')}).")


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
        # Same 429 backoff as alpaca() — this endpoint is hit once per ticker during a
        # scan and was the one that actually threw HTTP 429 under load.
        raw = None
        for attempt in range(RATE_LIMIT_RETRIES):
            try:
                with urlopen(request, timeout=45) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code == 429 and attempt < RATE_LIMIT_RETRIES - 1:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        delay = float(retry_after) if retry_after else RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
                    except (TypeError, ValueError):
                        delay = RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
                    time.sleep(min(delay, 20.0))
                    continue
                raise ValueError(f"Alpaca option-contract metadata failed (HTTP {exc.code}).") from exc
            except URLError as exc:
                raise ValueError("Unable to reach Alpaca option-contract metadata. Check the network and retry.") from exc
        if raw is None:
            break
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
            # Trading date in EXCHANGE time, not UTC. A daily bar is stamped
            # 04:00Z (midnight ET), so between 20:00 ET and midnight ET the UTC
            # date has already rolled forward while the session has not: the
            # "is the newest bar today?" test failed and prior_close fell back to
            # TODAY's own close. NVDA then read -0.08% after the bell against the
            # real product's +2.20%, because it was comparing today's price to
            # today's close instead of yesterday's.
            today_et = datetime.now(ET_ZONE).date()

            def bar_date(bar):
                try:
                    stamp = datetime.fromisoformat(str(bar.get("time")).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    return None
                return stamp.astimezone(ET_ZONE).date()

            latest_date = bar_date(closes[-1])
            if len(closes) >= 2 and latest_date == today_et:
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
        # 60s matches the real product's own "server cache 60s" footer. The chain is
        # the expensive call (multi-page snapshot fetch), and a 580-ticker Setup Scanner
        # run previously blew through the old 15s window before it could reuse anything.
        if not force and cached and time.time() - cached[0] < 60:
            return deepcopy(cached[1])
    # Fall back to the disk cache before paying for the network. This is what lets a
    # restart (or a second scan soon after the first) start warm instead of cold.
    disk_key = "chain|" + "|".join(str(part) for part in cache_key)
    if not force:
        spilled = disk_cache.get(disk_key, ttl=60)
        if spilled is not None:
            with _CACHE_LOCK:
                CHAIN_CACHE[cache_key] = (time.time(), spilled)
            return deepcopy(spilled)

    # Push the expiration window into the snapshot request instead of downloading the
    # whole chain and filtering client-side. These args used to be forwarded only to
    # option_open_interest(), so every caller pulled every listed contract out to the
    # furthest LEAP: SPY came back as 14,572 contracts over 15 pages (~8.0s) when the
    # matrix needed a couple of weeks of expirations. Measured on SPY: unfiltered
    # 7.96s/15 pages, +200d 5.98s/11, +60d 4.52s/8, +14d 2.02s/5.
    # (Deliberately NOT filtering by strike_price_gte/lte — probing showed that makes
    # the request markedly slower, 7.66s vs 0.55s for the same page.)
    window = {}
    if expiration_gte:
        window["expiration_date_gte"] = expiration_gte
    if expiration_lte:
        window["expiration_date_lte"] = expiration_lte

    def pull(active_feed):
        pages = []
        query = {"feed": active_feed, "limit": 1000, **window}
        for _ in range(max(1, int(max_pages))):
            raw = alpaca(f"/v1beta1/options/snapshots/{ticker.upper()}", query)
            pages.append(raw)
            token = raw.get("next_page_token")
            if not token:
                break
            query = {"feed": active_feed, "limit": 1000, "page_token": token, **window}
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
    disk_cache.put(disk_key, contracts)
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
        if not force and cached and time.time() - cached[0] < 60:
            return deepcopy(cached[1])
    context = quote(ticker)
    spot = context["price_context"]
    if spot is None:
        raise ValueError("No usable underlying price is available to center the matrix.")
    depth_pts = _depth_to_points(spot, depth)
    full_chain = _depth_is_full_chain(depth)
    # Constrain the chain/OI queries to the expiration range the matrix actually needs.
    # A tight first window keeps the snapshot fetch small (see option_chain), but a
    # ticker with only monthly expirations can legitimately need months of calendar to
    # supply `expiration_count` of them — so widen and retry if the tight window came
    # up short rather than silently returning fewer columns than requested.
    today = datetime.now(timezone.utc).date()
    oi_gte = today.isoformat()
    wide_days = _matrix_oi_horizon_days(expiration_count)
    tight_days = min(wide_days, max(10, expiration_count * 4))

    def _fetch(days):
        lte = (today + timedelta(days=days)).isoformat()
        got = option_chain(
            ticker, feed, force=force, max_pages=chain_pages,
            expiration_gte=oi_gte, expiration_lte=lte,
        )
        return [c for c in got if c["expiry"] and c["expiry"] >= oi_gte], lte

    contracts, oi_lte = _fetch(tight_days)
    if tight_days < wide_days and len({c["expiry"] for c in contracts}) < expiration_count:
        contracts, oi_lte = _fetch(wide_days)
    # (_fetch above already drops contracts that expired earlier today. Alpaca keeps
    # same-day expiries in the snapshot after the close, and an expired "nearest"
    # expiration carries no live GEX/OI, which used to make Flash/Flash Index return
    # empty for SPY/QQQ/IWM after hours.)
    used_feed = contracts[0]["feed"] if contracts else feed
    all_expirations = sorted({c["expiry"] for c in contracts if c["expiry"]})
    total_expirations_available = len(all_expirations)
    expirations = all_expirations[:expiration_count]
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
            "gamma_modeled": False,
            "iv_min_tick": False,
            "oi_from_volume": False,
            "available": False,
        }
        for c in contracts
        if c["expiry"] in expirations and c["strike"] in strikes
    }
    missing_gamma = 0
    oi_assumed_zero = 0

    # one-element lists so the per-contract loop below can mutate them
    gamma_modeled_contracts = [0]
    oi_proxy_contracts = [0]
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
        # Provenance: a cell whose exposure leans on a reconstructed gamma or a
        # volume-for-OI substitution is weaker evidence than one built from feed
        # greeks and real open interest, and should not read as equally solid.
        if number(contract.get("gamma")) is None:
            cell["gamma_modeled"] = True
            gamma_modeled_contracts[0] += 1
        if oi_is_proxy(contract):
            cell["oi_from_volume"] = True
            oi_proxy_contracts[0] += 1
        if iv_is_ill_conditioned(contract):
            cell["iv_min_tick"] = True
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
                    "gamma_modeled": bool(cell.get("gamma_modeled")),
                    "oi_from_volume": bool(cell.get("oi_from_volume")),
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
        "total_expirations_available": total_expirations_available,
        "rows": rows,
        "formula": "Gamma × open interest × 100 × spot² × 0.01; puts receive a negative sign. Null OI remains unknown.",
        "caveat": "This is a public-OI heuristic, not verified dealer positioning.",
        "coverage": {
            "contracts": len(contracts),
            "contracts_missing_gamma": missing_gamma,
            # Back-compat alias for older UI/debug consumers.
            "contracts_missing_gamma_or_oi": missing_gamma,
            "contracts_oi_assumed_zero": oi_assumed_zero,
            # Provenance counters — how much of this grid is reconstructed rather
            # than taken straight from the feed. See exposure.resolve_iv/contract_size.
            "contracts_gamma_modeled": gamma_modeled_contracts[0],
            "contracts_oi_from_volume": oi_proxy_contracts[0],
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

    # Prior-session and extended-hours levels. The real product draws these on every
    # chart next to the exposure levels and treats them as reaction zones; nothing
    # here produced them before. Failures are non-fatal — a missing bar feed should
    # cost the session lines, not the whole Night Vision payload.
    try:
        minute = bars(ticker, "5m", limit=1000).get("bars") or []
        daily = bars(ticker, "1d", limit=30).get("bars") or []
        payload["session_levels"] = session_levels.compute(minute, daily_bars=daily)
        premarket_pct = session_levels.premarket_range_pct(minute)
        payload["premarket_range_pct"] = (
            round(premarket_pct, 4) if premarket_pct is not None else None
        )
    except Exception as exc:  # noqa: BLE001 - bar feed is optional context here
        payload["session_levels"] = {"levels": [], "note": f"unavailable: {exc}"}
        payload["premarket_range_pct"] = None

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


def bars(ticker, timeframe, limit=200, start=None):
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
    start_override = None
    if start:
        try:
            start_override = datetime.fromisoformat(str(start)).replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError(f"invalid start date: {start!r}") from None
    cache_key = (ticker.upper(), normalized, want, start_override.date().isoformat() if start_override else None)
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
    # An explicit `start` (e.g. "give me bars since this position's entry date") means
    # the caller wants the OLDEST bars in that window, not the newest — skip the
    # newest-`want`-only slice below for this case, since holdings-style benchmark
    # charts need the bars nearest the start date, not nearest now.
    start = start_override or (end - timedelta(days=calendar_days))
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
            # With an explicit start override, keep every bar from that date forward
            # (bounded by Alpaca's own pagination above) rather than truncating to the
            # newest `want` — the earliest bars are exactly what a since-a-past-date
            # comparison needs, and daily bars for even a decade-old date are a few
            # thousand rows at most.
            output = collected if start_override else collected[-want:]
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


def occ_symbol(ticker, expiration, strike, option_type):
    """Build an OCC option symbol, the inverse of exposure.parse_contract()."""
    date = datetime.fromisoformat(expiration).strftime("%y%m%d")
    kind = "C" if str(option_type).lower().startswith("c") else "P"
    return f"{ticker.upper()}{date}{kind}{int(round(float(strike) * 1000)):08d}"


def contract_search(ticker, feed, strike, option_type, expiration=None, trade_date=None):
    """Every trade in one contract for one session, split into bought vs sold.

    Backs Spyglass's Contract Search, which previously had no backend at all: the
    view rendered its inputs and an empty state because no capture had shown what
    the real product returns.

    The buy/sell split is INFERRED, and the reason matters. Alpaca serves a full
    options trade tape (/v1beta1/options/trades) with price, size and timestamp, but
    it serves no historical options *quote* tape — /v1beta1/options/quotes returns
    404 on this account, and snapshots carry only the current bid/ask. Classifying a
    09:31 trade against a 16:00 quote would be worse than useless, so side comes
    from the tick rule instead: a trade above the previous trade is a buy, below is
    a sell, and an unchanged price inherits the last non-flat direction. That is a
    standard microstructure inference, not exchange-reported side, and the response
    labels it as such rather than presenting it as fact.
    """
    contracts = option_chain(ticker, resolve_options_feed(feed))
    strike = float(strike)
    kind = "call" if str(option_type).lower().startswith("c") else "put"
    matches = [
        c for c in contracts
        if c.get("type") == kind and number(c.get("strike")) is not None
        and abs(number(c["strike"]) - strike) < 1e-6
        and (expiration is None or c.get("expiry") == expiration)
    ]
    if not matches:
        available = sorted({number(c["strike"]) for c in contracts
                            if c.get("type") == kind and number(c.get("strike")) is not None})
        near = sorted(available, key=lambda s: abs(s - strike))[:8]
        return {
            "ticker": ticker.upper(), "strike": strike, "type": kind,
            "expiration": expiration, "found": False,
            "nearest_strikes": sorted(near),
            "message": f"No listed {kind} at strike {strike} for {ticker.upper()}.",
        }
    # Without an expiration the nearest-dated listing is the sensible default, which
    # is also the one a trader typing just a strike almost always means.
    matches.sort(key=lambda c: c.get("expiry") or "")
    chosen = matches[0]
    symbol = chosen.get("symbol") or occ_symbol(ticker, chosen["expiry"], strike, kind)

    day = trade_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    query = {"symbols": symbol, "limit": 10000,
             "start": f"{day}T00:00:00Z", "end": f"{day}T23:59:59Z"}
    trades, token, pages = [], None, 0
    while pages < 10:
        if token:
            query["page_token"] = token
        payload = alpaca("/v1beta1/options/trades", query)
        trades.extend((payload.get("trades") or {}).get(symbol) or [])
        token = payload.get("next_page_token")
        pages += 1
        if not token:
            break

    buy_vol = sell_vol = flat_vol = 0
    buy_prem = sell_prem = 0.0
    direction = 0
    prev_price = None
    largest = []
    for t in trades:
        price = number(t.get("p"))
        size = int(t.get("s") or 0)
        if price is None or size <= 0:
            continue
        if prev_price is not None:
            if price > prev_price:
                direction = 1
            elif price < prev_price:
                direction = -1
            # equal price keeps the previous direction (zero-tick rule)
        prev_price = price
        premium = price * size * 100.0
        if direction > 0:
            buy_vol += size
            buy_prem += premium
        elif direction < 0:
            sell_vol += size
            sell_prem += premium
        else:
            flat_vol += size
        largest.append({"time": t.get("t"), "price": price, "size": size,
                        "premium": round(premium, 2),
                        "side": "buy" if direction > 0 else "sell" if direction < 0 else "unknown",
                        "exchange": t.get("x")})
    largest.sort(key=lambda r: -r["premium"])
    total_vol = buy_vol + sell_vol + flat_vol
    total_prem = sum(r["premium"] for r in largest)

    return {
        "ticker": ticker.upper(),
        "symbol": symbol,
        "strike": strike,
        "type": kind,
        "expiration": chosen.get("expiry"),
        "trade_date": day,
        "found": True,
        "trades": len(largest),
        "volume": total_vol,
        "buy_volume": buy_vol,
        "sell_volume": sell_vol,
        "unclassified_volume": flat_vol,
        "buy_pct": round(100.0 * buy_vol / total_vol, 1) if total_vol else None,
        "premium": round(total_prem, 2),
        "buy_premium": round(buy_prem, 2),
        "sell_premium": round(sell_prem, 2),
        "vwap": round(total_prem / (total_vol * 100.0), 4) if total_vol else None,
        # Snapshot values, for context against the day's tape.
        "open_interest": number(chosen.get("open_interest")),
        "bid": number(chosen.get("bid")),
        "ask": number(chosen.get("ask")),
        "last": number(chosen.get("last")),
        "largest_trades": largest[:25],
        "expirations_available": sorted({c.get("expiry") for c in matches if c.get("expiry")}),
        "method": "tick rule (Lee-Ready style): trade above the previous trade counts "
                  "as bought, below as sold, unchanged inherits the last direction",
        "caveat": "Buy/sell side is inferred from trade-price direction, not reported "
                  "by the exchange. Alpaca serves no historical options quote tape, so "
                  "trades cannot be matched against the bid/ask that prevailed at the "
                  "time. Treat the split as an estimate.",
    }


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


# Curated pharma/biotech/medtech universe for the Bio flow scan — no GICS/sector feed is
# wired into this service, so this is a hand-picked list (cross-checked against names
# actually surfaced in a real Bio scan capture: AMGN, BSX, UTHR, INSM, BMY, LLY, MRNA,
# ISRG, RMD, SYK, REGN, ABBV), not an exhaustive or auto-derived sector universe.
BIO_UNIVERSE = [
    "JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY", "AMGN", "GILD", "BIIB", "REGN",
    "VRTX", "ISRG", "MDT", "SYK", "BSX", "EW", "ZBH", "MRNA", "BNTX", "DXCM",
    "PODD", "TMO", "DHR", "A", "IQV", "ILMN", "BAX", "BDX", "ZTS", "IDXX",
    "ALGN", "HOLX", "MOH", "BMRN", "ALNY", "SRPT", "IONS", "NBIX", "INCY", "EXEL",
    "BGNE", "ACAD", "AXSM", "TXG", "UTHR", "INSM", "RMD", "CI", "ELV", "HUM",
    "CVS", "CNC", "HCA", "UNH",
]


def flow_bulk(tickers, feed, job_id=None, **filters):
    """Loop flow() across a fixed ticker universe (Bio scan) and merge by premium desc.

    ~1.6s/ticker average measured against BIO_UNIVERSE (54 names) → ~90s total, far past
    a reasonable blocking HTTP request. job_id, if given, reports progress the same way
    scanner.py's run_scan() does for the Setup Scanner's async jobs.
    """
    all_prints = []
    errors = []
    for i, ticker in enumerate(tickers):
        try:
            result = flow(ticker, feed, **filters)
            all_prints.extend(result.get("prints") or [])
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
        if job_id:
            _update_bio_job(job_id, done=i + 1, total=len(tickers))
    all_prints.sort(key=lambda item: item.get("premium") or 0, reverse=True)
    return {
        "as_of": utcnow(),
        "feed": resolve_options_feed(feed),
        "universe_size": len(tickers),
        "names_with_prints": len({p["ticker"] for p in all_prints}),
        "min_premium": float(filters.get("min_premium") or 5000),
        "count": len(all_prints),
        "prints": all_prints[:400],
        "errors": errors,
        "caveat": "Aggressor side is inferred from trade vs bid/ask; not a verified buyer/seller label.",
    }


_BIO_JOBS: dict = {}
_BIO_JOB_LOCK = threading.Lock()


def _update_bio_job(job_id, **fields):
    with _BIO_JOB_LOCK:
        job = _BIO_JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        if "done" in fields and "total" in fields and fields["total"]:
            job["pct"] = int(100 * fields["done"] / fields["total"])
            job["message"] = f"Scanning pharma, biotech & medtech… {fields['done']}/{fields['total']} names"


def start_bio_job(feed, **filters):
    job_id = uuid.uuid4().hex[:12]
    with _BIO_JOB_LOCK:
        _BIO_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "done": 0,
            "total": len(BIO_UNIVERSE),
            "pct": 0,
            "message": "Queued…",
            "result": None,
            "error": None,
            "started_at": utcnow(),
        }

    def _worker():
        _update_bio_job(job_id, status="running")
        try:
            result = flow_bulk(BIO_UNIVERSE, feed, job_id=job_id, **filters)
            _update_bio_job(job_id, status="done", pct=100, result=result, message="Scan complete")
        except Exception as exc:
            _update_bio_job(job_id, status="error", error=str(exc), message=str(exc))

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def get_bio_job(job_id):
    with _BIO_JOB_LOCK:
        job = _BIO_JOBS.get(job_id)
        return deepcopy(job) if job else None


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
        """Stream a backtest_jobs job's progress. Not ticker data — every panel
        already polls quote/night_vision directly, so re-serving them here on a
        timer held a thread per connection for nothing. A 24+ strategy catalog
        sweep takes minutes; this makes it watchable.
        """
        def pget(name, default=None):
            values = params.get(name)
            if not values:
                return default
            return values[-1]

        job_id = pget("job") or pget("id")
        source = chat_jobs if pget("type") == "chat" else backtest_jobs
        self.send_sse_headers()
        if not job_id:
            self.write_event("error", {"error": "missing job id (?job=<id>)"})
            return
        if not self.write_event("hello", {"job_id": job_id}):
            return
        sent = 0
        try:
            while True:
                job = source.get_job(job_id)
                if job is None:
                    self.write_event("error", {"error": "unknown job", "job_id": job_id})
                    return
                events = job.get("events") or []
                for event in events[sent:]:
                    event_name = "blocked" if event.get("verdict") == "BLOCKED" else event["type"]
                    if not self.write_event(event_name, event):
                        return
                sent = len(events)
                status = job.get("status")
                if status in ("done", "error"):
                    if not self.write_event("complete", {
                        "job_id": job_id,
                        "status": status,
                        "verdicts": (job.get("result") or {}).get("verdicts"),
                        "error": job.get("error"),
                    }):
                        return
                    return
                if not self.write_event("heartbeat", {"ts": utcnow(), "pct": job.get("pct")}):
                    return
                time.sleep(1)
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
            elif parsed.path == "/api/standing":
                data = standing_status()
            elif parsed.path == "/api/signal-backtest":
                action = (pget("action") or "status").lower()
                if action == "start":
                    tickers = [t for t in (pget("symbols") or "").split(",") if t.strip()]
                    job_id = backtest_jobs.start_backtest_job(
                        mode=(pget("mode") or "filter").lower(),
                        symbols=tickers or None,
                        timeframe=pget("timeframe", "15Min"),
                        years=float(pget("years", "1") or 1),
                        detector_mode=pget("detector", "EOD Focus"),
                        lookback_bars=int(pget("lookback", "6") or 6),
                        entry_every=int(pget("entry_every", "12") or 12),
                        control_repeats=int(pget("repeats", "20") or 20),
                    )
                    data = {"job_id": job_id, "status": "queued"}
                elif action == "list":
                    data = {"jobs": backtest_jobs.list_jobs()}
                else:
                    job_id = pget("id")
                    job = backtest_jobs.get_job(job_id) if job_id else None
                    if job is None:
                        self.send_json(404, {"error": "Unknown backtest job"})
                        return
                    data = job
            elif parsed.path == "/api/evidence-status":
                data = evidence_status.status()
            elif parsed.path == "/api/research-status":
                data = research_status()
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
                data = bars(
                    ticker, pget("timeframe", "5m"), limit=int(pget("limit", "200")),
                    start=pget("start"),
                )
            elif parsed.path == "/api/holdings":
                data = holdings.holdings_status(
                    quote_fn=quote, bars_fn=bars,
                    include_benchmark=(pget("benchmark") or "").lower() in {"1", "true", "yes"},
                )
            elif parsed.path == "/api/workspace-layouts":
                name = pget("name")
                data = (
                    workspace_layouts.get_layout(name)
                    if name
                    else workspace_layouts.layouts_status()
                )
            elif parsed.path == "/api/contract-search":
                data = contract_search(
                    ticker,
                    feed,
                    pget("strike"),
                    pget("type", "call"),
                    expiration=pget("expiration") or None,
                    trade_date=pget("date") or None,
                )
            elif parsed.path in {"/api/flow", "/api/spyglass"}:
                max_price = pget("pmax")
                flow_kwargs = dict(
                    min_premium=float(pget("min") or pget("premium") or "5000"),
                    max_price=float(max_price) if max_price not in (None, "", "all") else None,
                    option_type=str(pget("type", "all")).lower(),
                    side=str(pget("side", "all")).lower(),
                    moneyness=str(pget("money") or pget("moneyness") or "all").lower(),
                    force=str(pget("fresh", "0")).lower() in {"1", "true", "yes"},
                )
                if str(pget("sector", "")).lower() == "bio":
                    if str(pget("async", "0")).lower() in {"1", "true", "yes"}:
                        job_id = start_bio_job(feed, **flow_kwargs)
                        data = {"job_id": job_id, "status": "queued", "universe_size": len(BIO_UNIVERSE)}
                    else:
                        data = flow_bulk(BIO_UNIVERSE, feed, **flow_kwargs)
                else:
                    data = flow(ticker, feed, **flow_kwargs)
            elif parsed.path == "/api/flow/job":
                job_id = pget("id")
                job = get_bio_job(job_id) if job_id else None
                if not job:
                    self.send_json(404, {"error": "Unknown bio flow job"})
                    return
                data = job
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
                # Bounded fan-out; run_scan clamps to SCAN_MAX_WORKERS. Safe now that
                # the chain fetch is expiration-windowed and 429s are retried with
                # backoff. `workers` is overridable per-request for A/B testing.
                try:
                    workers = int(pget("workers", str(DEFAULT_SCAN_WORKERS)))
                except (TypeError, ValueError):
                    workers = DEFAULT_SCAN_WORKERS
                # Flash strategies use bars_fn for a real session VWAP; the small
                # flash universe (12-13 tickers) makes the extra per-ticker bars
                # fetch cheap, unlike the 580-ticker cluster/cipher scans.
                flash_bars_fn = bars if strategy in {"flash", "flash_index", "flash_agentic"} else None
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
                        bars_fn=flash_bars_fn,
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
                        bars_fn=flash_bars_fn,
                    )
            elif parsed.path == "/api/scan/job":
                job_id = pget("id")
                job = get_scan_job(job_id) if job_id else None
                if not job:
                    self.send_json(404, {"error": "Unknown scan job"})
                    return
                data = job
            elif parsed.path == "/api/scan/history":
                import scan_history

                scan_id = pget("id")
                if scan_id:
                    loaded = scan_history.load_scan(scan_id)
                    if not loaded:
                        self.send_json(404, {"error": "Unknown saved scan"})
                        return
                    data = loaded
                else:
                    data = {
                        "scans": scan_history.list_scans(
                            strategy=pget("strategy"),
                            limit=int(pget("limit", "50")),
                        )
                    }
            elif parsed.path == "/api/flash-agentic/live":
                capture = _latest_flash_agentic_capture()
                status = _flash_agentic_live_status()
                loop_running = False
                pid_path = FLASH_AGENTIC_DATA_DIR / "live_loop.pid"
                if pid_path.is_file():
                    try:
                        pid = int(pid_path.read_text(encoding="utf-8").strip())
                        os.kill(pid, 0)
                        loop_running = True
                    except (ValueError, ProcessLookupError, PermissionError, OSError):
                        loop_running = False
                data = {
                    "loop_running": loop_running,
                    # The running loop writes "cycle"; on clean shutdown it writes a
                    # final payload with "cycles" instead, so a stopped loop reported
                    # cycle=None and looked like it had never run.
                    "cycle": (status or {}).get("cycle") or (status or {}).get("cycles"),
                    "loop_status": (status or {}).get("status"),
                    "status_updated_at": (status or {}).get("updated_at"),
                    "captured_at": (capture or {}).get("captured_at"),
                    "rows": (capture or {}).get("rows") or [],
                    "caveat": (
                        "Captured from the real AccessObsidian Flash Agentic panel via browser "
                        "automation on the logged-in session — not independently computed."
                    ),
                }
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
            elif parsed.path == "/api/strategies":
                # One route replaces five. /api/strategy, /api/historical-backtest,
                # /api/price-backtest, /api/edge-backtest and /api/intraday-backtest
                # each ranked strategies with their own scoring half, and those
                # halves charged no transaction cost, truncated chronologically, or
                # (for the GEX family) read today's open interest while trading past
                # bars. Their entry logic now lives in core/strategy_catalog.py and
                # every one of them is measured by core/strategy_evaluation.py
                # against a matched random-entry control.
                action = (pget("action") or "list").lower()
                if action == "list":
                    import strategy_catalog as _catalog
                    data = {
                        "summary": _catalog.summary(),
                        "standard": (
                            "A strategy passes only by beating a random-entry control "
                            "matched trade-for-trade by symbol and direction. Costs are "
                            "charged both sides. Strategies whose data cannot support an "
                            "honest measurement are reported blocked, never scored."
                        ),
                        "strategies": [
                            {
                                "strategy_id": spec.strategy_id,
                                "name": spec.name,
                                "family": spec.family,
                                "source": spec.source,
                                "data_requirement": spec.data_requirement,
                                "bar_timeframe": spec.bar_timeframe,
                                "evaluable": spec.evaluable,
                                "blocked_reason": spec.blocked_reason,
                            }
                            for spec in _catalog.CATALOG.values()
                        ],
                    }
                elif action == "evaluate":
                    tickers = [
                        part.strip().upper()
                        for part in (pget("symbols") or "").replace(";", ",").split(",")
                        if part.strip()
                    ]
                    ids = [
                        part.strip()
                        for part in (pget("strategy_ids") or "").split(",")
                        if part.strip()
                    ]
                    job_id = backtest_jobs.start_catalog_job(
                        strategy_ids=ids or None,
                        family=pget("family") or None,
                        symbols=tickers or None,
                        timeframe=pget("timeframe", "1Day"),
                        years=float(pget("years", "5") or 5),
                        control_repeats=int(pget("repeats", "20") or 20),
                    )
                    data = {"job_id": job_id, "status": "queued"}
                elif action == "jobs":
                    data = {"jobs": backtest_jobs.list_jobs()}
                else:
                    job_id = pget("id")
                    job = backtest_jobs.get_job(job_id) if job_id else None
                    if job is None:
                        self.send_json(404, {"error": "Unknown strategy job"})
                        return
                    data = job
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
                            "/api/standing",
                            "/api/holdings",
                            "/api/workspace-layouts",
                            "/api/ask",
                            "/api/research-status",
                            "/api/evidence-status",
                            "/api/signal-backtest",
                            "/api/matrix",
                            "/api/heatmap",
                            "/api/night-vision",
                            "/api/bars",
                            "/api/flow",
                            "/api/contract-search",
                            "/api/stream",
                            "/api/scan",
                            "/api/scan/job",
                            "/api/scan/universe",
                            "/api/ranking-lab",
                            "/api/weight-lab",
                            "/api/backtest",
                            "/api/strategies",
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
            if parsed.path == "/api/holdings":
                action = (pget("action") or "").lower()
                body = self._read_json_body()
                if not isinstance(body, dict):
                    raise ValueError("request body must be a JSON object")
                if action == "add":
                    data = holdings.add_position(
                        ticker=body.get("ticker", ""),
                        shares=body.get("shares"),
                        entry_price=body.get("entry_price"),
                        entry_date=body.get("entry_date", ""),
                        notes=body.get("notes"),
                    )
                    self.send_json(201, data)
                    return
                if action == "close":
                    data = holdings.close_position(
                        position_id=body.get("id", ""),
                        exit_price=body.get("exit_price"),
                        exit_date=body.get("exit_date", ""),
                        shares=body.get("shares"),
                    )
                    self.send_json(200, data)
                    return
                if action == "delete":
                    data = holdings.delete_position(body.get("id", ""))
                    self.send_json(200, data)
                    return
                self.send_json(400, {"error": f"Unknown holdings POST action: {action or '(none)'}"})
                return
            if parsed.path == "/api/workspace-layouts":
                action = (pget("action") or "").lower()
                body = self._read_json_body()
                if not isinstance(body, dict):
                    raise ValueError("request body must be a JSON object")
                if action == "save":
                    data = workspace_layouts.save_layout(
                        name=body.get("name", ""), layout=body.get("layout"),
                    )
                    self.send_json(200, data)
                    return
                if action == "delete":
                    data = workspace_layouts.delete_layout(body.get("name", ""))
                    self.send_json(200, data)
                    return
                self.send_json(
                    400,
                    {"error": f"Unknown workspace-layouts POST action: {action or '(none)'}"},
                )
                return
            if parsed.path == "/api/ask":
                body = self._read_json_body()
                if not isinstance(body, dict):
                    raise ValueError("request body must be a JSON object")
                message = str(body.get("message") or "").strip()
                if not message:
                    raise ValueError("message is required")
                history = body.get("history") or []
                if not isinstance(history, list):
                    raise ValueError("history must be a list")
                tool_impls = {
                    "get_evidence_status": evidence_status.status,
                    "get_standing": standing_status,
                    "get_holdings": lambda: holdings.holdings_status(quote_fn=quote, bars_fn=bars),
                    "get_quote": quote,
                    "list_strategies": _list_strategies_for_chat,
                }
                job_id = chat_jobs.start_chat_job(message, history, tool_impls)
                self.send_json(202, {"job_id": job_id, "status": "queued"})
                return
            self.send_json(
                404,
                {
                    "error": "Not found",
                    "routes": [
                        "/api/backtest?action=ingest-scan",
                        "/api/holdings?action=add|close|delete",
                        "/api/workspace-layouts?action=save|delete",
                        "/api/ask",
                    ],
                },
            )
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
