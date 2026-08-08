#!/usr/bin/env python3
"""Cipher Stream Health Check with Market Hours Awareness and Telegram Alerts."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 12 scanner tickers + SPY, QQQ, IWM for broad market
DEFAULT_GEX_TICKERS = (
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL",
    "TSLA", "META", "MU", "AMD", "QQQ", "SPY", "IWM",
)
DEFAULT_CHAIN_TICKERS = DEFAULT_GEX_TICKERS[:12]  # 12 scanner tickers


def is_market_hours() -> bool:
    """Check if current time is during market hours (7:30 AM - 4:10 PM ET, Mon-Fri)."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:  # Sat/Sun
        return False
    return dtime(7, 30) <= now_et.time() <= dtime(16, 10)


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


def check_tradier_stream(data: Path, *, threshold: int, now: datetime) -> tuple[bool, list[str]]:
    """Check Tradier equity stream freshness."""
    rows = sqlite_rows(
        data / "tradier_stream.sqlite",
        "select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 20",
    )
    healthy = bool(rows)
    issues = []
    for symbol, timestamp in rows:
        age = age_minutes(str(timestamp), now=now)
        state = status(age, threshold)
        healthy = healthy and state == "OK"
        age_text = "unknown" if age is None else f"{age:.1f} min"
        if state != "OK":
            issues.append(f"  {symbol}: {age_text} [{state}] ({timestamp})")
    return healthy, issues


def check_gex_snapshots(data: Path, *, threshold: int, now: datetime) -> tuple[bool, list[str]]:
    """Check GEX snapshot freshness from gex_history.sqlite."""
    placeholders = ",".join("?" for _ in DEFAULT_GEX_TICKERS)
    # Per-ticker MAX(captured_at) is ground truth for freshness. A top-N
    # cross-ticker query previously surfaced old rows from prior capture runs
    # (duplicates per ticker) and falsely flagged fresh tickers as STALE.
    rows = sqlite_rows(
        data / "gex_history.sqlite",
        f"select ticker, max(captured_at) from gex_snapshots where ticker in ({placeholders}) group by ticker order by max(captured_at) desc",
        DEFAULT_GEX_TICKERS,
    )
    healthy = bool(rows)
    issues = []
    for ticker, timestamp in rows:
        age = age_minutes(str(timestamp), now=now)
        state = status(age, threshold)
        healthy = healthy and state == "OK"
        age_text = "unknown" if age is None else f"{age:.1f} min"
        if state != "OK":
            issues.append(f"  {ticker}: {age_text} [{state}] ({timestamp})")
    return healthy, issues


def check_option_chains(data: Path, *, tickers: tuple[str, ...], threshold: int, now: datetime) -> tuple[bool, list[str]]:
    """Check live option chain freshness."""
    healthy = True
    issues = []
    for ticker in tickers:
        latest_path = data / "live_option_chains" / f"latest_{ticker}.json"
        if not latest_path.is_file():
            issues.append(f"  {ticker}: MISSING")
            healthy = False
            continue
        try:
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            issues.append(f"  {ticker}: INVALID JSON")
            healthy = False
            continue
        timestamp = payload.get("as_of") or payload.get("timestamp")
        age = age_minutes(timestamp, now=now)
        state = status(age, threshold)
        healthy = healthy and state == "OK"
        age_text = "unknown" if age is None else f"{age:.1f} min"
        if state != "OK":
            issues.append(f"  {ticker}: {age_text} [{state}] ({timestamp or 'no timestamp'})")
    return healthy, issues


def send_telegram_alert(message: str) -> int:
    """Send alert via Hermes CLI."""
    hermes_bin = os.environ.get("HERMES_BIN") or "/home/aarav/.local/bin/hermes"
    try:
        result = subprocess.run(
            [hermes_bin, "send", "--to", "telegram", message],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 or "sent to telegram" in result.stdout.lower():
            return 0
        print(f"Hermes send failed: {result.stderr}", file=sys.stderr)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("Hermes send timed out", file=sys.stderr)
        return 124
    except FileNotFoundError:
        print(f"Hermes binary not found at {hermes_bin}", file=sys.stderr)
        return 127


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Cipher market-data freshness with market-hours awareness.")
    parser.add_argument("--data-root", type=Path, default=DATA)
    parser.add_argument("--tradier-max-age", type=int, default=5)
    parser.add_argument("--gex-max-age", type=int, default=15)
    parser.add_argument("--option-chains-max-age", type=int, default=15)
    parser.add_argument("--alert-only-if-stale", action="store_true", help="Only alert if streams are stale during market hours")
    parser.add_argument("--always-alert", action="store_true", help="Always send alert regardless of market hours")
    parser.add_argument("--no-alert", action="store_true", help="Suppress Telegram alerts")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    data = args.data_root.resolve()
    market_open = is_market_hours()

    print("=== Cipher Stream Health Check ===")
    print(f"Current time (UTC): {now.isoformat()}")
    print(f"Market hours (ET): {market_open}")
    print(f"Data root: {data}")
    print()

    # Check all streams
    tradier_ok, tradier_issues = check_tradier_stream(data, threshold=args.tradier_max_age, now=now)
    print("--- Tradier Equity Stream ---")
    if tradier_ok:
        print("  All symbols OK")
    else:
        for issue in tradier_issues:
            print(issue)
    print()

    gex_ok, gex_issues = check_gex_snapshots(data, threshold=args.gex_max_age, now=now)
    print("--- GEX Snapshots (Alpaca) ---")
    if gex_ok:
        print("  All tickers OK")
    else:
        for issue in gex_issues:
            print(issue)
    print()

    chains_ok, chains_issues = check_option_chains(
        data, tickers=DEFAULT_CHAIN_TICKERS, threshold=args.option_chains_max_age, now=now
    )
    print("--- Live Option Chains ---")
    if chains_ok:
        print("  All tickers OK")
    else:
        for issue in chains_issues:
            print(issue)
    print()

    all_ok = tradier_ok and gex_ok and chains_ok
    overall_status = "ALL OK" if all_ok else "STALE OR MISSING DATA DETECTED"
    print(f"Overall: {overall_status}")

    # Determine if we should alert
    should_alert = False
    alert_reason = ""

    if args.no_alert:
        should_alert = False
    elif args.always_alert:
        should_alert = True
        alert_reason = "forced by --always-alert"
    elif args.alert_only_if_stale and not all_ok and market_open:
        should_alert = True
        alert_reason = "streams stale during market hours"
    elif not all_ok and market_open:
        # Default behavior: alert if stale during market hours
        should_alert = True
        alert_reason = "streams stale during market hours"
    elif not all_ok and not market_open:
        alert_reason = "streams stale but outside market hours (expected)"

    if should_alert:
        # Build alert message
        lines = [
            f"🚨 CIPHER STREAM HEALTH ALERT",
            f"Time (UTC): {now.isoformat()}",
            f"Market hours: {'YES' if market_open else 'NO'}",
            f"Status: {overall_status}",
            f"Reason: {alert_reason}",
            "",
        ]
        if tradier_issues:
            lines.append("Tradier Equity Stream issues:")
            lines.extend(tradier_issues[:10])  # Limit to first 10
            lines.append("")
        if gex_issues:
            lines.append("GEX Snapshot issues:")
            lines.extend(gex_issues[:10])
            lines.append("")
        if chains_issues:
            lines.append("Live Option Chain issues:")
            lines.extend(chains_issues[:10])
            lines.append("")

        message = "\n".join(lines)
        print(f"\nSending Telegram alert...")
        send_telegram_alert(message)
    else:
        print(f"\nNo alert sent: {alert_reason or 'all streams healthy'}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())