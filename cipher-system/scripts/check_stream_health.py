#!/usr/bin/env python3
"""Print a local, read-only health summary for Cipher data collectors."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_GEX_TICKERS = (
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL",
    "TSLA", "META", "MU", "AMD", "QQQ", "SPY", "IWM",
)
DEFAULT_CHAIN_TICKERS = DEFAULT_GEX_TICKERS[:12]


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def age_minutes(value: str | None, *, now: datetime) -> float | None:
    observed = parse_dt(value)
    if observed is None:
        return None
    return (now - observed).total_seconds() / 60


def sqlite_rows(path: Path, query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if not path.is_file():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as db:
        return db.execute(query, parameters).fetchall()


def status(age: float | None, threshold: int) -> str:
    if age is None:
        return "MISSING"
    return "STALE" if age > threshold else "OK"


def print_rows(title: str, rows: list[tuple[Any, ...]], *, threshold: int, now: datetime) -> bool:
    print(f"--- {title} ---")
    healthy = bool(rows)
    if not rows:
        print("  no readable records")
        return False
    for symbol, timestamp in rows:
        age = age_minutes(str(timestamp), now=now)
        state = status(age, threshold)
        healthy = healthy and state == "OK"
        age_text = "unknown" if age is None else f"{age:.1f} min"
        print(f"  {symbol}: {age_text} [{state}] ({timestamp})")
    return healthy


def option_chain_status(chain_dir: Path, *, tickers: tuple[str, ...], threshold: int, now: datetime) -> bool:
    print("--- Live Option Chains ---")
    healthy = True
    for ticker in tickers:
        latest_path = chain_dir / f"latest_{ticker}.json"
        if not latest_path.is_file():
            print(f"  {ticker}: MISSING")
            healthy = False
            continue
        try:
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"  {ticker}: INVALID JSON")
            healthy = False
            continue
        timestamp = payload.get("as_of") or payload.get("timestamp")
        age = age_minutes(timestamp, now=now)
        state = status(age, threshold)
        healthy = healthy and state == "OK"
        age_text = "unknown" if age is None else f"{age:.1f} min"
        print(f"  {ticker}: {age_text} [{state}] ({timestamp or 'no timestamp'})")
    return healthy


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local Cipher market-data freshness.")
    parser.add_argument("--data-root", type=Path, default=DATA)
    parser.add_argument("--tradier-max-age", type=int, default=5)
    parser.add_argument("--gex-max-age", type=int, default=15)
    parser.add_argument("--option-chains-max-age", type=int, default=15)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    data = args.data_root.resolve()
    print("=== Cipher Stream Health Check ===")
    print(f"Current time: {now.isoformat()}")
    print(f"Data root: {data}")
    print()

    tradier_rows = sqlite_rows(
        data / "tradier_stream.sqlite",
        "select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 20",
    )
    tradier_ok = print_rows(
        "Tradier Equity Stream",
        tradier_rows,
        threshold=args.tradier_max_age,
        now=now,
    )
    print()

    placeholders = ",".join("?" for _ in DEFAULT_GEX_TICKERS)
    gex_rows = sqlite_rows(
        data / "gex_history.sqlite",
        f"select ticker, captured_at from gex_snapshots where ticker in ({placeholders}) order by captured_at desc limit 20",
        DEFAULT_GEX_TICKERS,
    )
    gex_ok = print_rows(
        "GEX Snapshots (Alpaca)",
        gex_rows,
        threshold=args.gex_max_age,
        now=now,
    )
    print()

    chains_ok = option_chain_status(
        data / "live_option_chains",
        tickers=DEFAULT_CHAIN_TICKERS,
        threshold=args.option_chains_max_age,
        now=now,
    )
    print()
    all_ok = tradier_ok and gex_ok and chains_ok
    print(f"Overall: {'ALL OK' if all_ok else 'STALE OR MISSING DATA DETECTED'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
