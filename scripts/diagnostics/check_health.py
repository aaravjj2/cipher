#!/usr/bin/env python3
"""Cipher Live Data Stream Health Check
Checks health of all live data streams and sends Telegram alert if stale.
Live option chains are 24/7 stream - staleness is actionable even off-hours.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Configuration
TICKERS = ["NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ"]
DATA_DIR = Path("/home/aarav/Aarav/cipher/cipher-system/data")
TRADIER_DB = DATA_DIR / "tradier_stream.sqlite"
GEX_DB = DATA_DIR / "gex_history.sqlite"
LIVE_OPTION_DIR = DATA_DIR / "live_option_chains"

# Thresholds (during market hours)
TRADIER_MAX_AGE = timedelta(minutes=5)
GEX_MAX_AGE = timedelta(minutes=15)
OPTION_CHAIN_MAX_AGE = timedelta(minutes=15)


def is_market_hours(now: datetime) -> bool:
    """Determine if US market is currently open.
    Approximate: Mon-Fri, 9:30 AM - 4:00 PM ET.
    Uses UTC-4 for EDT (March-Nov), UTC-5 for EST (Nov-March).
    """
    # Weekend check
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False

    # Approximate ET offset (EDT = UTC-4, EST = UTC-5)
    # Simplified: assume EDT (UTC-4) for now
    et_hour = (now.hour - 4) % 24
    et_minute = now.minute

    # Market hours: 9:30 - 16:00 ET
    market_open = (et_hour == 9 and et_minute >= 30) or (10 <= et_hour < 16)
    return market_open


def check_tradier_stream(now: datetime) -> tuple[str | None, timedelta | None]:
    """Check Tradier equity stream freshness."""
    if not TRADIER_DB.exists():
        return "Tradier DB NOT FOUND", None

    # Use file mtime (DB is 13GB, queries too slow)
    mtime = datetime.fromtimestamp(TRADIER_DB.stat().st_mtime, tz=timezone.utc)
    age = now - mtime
    return None, age


def check_gex_snapshots(now: datetime) -> tuple[str | None, timedelta | None]:
    """Check GEX snapshots freshness."""
    if not GEX_DB.exists():
        return "GEX DB NOT FOUND", None

    try:
        conn = sqlite3.connect(GEX_DB)
        c = conn.cursor()
        c.execute("SELECT MAX(captured_at) FROM gex_snapshots")
        r = c.fetchone()[0]
        conn.close()

        if r:
            dt = datetime.fromisoformat(r.replace('Z', '+00:00'))
            age = now - dt
            return None, age
        else:
            return "GEX: no snapshots found", None
    except Exception as e:
        return f"GEX query failed: {e}", None


def check_live_option_chains(now: datetime) -> tuple[list[str], list[tuple[str, timedelta]]]:
    """Check live option chains freshness for all tickers."""
    missing = []
    stale = []

    for ticker in TICKERS:
        latest_file = LIVE_OPTION_DIR / f"latest_{ticker}.json"
        if latest_file.exists():
            mtime = datetime.fromtimestamp(latest_file.stat().st_mtime, tz=timezone.utc)
            age = now - mtime
            if age > OPTION_CHAIN_MAX_AGE:
                stale.append((ticker, age))
        else:
            missing.append(ticker)

    return missing, stale


def send_telegram(message: str) -> int:
    """Send message via Hermes to Telegram."""
    hermes_bin = "/home/aarav/.local/bin/hermes"
    cmd = [hermes_bin, "send", "--to", "telegram", message]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    print(f"Hermes stdout: {result.stdout}")
    print(f"Hermes stderr: {result.stderr}")
    return result.returncode


def main() -> int:
    now = datetime.now(timezone.utc)
    market_open = is_market_hours(now)

    print(f"Check time (UTC): {now}")
    print(f"Market hours (ET): {market_open}")

    alerts = []

    # 1. Check Tradier equity stream (only alert during market hours)
    tradier_err, tradier_age = check_tradier_stream(now)
    if tradier_err:
        print(f"Tradier: {tradier_err}")
        if market_open:
            alerts.append(f"⚠️ Tradier stream: {tradier_err}")
    elif tradier_age is not None:
        print(f"Tradier DB mtime: {now - tradier_age} (age: {tradier_age})")
        if market_open and tradier_age > TRADIER_MAX_AGE:
            alerts.append(f"⚠️ Tradier stream STALE: {tradier_age} old (limit: 5 min)")

    # 2. Check GEX snapshots (only alert during market hours)
    gex_err, gex_age = check_gex_snapshots(now)
    if gex_err:
        print(f"GEX: {gex_err}")
        if market_open:
            alerts.append(f"⚠️ GEX snapshots: {gex_err}")
    elif gex_age is not None:
        print(f"GEX latest snapshot: {now - gex_age} (age: {gex_age})")
        if market_open and gex_age > GEX_MAX_AGE:
            alerts.append(f"⚠️ GEX snapshots STALE: {gex_age} old (limit: 15 min)")

    # 3. Check live option chains (ALWAYS alert - 24/7 stream)
    print("\nLive option chains:")
    missing, stale = check_live_option_chains(now)

    for ticker in TICKERS:
        latest_file = LIVE_OPTION_DIR / f"latest_{ticker}.json"
        if latest_file.exists():
            mtime = datetime.fromtimestamp(latest_file.stat().st_mtime, tz=timezone.utc)
            age = now - mtime
            is_stale = age > OPTION_CHAIN_MAX_AGE  # ALWAYS check, not just market hours
            status = "STALE" if is_stale else "OK"
            print(f"  {ticker}: {mtime} (age: {age}) [{status}]")
        else:
            print(f"  {ticker}: NO FILE")

    if missing:
        alerts.append(f"⚠️ Missing option chain files: {', '.join(missing)}")
    if stale:
        stale_str = ", ".join(f"{t} ({a})" for t, a in stale)
        alerts.append(f"⚠️ Stale option chains (24/7 stream): {stale_str} (limit: 15 min)")

    # Summary
    print("\n--- ALERTS ---")
    if alerts:
        for a in alerts:
            print(a)

        # Send Telegram alert
        msg = f"""Cipher Data Health Alert 🚨

Checked: {now.strftime('%Y-%m-%d %H:%M UTC')}

STALE STREAMS DETECTED:
{chr(10).join(alerts)}

Expected:
- Tradier equity stream: <5 min (market hours only)
- GEX snapshots: <15 min (market hours only)
- Option chains (12 tickers): <15 min each (24/7 stream)

Market hours (ET): {'OPEN' if market_open else 'CLOSED'}
"""
        print("\nSending Telegram alert...")
        rc = send_telegram(msg)
        print(f"Telegram send return code: {rc}")
        return 1
    else:
        print("All streams healthy ✓")
        return 0


if __name__ == "__main__":
    sys.exit(main())