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
    its own `/api/health` reports `read_only: true`.

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
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = os.environ.get("CIPHER_CORE_URL", "http://127.0.0.1:8282").rstrip("/")
TIMEOUT = float(os.environ.get("CIPHER_MCP_TIMEOUT", "45"))

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

def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    if path not in ALLOWED_PATHS:
        raise ValueError(f"path is not in the read-only allowlist: {path}")
    query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
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


# Symbols the research surface covers. Kept explicit so `search` never invents a ticker
# that cipher-core would then fail on.
SEARCHABLE = (
    "SPY", "QQQ", "IWM", "DIA", "SMH", "XLE", "XLF", "XLI", "XLK", "XLP", "XLV",
    "AAPL", "AMD", "AMZN", "AVGO", "BA", "BAC", "CAT", "COST", "CRM", "CVX", "DIS",
    "GOOGL", "GS", "HD", "INTC", "JNJ", "JPM", "KO", "LLY", "MCD", "META", "MSFT",
    "MU", "NFLX", "NVDA", "ORCL", "TSLA", "UNH", "WMT", "XOM", "IBIT",
)

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


def handle(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": "2025-06-18",
            "capabilities": {
                "tools": {"listChanged": False},
                "prompts": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {"name": "cipher-market-mcp", "version": "0.1.0"},
            "instructions": RESEARCH_NOTICE,
        }
    if method == "tools/list":
        return {"tools": tool_specs()}
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
