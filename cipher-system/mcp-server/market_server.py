#!/usr/bin/env python3
"""Cipher Market MCP — read-only stock/options research over the local cipher-core API.

This is a second, separate MCP server from `server.py`. That one records a browser
research workflow into its own SQLite file and never contacts cipher-core. This one
exposes cipher-core's live read-only `/api/*` surface (quotes, bars, GEX exposure,
Night Vision levels, option-contract tape, headlines, strategy standing) so an MCP host
such as Claude Desktop can do ticker analysis directly.

Two properties are structural, not conventions to be remembered:

*   **Read-only.** `_get` issues HTTP GET only, against an explicit path allowlist. There
    is no POST path in this file, no broker or account endpoint in the allowlist, and no
    tool that can place, size, modify or cancel an order. Cipher is research software;
    its own `/api/health` reports `read_only: true`. The paper-autopilot tools extend
    this property rather than weaken it: one more loopback GET (the executor status
    endpoint, which itself reports `live_execution_capability: false`), and two local
    files read through immutable read-only handles (`mode=ro` SQLite, JSONL tail).

*   **Small results.** `/api/night-vision` and `/api/matrix` return ~730 KB each, which is
    roughly 180k tokens and would exhaust any host's context in a single call. Every tool
    that wraps a large endpoint projects it to the analytically meaningful fields —
    Cipher's own computed walls, gamma flip, peak and session levels — and a hard byte cap
    catches anything unexpected rather than letting it through.

Cipher's data caveats are forwarded verbatim rather than stripped. A null exposure cell
means "no listed or calculable exposure", not zero, and the counts of unavailable cells
travel with the numbers so a reader cannot mistake unknown for empty.

Dependencies: the Python standard library only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("CIPHER_CORE_URL", "http://127.0.0.1:8282").rstrip("/")
TIMEOUT = float(os.environ.get("CIPHER_MCP_TIMEOUT", "45"))

# Paper-executor status surface (loopback only). Separate allowlist from
# cipher-core's: one URL, a GET, and the endpoint itself reports
# live_execution_capability: false.
EXECUTOR_STATUS_URL = os.environ.get(
    "CIPHER_EXECUTOR_URL", "http://127.0.0.1:8787"
).rstrip("/") + "/api/paper/status"

# Local ledgers read through immutable, read-only SQLite/JSONL handles.
PAPER_LEDGER_DB = Path(os.environ.get(
    "CIPHER_PAPER_LEDGER",
    "/home/aarav/Aarav/cipher/runtime/data/paper_runtime/data/paper_trades/autopilot_shadow.sqlite",
))
GEX_HISTORY_DB = Path(os.environ.get(
    "CIPHER_GEX_HISTORY",
    "/home/aarav/Aarav/cipher/runtime/data/gex_history.sqlite",
))
PROSPECTIVE_LOG = Path(os.environ.get(
    "CIPHER_PROSPECTIVE_LOG",
    "/home/aarav/Aarav/cipher/runtime/data/earnings_prospective_log.jsonl",
))

# GET-only allowlist. Adding a path here is the only way to reach cipher-core, and every
# entry below is a read. cipher-core also serves POST routes (/api/backtest, /api/holdings,
# /api/alerts, /api/ask, ...); none is reachable from this file.
ALLOWED_PATHS = frozenset({
    "/api/health",
    "/api/quote",
    "/api/bars",
    "/api/heatmap",
    "/api/night-vision",
    "/api/contract-search",
    "/api/news",
    "/api/strategies",
    "/api/standing",
    "/api/governance",
})

MAX_RESULT_BYTES = 120_000

RESEARCH_NOTICE = (
    "Cipher is research software and read-only. Nothing it returns is a trade "
    "recommendation, an order, or a position instruction, and no tool here can place one."
)


# --------------------------------------------------------------------------- transport

def _core_headers() -> dict[str, str]:
    """Hosted cores require the internal proxy token; guests are fine with it.

    The token is read per call so a credential rotation never needs this
    process to restart. No key material ever appears in a tool result.
    """
    headers = {"Accept": "application/json"}
    token = os.environ.get("CIPHER_INTERNAL_PROXY_TOKEN", "")
    if token:
        headers.update({
            "X-Cipher-Internal-Token": token,
            "X-Cipher-Guest": "1",
            "X-Cipher-User-Id": "guest",
        })
    return headers


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    if path not in ALLOWED_PATHS:
        raise ValueError(f"path is not in the read-only allowlist: {path}")
    query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, method="GET", headers=_core_headers())
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        try:
            parsed = json.loads(detail)
            detail = parsed.get("error") or detail
        except Exception:
            pass
        raise ValueError(f"cipher-core returned HTTP {exc.code} for {path}: {detail}") from None
    except urllib.error.URLError as exc:
        raise ValueError(
            f"cannot reach cipher-core at {BASE_URL} ({exc.reason}). "
            "Is cipher-core.service running, and is CIPHER_CORE_URL correct?"
        ) from None
    return json.loads(body)


# --------------------------------------------------------------------------- projections

def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _strike_totals(strikes: list[Any], grid: list[Any]) -> tuple[list[dict[str, Any]], int, int]:
    """Sum a strikes x expirations grid along expirations, skipping unknown cells.

    A null cell is not a zero. Summing it as one would invent exposure that was never
    measured, so nulls are skipped and counted; the count is reported alongside.
    """
    totals: list[dict[str, Any]] = []
    known = unknown = 0
    for index, strike in enumerate(strikes):
        row = grid[index] if index < len(grid) else []
        acc = 0.0
        seen = 0
        for cell in row or []:
            value = _f(cell)
            if value is None:
                unknown += 1
                continue
            acc += value
            seen += 1
            known += 1
        if seen:
            totals.append({"strike": _f(strike), "net_gex": round(acc, 2), "expirations_counted": seen})
    return totals, known, unknown


def project_gex_levels(payload: dict[str, Any], near: int) -> dict[str, Any]:
    spot = _f(payload.get("spot"))
    strikes = payload.get("strikes") or []
    totals, known, unknown = _strike_totals(strikes, payload.get("gex") or [])
    by_strike = sorted(totals, key=lambda row: abs(row["net_gex"] or 0.0), reverse=True)
    nearest = sorted(totals, key=lambda row: abs((row["strike"] or 0.0) - (spot or 0.0)))[:near]
    nearest.sort(key=lambda row: row["strike"] or 0.0, reverse=True)
    expirations = payload.get("expirations") or []
    by_expiration = payload.get("totals", {}).get("gex_by_expiration") or []
    paired = [
        {"expiration": expiration, "net_gex": round(_f(value) or 0.0, 2)}
        for expiration, value in zip(expirations, by_expiration)
    ]
    paired.sort(key=lambda row: abs(row["net_gex"]), reverse=True)
    return {
        "ticker": payload.get("ticker"),
        "spot": spot,
        "day_change_pct": _f(payload.get("day_change_pct")),
        "updated": payload.get("updated"),
        "contracts": payload.get("contracts"),
        "levels": payload.get("summary"),
        "net_gex_total": round(sum(row["net_gex"] or 0.0 for row in totals), 2),
        "strikes_near_spot": nearest,
        "largest_absolute_gex_strikes": by_strike[:10],
        "largest_absolute_gex_expirations": paired[:10],
        "cell_coverage": {
            "calculable_cells": known,
            "unavailable_cells": unknown,
            "note": "An unavailable cell has no listed or calculable exposure. It is not a zero measurement and was skipped, not summed.",
        },
        "formula": payload.get("formula"),
        "caveat": payload.get("caveat"),
        "research_notice": RESEARCH_NOTICE,
    }


def project_night_vision(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep Cipher's own computed levels and drop the 87x12 exposure grid.

    The grid is what makes the raw response ~730 KB; `summary`, `levels`, `peak`, `xray`
    and `session_levels` are what a reader actually reasons about, and Cipher already
    derives them from that grid.
    """
    return {
        "ticker": payload.get("ticker"),
        "as_of": payload.get("as_of"),
        "feed": payload.get("feed"),
        "quote": payload.get("quote"),
        "levels": payload.get("summary"),
        "peak_exposure": payload.get("peak"),
        "key_levels": payload.get("levels"),
        "xray_strikes": (payload.get("xray") or [])[:20],
        "session_levels": payload.get("session_levels"),
        "premarket_range_pct": payload.get("premarket_range_pct"),
        "expirations": payload.get("expirations"),
        "total_expirations_available": payload.get("total_expirations_available"),
        "depth_points": payload.get("depth_points"),
        "coverage": payload.get("coverage"),
        "formula": payload.get("formula"),
        "caveat": payload.get("caveat"),
        "omitted": "The per-strike x per-expiration exposure grid (~730 KB) is not returned; call get_gex_levels for per-strike totals.",
        "research_notice": RESEARCH_NOTICE,
    }


def project_strategies(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": payload.get("summary"),
        "standard": payload.get("standard"),
        "strategies": [
            {
                key: entry.get(key)
                for key in (
                    "strategy_id", "name", "family", "evaluable",
                    "blocked_reason", "data_requirement", "bar_timeframe",
                )
                if key in entry
            }
            for entry in (payload.get("strategies") or [])
        ],
        "research_notice": RESEARCH_NOTICE,
    }


# --------------------------------------------------------------------------- tools

def tool_specs() -> list[dict[str, Any]]:
    def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        }

    symbol = {"type": "string", "description": "Ticker, e.g. SPY or NVDA."}
    return [
        {
            "name": "cipher_health",
            "description": "Check that the local cipher-core research service is reachable and which data feeds it is configured for.",
            "inputSchema": schema({}),
        },
        {
            "name": "get_quote",
            "description": "Current bid/ask/mid/last, prior close and day change for one ticker, from the SIP feed.",
            "inputSchema": schema({"symbol": symbol}, ["symbol"]),
        },
        {
            "name": "get_bars",
            "description": "Historical OHLCV bars for one ticker.",
            "inputSchema": schema({
                "symbol": symbol,
                "timeframe": {"type": "string", "description": "e.g. 1Min, 5Min, 15Min, 1Hour, 1Day. Defaults to 1Day.", "default": "1Day"},
                "limit": {"type": "integer", "description": "Number of bars, 1-500. Defaults to 30.", "default": 30, "minimum": 1, "maximum": 500},
            }, ["symbol"]),
        },
        {
            "name": "get_gex_levels",
            "description": "Gamma exposure structure for one ticker: call wall, put wall, gamma flip level, net GEX, the strikes nearest spot and the largest absolute-exposure strikes and expirations. Condensed from a ~200 KB grid.",
            "inputSchema": schema({
                "symbol": symbol,
                "strikes_near_spot": {"type": "integer", "description": "How many strikes around spot to return. Defaults to 12.", "default": 12, "minimum": 1, "maximum": 40},
            }, ["symbol"]),
        },
        {
            "name": "get_night_vision",
            "description": "Night Vision view for one ticker: quote, gamma walls and flip, peak exposure, key levels above and below spot, previous-day/week and pre/post-market session levels. Condensed from a ~730 KB payload.",
            "inputSchema": schema({"symbol": symbol}, ["symbol"]),
        },
        {
            "name": "search_contract",
            "description": "Every trade in one option contract for one session, split into bought versus sold by the tick rule, with volume, premium, VWAP, open interest and the largest prints. The buy/sell split is an inference, not exchange-reported side.",
            "inputSchema": schema({
                "symbol": symbol,
                "strike": {"type": "number", "description": "Strike price. Required."},
                "option_type": {"type": "string", "enum": ["call", "put"], "default": "call"},
                "expiration": {"type": "string", "description": "YYYY-MM-DD. Defaults to the nearest expiration."},
                "date": {"type": "string", "description": "Session to read, YYYY-MM-DD. Defaults to the current session."},
            }, ["symbol", "strike"]),
        },
        {
            "name": "get_news_headlines",
            "description": "Recent headlines for one ticker, straight from Yahoo Finance's public RSS. Cipher does not score, rank or summarise them and derives no signal from them.",
            "inputSchema": schema({
                "symbol": symbol,
                "limit": {"type": "integer", "description": "How many headlines, 1-50. Defaults to 10.", "default": 10, "minimum": 1, "maximum": 50},
            }, ["symbol"]),
        },
        {
            "name": "list_strategies",
            "description": "Cipher's researched strategies with the standard each must beat (a trade-for-trade random-entry control with costs charged both sides), and which are evaluable versus blocked by insufficient data.",
            "inputSchema": schema({}),
        },
        {
            "name": "get_research_standing",
            "description": "Status of prospective (forward-testing) strategy registrations: sample progress, scored count and whether a verdict is yet supportable.",
            "inputSchema": schema({}),
        },
        {
            "name": "gex_regime",
            "description": "Stored gamma regime for one ticker from capture history: spot vs gamma-flip (positive/negative gamma), walls, latest net GEX in billions, and the last eight captures. Complements get_gex_levels, which reads the live matrix.",
            "inputSchema": schema({"symbol": symbol}, ["symbol"]),
        },
        {
            "name": "autopilot_status",
            "description": "Health of the local paper autopilot executor: mode, reconciliation, market-data and broker readiness, entry blocker, ledger counts. Paper-only by construction.",
            "inputSchema": schema({}),
        },
        {
            "name": "paper_ledger_summary",
            "description": "Truth from the paper-trading ledger: totals (trades/wins/P&L), the 20 most recent closed positions, open positions, and the last five orders.",
            "inputSchema": schema({}),
        },
        {
            "name": "decision_quality",
            "description": "Decision-quality statistics over closed paper trades: expectancy per trade, win rate, payoff ratio, dead-on-arrival share (losses that never reached +5% MFE), buckets by entry hour and exit reason.",
            "inputSchema": schema({}),
        },
        {
            "name": "prospective_log_tail",
            "description": "The most recent rows of the append-only earnings prediction log, including NO_TRADE decisions. Written once per digest run per ticker.",
            "inputSchema": schema({
                "limit": {"type": "integer", "description": "Rows to return, 1-100. Defaults to 20.", "default": 20, "minimum": 1, "maximum": 100},
            }),
        },
        # ChatGPT's deep-research mode looks for a `search`/`fetch` pair by name and will
        # not drive arbitrary tools. These two wrap the same read-only calls so one server
        # satisfies both hosts; Claude Desktop can ignore them and use the specific tools.
        {
            "name": "search",
            "description": "Find tickers Cipher can analyse. Returns matching symbols as ids for `fetch`. Accepts a ticker, a partial ticker, or a plain-language query naming one.",
            "inputSchema": schema({"query": {"type": "string", "description": "Ticker or phrase, e.g. \"NVDA\" or \"nvidia gamma\"."}}, ["query"]),
        },
        {
            "name": "fetch",
            "description": "Full Cipher research record for one ticker id from `search`: quote, gamma walls and flip level, key exposure levels, session levels and recent headlines.",
            "inputSchema": schema({"id": {"type": "string", "description": "Ticker id returned by `search`, e.g. \"NVDA\"."}}, ["id"]),
        },
    ]


# Every tool on this server reads. OpenAI's MCP guidance asks for `readOnlyHint` on
# read-only tools alongside the standard search/fetch schemas, and a host that trusts the
# hint can skip a confirmation prompt it would otherwise show. The hints are set from one
# place so a tool added later cannot quietly claim to be read-only when it is not: they are
# derived from the fact that `handle_tool` has no write path at all.
TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    # The data comes from live market feeds outside this process, so results for identical
    # arguments change over time.
    "openWorldHint": True,
}


def annotated_tool_specs() -> list[dict[str, Any]]:
    return [{**spec, "annotations": dict(TOOL_ANNOTATIONS)} for spec in tool_specs()]


# Symbols the research surface covers. Loaded from the same cap-tiered
# optionable universe the capture loop and scanner consume, so `search` is
# never narrower than what Cipher actually trades — the hardcoded list this
# replaced had gone stale (SNDK, NBIS and ALAB were invisible). The static
# tuple remains only as the fail-closed fallback when the file is unreadable,
# and as the ETF/anchor floor that survives any universe reshuffle.
UNIVERSE_JSON = Path(os.environ.get(
    "CIPHER_UNIVERSE_JSON",
    str(Path(__file__).resolve().parents[1] / "data" / "optionable_universe_by_cap.json"),
))

FALLBACK_SEARCHABLE = (
    "SPY", "QQQ", "IWM", "DIA", "SMH", "XLE", "XLF", "XLI", "XLK", "XLP", "XLV",
    "AAPL", "AMD", "AMZN", "AVGO", "BA", "BAC", "CAT", "COST", "CRM", "CVX", "DIS",
    "GOOGL", "GS", "HD", "INTC", "JNJ", "JPM", "KO", "LLY", "MCD", "META", "MSFT",
    "MU", "NFLX", "NVDA", "ORCL", "TSLA", "UNH", "WMT", "XOM", "IBIT",
)


def _load_searchable() -> tuple[str, frozenset[str]]:
    try:
        payload = json.loads(UNIVERSE_JSON.read_text(encoding="utf-8-sig"))
        tiers = payload.get("sorted_tickers") or {}
        symbols: set[str] = set()
        for rows in tiers.values():
            for row in rows or []:
                ticker = row.get("ticker") if isinstance(row, dict) else row
                if ticker:
                    symbols.add(str(ticker).upper().strip())
        symbols.update(FALLBACK_SEARCHABLE)
        return f"{UNIVERSE_JSON.name}: {len(symbols)} symbols", frozenset(symbols)
    except (OSError, ValueError, AttributeError):
        return "fallback static list", frozenset(FALLBACK_SEARCHABLE)


SEARCHABLE_SOURCE, SEARCHABLE = _load_searchable()

# Words a query may contain that name a ticker without spelling it.
ALIASES = {
    "nvidia": "NVDA", "apple": "AAPL", "amazon": "AMZN", "google": "GOOGL",
    "alphabet": "GOOGL", "microsoft": "MSFT", "meta": "META", "facebook": "META",
    "tesla": "TSLA", "broadcom": "AVGO", "micron": "MU", "intel": "INTC",
    "netflix": "NFLX", "oracle": "ORCL", "salesforce": "CRM", "walmart": "WMT",
    "costco": "COST", "disney": "DIS", "boeing": "BA", "caterpillar": "CAT",
    "chevron": "CVX", "exxon": "XOM", "goldman": "GS", "lilly": "LLY",
    "eli lilly": "LLY", "johnson": "JNJ", "unitedhealth": "UNH", "bitcoin": "IBIT",
    "semis": "SMH", "semiconductors": "SMH", "nasdaq": "QQQ", "s&p": "SPY",
    "sp500": "SPY", "russell": "IWM", "dow": "DIA", "coca cola": "KO", "coke": "KO",
}


def _search(query: str) -> dict[str, Any]:
    text = (query or "").strip()
    upper = text.upper()
    hits: list[str] = []

    def add(symbol: str) -> None:
        if symbol in SEARCHABLE and symbol not in hits:
            hits.append(symbol)

    for token in "".join(c if c.isalnum() or c == "&" else " " for c in upper).split():
        add(token)
    lower = text.lower()
    for phrase, symbol in ALIASES.items():
        if phrase in lower:
            add(symbol)
    if not hits and len(upper) >= 2:
        for symbol in SEARCHABLE:
            if symbol.startswith(upper):
                add(symbol)
    return {
        "results": [
            {"id": symbol, "title": symbol,
             "text": f"Cipher research record for {symbol}: quote, gamma walls and flip level, exposure levels, session levels, headlines.",
             "url": f"cipher://market/{symbol}"}
            for symbol in hits[:10]
        ],
        "query": text,
        "universe_source": SEARCHABLE_SOURCE,
        "note": (
            "No match means the symbol is not in Cipher's covered universe, not that it does not exist."
            if not hits else RESEARCH_NOTICE
        ),
    }


def _fetch(identifier: str) -> dict[str, Any]:
    symbol = (identifier or "").strip().upper()
    if symbol not in SEARCHABLE:
        raise ValueError(f"{symbol!r} is not in Cipher's covered universe; call search first")
    record: dict[str, Any] = {
        "id": symbol,
        "title": f"{symbol} — Cipher research record",
        "url": f"cipher://market/{symbol}",
        "quote": _get("/api/quote", {"symbol": symbol}),
        "night_vision": project_night_vision(_get("/api/night-vision", {"symbol": symbol})),
    }
    try:
        record["headlines"] = _get("/api/news", {"symbol": symbol, "limit": 8})
    except ValueError as exc:
        record["headlines"] = {"error": str(exc)}
    record["research_notice"] = RESEARCH_NOTICE
    return record


# ---------------------------------------------------------------- paper autopilot

def _executor_status() -> dict[str, Any]:
    """Condensed paper-executor health. Read-only GET against one loopback URL."""
    request = urllib.request.Request(EXECUTOR_STATUS_URL, headers=_core_headers())
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    observed = payload.get("observability") or {}
    readiness = payload.get("market_data_readiness") or {}
    broker = payload.get("paper_broker") or {}
    counts = observed.get("counts") or {}
    return {
        "mode": payload.get("mode"),
        "reconciliation_passed": bool(payload.get("reconciliation_passed")),
        "market_data_ready": bool(readiness.get("market_data_ready")),
        "broker_ready": bool(broker.get("ready")),
        "entry_blocked_reason": observed.get("entry_blocked_reason"),
        "quote_feed_degraded": bool((payload.get("quote_manager") or {}).get("degraded")),
        "counts": {k: counts.get(k) for k in (
            "signal_batches", "signal_cards", "paper_orders",
            "open_paper_positions", "closed_positions", "entry_blocks",
        )},
        "last_worker_exception": observed.get("last_worker_exception"),
        "live_execution_capability": False,
        "paper_only": True,
    }


def _ledger_summary() -> dict[str, Any]:
    """Closed/open position truth from the shadow ledger, opened mode=ro."""
    if not PAPER_LEDGER_DB.is_file():
        return {"available": False, "reason": f"no ledger at {PAPER_LEDGER_DB}",
                "paper_only": True, "live_execution_capability": False}
    db = sqlite3.connect(f"file:{PAPER_LEDGER_DB}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        closed = [dict(r) for r in db.execute(
            """select ticker, direction, quantity, entry_price, exit_price, exit_reason, closed_at
               from paper_positions where status='CLOSED' order by closed_at desc limit 20""")]
        for row in closed:
            row["pnl_usd"] = round(
                (row["exit_price"] - row["entry_price"]) * 100 * row["quantity"], 2)
        open_positions = [dict(r) for r in db.execute(
            """select ticker, direction, entry_price, opened_at from paper_positions
               where status in ('OPEN','SHADOW_OPEN')""")]
        orders = [dict(r) for r in db.execute(
            """select side, symbol, status, created_at from paper_orders
               order by created_at desc limit 5""")]
        totals = db.execute(
            """select count(*) n,
                      sum(case when (exit_price-entry_price)>0 then 1 else 0 end) wins,
                      round(sum((exit_price-entry_price)*100*quantity),2) pnl
               from paper_positions where status='CLOSED'""").fetchone()
    finally:
        db.close()
    return {
        "available": True,
        "closed_total": dict(totals) if totals else {},
        "recent_closed": closed,
        "open_positions": open_positions,
        "recent_orders": orders,
        "paper_only": True,
        "live_execution_capability": False,
    }


def _decision_quality() -> dict[str, Any]:
    """Reuse the ledger analyzer; import by path so the server stays dependency-free."""
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir.parent) not in sys.path:
        sys.path.insert(0, str(scripts_dir.parent))
    try:
        from scripts.autopilot_decision_quality import analyze as _analyze  # noqa: E402
        from scripts.autopilot_decision_quality import DEFAULT_DB  # noqa: E402
    except Exception as exc:  # pragma: no cover - environment-specific
        return {"available": False, "reason": f"analyzer unavailable: {exc}"}
    db_path = Path(os.environ.get("CIPHER_PAPER_LEDGER", str(DEFAULT_DB)))
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in db.execute(
            """select ticker, direction, quantity, entry_price, exit_price,
                      exit_reason, opened_at, closed_at, payload_json
               from paper_positions where status='CLOSED' order by opened_at""")]
    finally:
        db.close()
    report = _analyze(rows)
    # The full per-trade dump is noise through MCP; keep the decision-relevant half.
    report.pop("trades", None)
    return report


def _gex_regime(symbol: str) -> dict[str, Any]:
    """Historical gamma regime for one ticker from the local GEX history ledger.

    Complements `get_gex_levels` (the live matrix view): this reads the stored
    capture history, so a session can see the regime as of the most recent
    completed capture even after hours, and how today's net exposure compares
    with the last several captures.
    """
    ticker = symbol.strip().upper()
    if not GEX_HISTORY_DB.is_file():
        return {"available": False, "reason": f"no gex history at {GEX_HISTORY_DB}"}
    db = sqlite3.connect(f"file:{GEX_HISTORY_DB}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        snap = db.execute(
            """select captured_at, spot, call_wall_strike, put_wall_strike,
                      gamma_flip_level
               from gex_snapshots where ticker = ?
               order by captured_at desc limit 1""",
            (ticker,),
        ).fetchone()
        if not snap:
            return {"available": False,
                    "reason": f"no stored snapshots for {ticker}",
                    "note": "get_gex_levels reads the live matrix instead"}
        recent = [dict(r) for r in db.execute(
            """select captured_at, round(sum(net_gex)/1e9, 3) net_gex_b,
                      count(*) cells
               from gex_strike_cells where ticker = ?
               group by captured_at order by captured_at desc limit 8""",
            (ticker,),
        )]
    finally:
        db.close()
    latest_cells = recent[0] if recent else {}
    spot = snap["spot"]
    flip = snap["gamma_flip_level"]
    regime = None
    if spot is not None and flip is not None:
        regime = "positive_gamma" if spot > flip else "negative_gamma"
    return {
        "available": True,
        "ticker": ticker,
        "as_of": snap["captured_at"],
        "spot": spot,
        "call_wall": snap["call_wall_strike"],
        "put_wall": snap["put_wall_strike"],
        "gamma_flip_level": flip,
        "regime": regime,
        "net_gex_b_latest": latest_cells.get("net_gex_b"),
        "cells_latest": latest_cells.get("cells"),
        "recent_net_gex_b": [
            {"captured_at": row["captured_at"], "net_gex_b": row["net_gex_b"]}
            for row in reversed(recent)
        ],
        "caveat": RESEARCH_NOTICE,
    }


def _prospective_tail(limit: int) -> dict[str, Any]:
    limit = max(1, min(100, int(limit or 20)))
    if not PROSPECTIVE_LOG.is_file():
        return {"available": False,
                "reason": f"no prospective log yet at {PROSPECTIVE_LOG}",
                "note": "the daily digest writes it from 2026-08-26 onward"}
    lines = PROSPECTIVE_LOG.read_text(encoding="utf-8").splitlines()
    tail = []
    for line in lines[-limit:]:
        try:
            tail.append(json.loads(line))
        except json.JSONDecodeError:
            tail.append({"malformed": True})
    return {"available": True, "returned": len(tail), "rows": tail}


def handle_tool(name: str, args: dict[str, Any]) -> Any:
    symbol = str(args.get("symbol") or "").strip().upper()
    if name in {"get_quote", "get_bars", "get_gex_levels", "get_night_vision", "search_contract", "get_news_headlines"}:
        if not symbol:
            raise ValueError("symbol is required")

    if name == "cipher_health":
        return _get("/api/health")
    if name == "get_quote":
        return _get("/api/quote", {"symbol": symbol})
    if name == "get_bars":
        limit = max(1, min(500, int(args.get("limit") or 30)))
        return _get("/api/bars", {
            "symbol": symbol,
            "timeframe": args.get("timeframe") or "1Day",
            "limit": limit,
        })
    if name == "get_gex_levels":
        near = max(1, min(40, int(args.get("strikes_near_spot") or 12)))
        return project_gex_levels(_get("/api/heatmap", {"symbol": symbol}), near)
    if name == "get_night_vision":
        return project_night_vision(_get("/api/night-vision", {"symbol": symbol}))
    if name == "search_contract":
        strike = args.get("strike")
        if strike is None:
            raise ValueError("strike is required, e.g. 770")
        return _get("/api/contract-search", {
            "symbol": symbol,
            "strike": strike,
            "type": (args.get("option_type") or "call").lower(),
            "expiration": args.get("expiration"),
            "date": args.get("date"),
        })
    if name == "get_news_headlines":
        limit = max(1, min(50, int(args.get("limit") or 10)))
        return _get("/api/news", {"symbol": symbol, "limit": limit})
    if name == "list_strategies":
        return project_strategies(_get("/api/strategies"))
    if name == "get_research_standing":
        return _get("/api/standing")
    if name == "search":
        return _search(str(args.get("query") or ""))
    if name == "fetch":
        return _fetch(str(args.get("id") or ""))
    if name == "autopilot_status":
        return _executor_status()
    if name == "gex_regime":
        if not symbol:
            raise ValueError("symbol is required")
        return _gex_regime(symbol)
    if name == "paper_ledger_summary":
        return _ledger_summary()
    if name == "decision_quality":
        return _decision_quality()
    if name == "prospective_log_tail":
        return _prospective_tail(int(args.get("limit") or 20))
    raise ValueError(f"unknown tool: {name}")


def result(data: Any) -> dict[str, Any]:
    text = json.dumps(data, indent=2, default=str)
    if len(text) > MAX_RESULT_BYTES:
        text = (
            json.dumps({
                "truncated": True,
                "reason": f"result exceeded {MAX_RESULT_BYTES} bytes and was cut to protect the host's context",
                "bytes": len(text),
            }, indent=2)
            + "\n"
            + text[:MAX_RESULT_BYTES]
        )
        return {"content": [{"type": "text", "text": text}]}
    return {"content": [{"type": "text", "text": text}], "structuredContent": data if isinstance(data, dict) else {"value": data}}


# --------------------------------------------------------------------------- protocol

PROMPTS = [
    {
        "name": "analyze_ticker",
        "description": "Read Cipher's evidence for one ticker in a fixed order and state what it does and does not support.",
        "arguments": [{"name": "symbol", "required": True}],
    },
]

PROMPT_TEXT = (
    "Analyse {symbol} using Cipher's read-only evidence, in this order: get_quote for price "
    "context, get_night_vision for gamma walls, flip level and session levels, get_gex_levels "
    "for per-strike exposure near spot, then get_news_headlines for context.\n\n"
    "Report what the evidence shows and where it disagrees with itself. Distinguish measured "
    "values from unavailable ones — an unavailable exposure cell is unknown, not zero, and the "
    "cell_coverage counts tell you how much was unknown. State what would invalidate your "
    "reading.\n\n" + RESEARCH_NOTICE + " Do not produce an entry, exit, size or order."
)

RESOURCES = [
    {"uri": "cipher://market/about", "name": "What this server exposes", "mimeType": "application/json"},
    {"uri": "cipher://market/health", "name": "cipher-core health", "mimeType": "application/json"},
]


DEFAULT_PROTOCOL_VERSION = "2025-06-18"
# Revisions of the MCP protocol this server's messages are compatible with. The surface used
# here -- initialize, tools/list, tools/call, prompts, resources -- is unchanged across them.
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18"})


def _negotiate_protocol(params: dict[str, Any] | None) -> str:
    requested = str((params or {}).get("protocolVersion") or "").strip()
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION


def handle(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            # Echo the client's protocol version when it is one we can speak. Replying with
            # our own regardless is a legitimate reading of the spec, but a strict client
            # that receives a version it never offered may abort the handshake -- and an
            # aborted handshake shows up in a host UI as a generic "something went wrong"
            # with nothing to point at. Negotiating removes that class of failure.
            "protocolVersion": _negotiate_protocol(params),
            "capabilities": {
                "tools": {"listChanged": False},
                "prompts": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {"name": "cipher-market-mcp", "version": "0.1.0"},
            "instructions": RESEARCH_NOTICE,
        }
    if method == "tools/list":
        return {"tools": annotated_tool_specs()}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            return result(handle_tool(name, args))
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
    if method == "prompts/list":
        return {"prompts": PROMPTS}
    if method == "prompts/get":
        name = params.get("name", "")
        if name != "analyze_ticker":
            raise ValueError("prompt not found")
        symbol = str((params.get("arguments") or {}).get("symbol") or "SPY").upper()
        return {
            "description": name,
            "messages": [{"role": "user", "content": {"type": "text", "text": PROMPT_TEXT.format(symbol=symbol)}}],
        }
    if method == "resources/list":
        return {"resources": RESOURCES}
    if method == "resources/read":
        uri = params.get("uri", "")
        if uri == "cipher://market/about":
            data: Any = {
                "server": "cipher-market-mcp",
                "base_url": BASE_URL,
                "access": "read-only; HTTP GET against a fixed allowlist",
                "allowed_paths": sorted(ALLOWED_PATHS),
                "tools": [spec["name"] for spec in tool_specs()],
                "research_notice": RESEARCH_NOTICE,
            }
        elif uri == "cipher://market/health":
            data = _get("/api/health")
        else:
            raise ValueError("resource not found")
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if method.startswith("notifications/") or method == "logging/setLevel":
        return None
    raise ValueError(f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response_id = request.get("id")
            try:
                payload = handle(request.get("method", ""), request.get("params") or {})
            except Exception as exc:
                payload = {"jsonrpc": "2.0", "id": response_id, "error": {"code": -32603, "message": str(exc)}}
            else:
                if response_id is None:
                    continue
                payload = {"jsonrpc": "2.0", "id": response_id, "result": payload}
            sys.stdout.write(json.dumps(payload, default=str) + "\n")
            sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
