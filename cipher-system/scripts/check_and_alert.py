#!/usr/bin/env python3
"""Cipher stream health check + Telegram alert (FIXED VERSION)."""

import json
import sqlite3
import sys
import os
import subprocess
import selectors
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("/home/aarav/Aarav/cipher/cipher-system/data")
LIVE_OPTION_CHAINS_DIR = DATA / "live_option_chains"

SCANNER_TICKERS = (
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT",
    "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ",
)
ALL_TICKERS = SCANNER_TICKERS + ("SPY", "IWM")

# Market hours: 13:30–22:00 UTC (9:30 AM – 6:00 PM ET)
MARKET_OPEN_UTC = 13.5   # 13:30
MARKET_CLOSE_UTC = 22.0  # 22:00

THRESHOLDS = {
    "tradier": 5,   # minutes
    "gex": 15,
    "chains": 15,
}

def parse_dt(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

def is_market_hours(now):
    hour_float = now.hour + now.minute / 60.0
    return MARKET_OPEN_UTC <= hour_float <= MARKET_CLOSE_UTC

def age_min(dt, now):
    return (now - dt).total_seconds() / 60.0

def check_tradier(now):
    db = DATA / "tradier_stream.sqlite"
    if not db.exists():
        return None, True

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT MAX(last_event_at) FROM tradier_stream_runs").fetchone()

    latest = parse_dt(row[0]) if row and row[0] else None
    if not latest:
        return None, True

    age = age_min(latest, now)
    stale = is_market_hours(now) and age >= THRESHOLDS["tradier"]
    return age, stale

def check_gex(now):
    db = DATA / "gex_history.sqlite"
    if not db.exists():
        return None, True

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT MAX(captured_at) FROM gex_snapshots").fetchone()

    latest = parse_dt(row[0]) if row and row[0] else None
    if not latest:
        return None, True

    age = age_min(latest, now)
    stale = is_market_hours(now) and age >= THRESHOLDS["gex"]
    return age, stale

def check_chains(now):
    stale = []
    missing = []

    if not LIVE_OPTION_CHAINS_DIR.exists():
        return [(t, float("inf")) for t in ALL_TICKERS], list(ALL_TICKERS)

    for ticker in ALL_TICKERS:
        path = LIVE_OPTION_CHAINS_DIR / f"latest_{ticker}.json"
        if not path.is_file():
            missing.append(ticker)
            # Only treat missing scanner tickers as stale; SPY/IWM are expected to be missing
            if ticker in SCANNER_TICKERS:
                stale.append((ticker, float("inf")))
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            missing.append(ticker)
            if ticker in SCANNER_TICKERS:
                stale.append((ticker, float("inf")))
            continue

        ts = None
        for key in ("as_of", "timestamp", "fetched_at", "captured_at", "snapshot_time", "time"):
            if key in data:
                ts = data[key]
                break

        dt = parse_dt(ts)
        if not dt:
            missing.append(ticker)
            if ticker in SCANNER_TICKERS:
                stale.append((ticker, float("inf")))
            continue

        age = age_min(dt, now)
        if age >= THRESHOLDS["chains"]:
            stale.append((ticker, age))

    return stale, missing

def format_alert(now, tradier_age, tradier_stale, gex_age, gex_stale, stale_chains, missing_chains):
    market = is_market_hours(now)
    lines = [
        "Cipher data health change",
        f"Checked: {now.isoformat()} ({now.strftime('%a %H:%M ET')})",
        "",
    ]

    if stale_chains:
        all_stale = stale_chains + [(t, float("inf")) for t in missing_chains]
        finite_ages = [a for _, a in all_stale if a != float("inf")]
        max_age = max(finite_ages) if finite_ages else 0
        lines.append(
            f"LIVE_OPTION_CHAINS: STALE - all {len(ALL_TICKERS)} tickers "
            f"~{max_age:.0f} min old (threshold {THRESHOLDS['chains']} min, 24/7 stream)"
        )
        for i in range(0, len(all_stale), 4):
            chunk = all_stale[i:i+4]
            parts = []
            for t, a in chunk:
                if a == float("inf"):
                    parts.append(f"{t}: MISSING")
                else:
                    parts.append(f"{t}: {a:.1f} min")
            lines.append("  " + ", ".join(parts))

        fresh_times = []
        for t in ALL_TICKERS:
            path = LIVE_OPTION_CHAINS_DIR / f"latest_{t}.json"
            if path.is_file():
                try:
                    d = json.loads(path.read_text(encoding="utf-8"))
                    dt = parse_dt(d.get("as_of"))
                    if dt:
                        fresh_times.append(dt)
                except:
                    pass
        if fresh_times:
            last = max(fresh_times)
            lines.append(
                f"Last update: ~{last.isoformat()} ({last.strftime('%H:%M ET')}, near market close)"
            )

    tradier_status = "STALE" if tradier_stale else "OK"
    tradier_ctx = (
        "expected, outside market hours 9:30-16:00 ET"
        if not market else
        "within market hours"
    )
    lines.append(f"TRADIER: {tradier_status} - {tradier_age:.0f} min old ({tradier_ctx})")

    gex_status = "STALE" if gex_stale else "OK"
    gex_ctx = (
        "expected, outside market hours 9:30-16:10 ET"
        if not market else
        "within market hours"
    )
    lines.append(f"GEX: {gex_status} - {gex_age:.0f} min old ({gex_ctx})")

    mkt_status = "market open" if market else "market closed"
    lines.append(f"Current time: {now.strftime('%H:%M ET')} ({mkt_status})")

    return "\n".join(lines)

def hermes_binary():
    return (
        os.environ.get("HERMES_BIN")
        or shutil.which("hermes")
        or str(Path.home() / ".local" / "bin" / "hermes")
    )

def _contains_success(text: str) -> bool:
    success_markers = ("sent to telegram", "message sent", "delivery successful")
    return any(marker.lower() in text.lower() for marker in success_markers)

def send_hermes_message(message, target="telegram", timeout_seconds=90):
    timeout = float(timeout_seconds)
    command = [hermes_binary(), "send", "--to", target, message]
    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    if process.stdout is None:
        process.terminate()
        return 1

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + max(1.0, timeout)
    output_parts = []
    success = False

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            events = selector.select(timeout=min(0.5, remaining))
            for key, _ in events:
                line = key.fileobj.readline()
                if line:
                    output_parts.append(line)
                    print(line.rstrip(), flush=True)
                    if _contains_success("".join(output_parts)):
                        success = True
                        break
            if success:
                break
            return_code = process.poll()
            if return_code is not None:
                # Process exited - we already captured output via selector
                return 0 if return_code == 0 or _contains_success("".join(output_parts)) else return_code

        if success:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            return 0

        process.terminate()
        try:
            remainder, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            remainder, _ = process.communicate(timeout=5)
        if remainder:
            output_parts.append(remainder)
            print(remainder.rstrip(), flush=True)
        return 0 if _contains_success("".join(output_parts)) else 124
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

def main():
    now = datetime.now(timezone.utc)

    tradier_age, tradier_stale = check_tradier(now)
    gex_age, gex_stale = check_gex(now)
    stale_chains, missing_chains = check_chains(now)

    alert_needed = tradier_stale or gex_stale or bool(stale_chains)

    message = format_alert(
        now, tradier_age, tradier_stale,
        gex_age, gex_stale,
        stale_chains, missing_chains
    )

    print(message)
    print("---")

    if not alert_needed:
        print("No alerts needed.")
        return 0

    rc = send_hermes_message(message, target="telegram")
    print(f"Return code: {rc}")
    return 0 if rc == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
