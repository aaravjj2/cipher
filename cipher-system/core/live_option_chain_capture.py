#!/usr/bin/env python3
"""
Capture live option chain snapshots for Cipher's 12 scanner tickers via Alpaca OPRA.

Saves daily JSONL files to cipher-system/data/live_option_chains/ with full
bid/ask/mid/last/vol/OI/IV/Greeks for each contract.

Usage:
    python3 cipher-system/core/live_option_chain_capture.py --all
    python3 cipher-system/core/live_option_chain_capture.py --tickers NVDA,MSFT,AAPL
    python3 cipher-system/core/live_option_chain_capture.py --loop --interval-minutes 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "app" / ".env"
DATA_DIR = ROOT / "data"
LIVE_CHAINS_DIR = DATA_DIR / "live_option_chains"
DATA = "https://data.alpaca.markets"
PAPER_API = "https://paper-api.alpaca.markets"
CONTRACTS = f"{PAPER_API}/v2/options/contracts"

# Cipher's 12 active scanner tickers
SCANNER_TICKERS = [
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT",
    "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ"
]

DEFAULT_MAX_PAGES = 40  # Full liquid ETF chains can require >12 pages of 1000 contracts


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_env() -> dict[str, str]:
    values = {}
    if ENV.is_file():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (
        "ALPACA_ALGO_KEY", "ALPACA_ALGO_SECRET",
        "ALPACA_ALGO_PLUS_KEY", "ALPACA_ALGO_PLUS_SECRET",
        "ALPACA_API_KEY", "ALPACA_API_SECRET",
        "ALPACA_DATA_FEED", "ALPACA_STOCK_FEED",
    ):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def get_credentials(env: dict[str, str]) -> tuple[str, str, str]:
    key = env.get("ALPACA_ALGO_KEY") or env.get("ALPACA_ALGO_PLUS_KEY") or env.get("ALPACA_API_KEY")
    secret = env.get("ALPACA_ALGO_SECRET") or env.get("ALPACA_ALGO_PLUS_SECRET") or env.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise ValueError("Alpaca market-data credentials not configured. Check app/.env or environment.")
    options_feed = env.get("ALPACA_DATA_FEED", "opra").lower()
    if options_feed not in {"opra", "indicative"}:
        options_feed = "opra"
    return key, secret, options_feed


def alpaca_request(path: str, query: dict[str, Any] | None, key: str, secret: str, base: str = DATA) -> dict:
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
        detail = "the selected feed may require an eligible subscription" if exc.code == 403 else "check symbol and configured market-data access"
        raise ValueError(f"Alpaca request failed (HTTP {exc.code}); {detail}.") from exc
    except URLError as exc:
        raise ValueError("Unable to reach Alpaca market data. Check network and retry.") from exc


def fetch_option_chain(
    ticker: str,
    key: str,
    secret: str,
    feed: str,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[dict]:
    """Fetch full option chain snapshot from Alpaca OPRA."""
    all_contracts = []
    query = {"feed": feed, "limit": 1000}

    for _ in range(max(1, max_pages)):
        raw = alpaca_request(f"/v1beta1/options/snapshots/{ticker.upper()}", query, key, secret)
        snapshots = raw.get("snapshots", {})
        for symbol, item in snapshots.items():
            all_contracts.append(_parse_contract(symbol, item, feed))

        token = raw.get("next_page_token")
        if not token:
            break
        query = {"feed": feed, "limit": 1000, "page_token": token}

    return all_contracts


def _parse_contract(symbol: str, item: dict, feed: str) -> dict:
    meta = _parse_occ(symbol)
    q = item.get("latestQuote", {})
    trade = item.get("latestTrade", {})
    greeks = item.get("greeks", {})
    day = item.get("dailyBar", {})

    bid = _num(q.get("bp", q.get("bid_price")))
    ask = _num(q.get("ap", q.get("ask_price")))
    mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else None

    return {
        "symbol": symbol.upper(),
        "expiry": meta["expiry"],
        "strike": meta["strike"],
        "type": meta["type"],
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": _num(trade.get("p", trade.get("price"))),
        "size": int(_num(trade.get("s", trade.get("size"))) or 0),
        "trade_time": trade.get("t", trade.get("timestamp")),
        "exchange": trade.get("x", trade.get("exchange")),
        "volume": _num(day.get("v", day.get("volume", item.get("volume")))),
        "open_interest": _num(item.get("openInterest", item.get("open_interest"))),
        "open_interest_date": item.get("open_interest_date"),
        "iv": _num(item.get("impliedVolatility", item.get("implied_volatility"))),
        "gamma": _num(greeks.get("gamma")),
        "delta": _num(greeks.get("delta")),
        "theta": _num(greeks.get("theta")),
        "vega": _num(greeks.get("vega")),
        "rho": _num(greeks.get("rho")),
        "quote_time": q.get("t", q.get("timestamp")),
        "feed": feed,
    }


def _parse_occ(symbol: str) -> dict:
    """Parse OCC option symbol: AAPL260805C00325000 -> {expiry, strike, type}"""
    symbol = symbol.upper().strip()
    for i, ch in enumerate(symbol):
        if ch.isdigit():
            root = symbol[:i]
            rest = symbol[i:]
            break
    else:
        return {"expiry": None, "strike": None, "type": None}

    if len(rest) < 15:
        return {"expiry": None, "strike": None, "type": None}

    expiry_str = rest[:6]  # YYMMDD
    opt_type = rest[6]  # C or P
    strike_str = rest[7:]

    try:
        year = 2000 + int(expiry_str[:2])
        month = int(expiry_str[2:4])
        day = int(expiry_str[4:6])
        expiry = f"{year:04d}-{month:02d}-{day:02d}"
        strike = int(strike_str) / 1000.0
        opt_type = "call" if opt_type == "C" else "put"
        return {"expiry": expiry, "strike": strike, "type": opt_type}
    except Exception:
        return {"expiry": None, "strike": None, "type": None}


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def fetch_open_interest(ticker: str, key: str, secret: str) -> dict[str, dict]:
    """Fetch open interest from option contracts metadata."""
    oi_map = {}
    query = {"underlying_symbols": ticker.upper(), "limit": 10000}

    for _ in range(6):  # max 6 pages
        raw = alpaca_request("/v2/options/contracts", query, key, secret, base=PAPER_API)
        for item in raw.get("option_contracts") or raw.get("contracts") or []:
            symbol = str(item.get("symbol", "")).upper()
            if symbol:
                oi_map[symbol] = {
                    "open_interest": _num(item.get("open_interest")),
                    "open_interest_date": item.get("open_interest_date"),
                }
        token = raw.get("next_page_token")
        if not token:
            break
        query = {"underlying_symbols": ticker.upper(), "limit": 10000, "page_token": token}

    return oi_map


def capture_ticker(
    ticker: str,
    key: str,
    secret: str,
    feed: str,
    max_pages: int,
    oi_map: dict[str, dict] | None = None,
) -> dict:
    """Capture a full option chain snapshot for one ticker."""
    contracts = fetch_option_chain(ticker, key, secret, feed, max_pages)

    # Merge open interest
    if oi_map:
        for c in contracts:
            oi_data = oi_map.get(c["symbol"], {})
            if c["open_interest"] is None and oi_data.get("open_interest") is not None:
                c["open_interest"] = oi_data["open_interest"]
            if c["open_interest_date"] is None and oi_data.get("open_interest_date"):
                c["open_interest_date"] = oi_data["open_interest_date"]

    return {
        "timestamp": utcnow(),
        "ticker": ticker.upper(),
        "feed": feed,
        "contract_count": len(contracts),
        "contracts": contracts,
    }


def save_snapshot(ticker: str, payload: dict) -> Path:
    """Append snapshot to daily JSONL file and write latest_*.json."""
    LIVE_CHAINS_DIR.mkdir(parents=True, exist_ok=True)

    today = today_str()
    jsonl_path = LIVE_CHAINS_DIR / f"{today}_{ticker.upper()}.jsonl"
    latest_path = LIVE_CHAINS_DIR / f"latest_{ticker.upper()}.json"

    # Append to daily JSONL
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")

    # Write latest snapshot
    latest_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return jsonl_path


def capture_once(
    tickers: list[str],
    key: str,
    secret: str,
    feed: str,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict:
    """Capture one round for all tickers."""
    results = {"tickers": len(tickers), "success": 0, "errors": [], "files": []}

    for ticker in tickers:
        print(f"  Fetching {ticker}...", end=" ", flush=True)
        try:
            # Fetch OI first (lightweight)
            oi_map = fetch_open_interest(ticker, key, secret)

            # Fetch full chain
            payload = capture_ticker(ticker, key, secret, feed, max_pages, oi_map)

            # Save
            jsonl_path = save_snapshot(ticker, payload)

            results["success"] += 1
            results["files"].append(str(jsonl_path))
            print(f"OK ({payload['contract_count']} contracts)")

        except Exception as exc:
            results["errors"].append({"ticker": ticker, "error": str(exc)})
            print(f"ERROR: {exc}")

    return results


def is_market_hours() -> bool:
    """Check current time against 7:30 AM - 4:10 PM America/New_York, Mon-Fri."""
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:  # Sat/Sun
        return False
    return dtime(7, 30) <= now_et.time() <= dtime(16, 10)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture live option chains via Alpaca OPRA")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="Capture all 12 scanner tickers")
    scope.add_argument("--tickers", type=str, help="Comma-separated tickers")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Max pages per ticker")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval-minutes", type=float, default=5.0, help="Loop interval in minutes")
    parser.add_argument("--force", action="store_true", help="Run even outside market hours")
    args = parser.parse_args()

    # Determine tickers
    if args.all:
        tickers = SCANNER_TICKERS
    else:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    # Load credentials
    env = load_env()
    key, secret, feed = get_credentials(env)

    if feed != "opra":
        print(f"Warning: Configured feed is '{feed}', but OPRA is required for live option chains.")

    print(f"Live Option Chain Capture")
    print(f"  Tickers: {', '.join(tickers)}")
    print(f"  Feed: {feed}")
    print(f"  Max pages/ticker: {args.max_pages}")
    print(f"  Output: {LIVE_CHAINS_DIR}")
    print()

    if args.loop:
        print(f"Running in loop mode (interval: {args.interval_minutes} min)")
        if not args.force:
            print("Market hours check: ENABLED (7:30-16:10 ET Mon-Fri)")
        else:
            print("Market hours check: DISABLED (--force)")

        while True:
            if args.force or is_market_hours():
                started = time.time()
                print(f"\n[{utcnow()}] Capture cycle started")
                results = capture_once(tickers, key, secret, feed, args.max_pages)
                print(f"  Success: {results['success']}/{results['tickers']}")
                if results["errors"]:
                    for err in results["errors"]:
                        print(f"  ERROR {err['ticker']}: {err['error']}")
                elapsed = time.time() - started
                print(f"  Completed in {elapsed:.1f}s")

                # Sleep until next interval
                delay = max(0.0, args.interval_minutes * 60 - elapsed)
                if delay > 0:
                    print(f"  Sleeping {delay:.1f}s...")
                    time.sleep(delay)
            else:
                print(f"[{utcnow()}] Outside market hours, sleeping 60s...")
                time.sleep(60)
    else:
        results = capture_once(tickers, key, secret, feed, args.max_pages)
        print(f"\nCompleted: {results['success']}/{results['tickers']} successful")
        if results["errors"]:
            for err in results["errors"]:
                print(f"  ERROR {err['ticker']}: {err['error']}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())