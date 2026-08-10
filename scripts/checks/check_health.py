#!/usr/bin/env python3
import json
import os
import sqlite3
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/aarav/Aarav/cipher")
DATA = ROOT / "cipher-system" / "data"
NY_TZ = "America/New_York"

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

def check_tradier():
    db_path = DATA / "tradier_stream.sqlite"
    if not db_path.is_file():
        return {"ok": False, "reason": "missing_db", "status": "MISSING", "detail": "tradier_stream.sqlite not found"}
    try:
        with sqlite3.connect(db_path) as db:
            rows = db.execute(
                "select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 5"
            ).fetchall()
        latest = max((parse_dt(row[1]) for row in rows), default=None)
        if not latest:
            return {"ok": False, "reason": "no_timestamps", "status": "STALE", "detail": "no latest timestamp"}
        age_min = (datetime.now(timezone.utc) - latest).total_seconds() / 60
        status = "healthy" if age_min < 5 else "STALE"
        return {
            "ok": True,
            "latest": latest,
            "age_min": round(age_min, 1),
            "status": status,
            "detail": f"latest {age_min:.1f} minutes old (threshold <5 min)",
            "rows": rows
        }
    except Exception as e:
        return {"ok": False, "reason": "error", "status": "ERROR", "detail": str(e)}

def check_gex():
    db_path = DATA / "gex_history.sqlite"
    if not db_path.is_file():
        return {"ok": False, "reason": "missing_db", "status": "MISSING", "detail": "gex_history.sqlite not found"}
    try:
        with sqlite3.connect(db_path) as db:
            rows = db.execute(
                "select ticker, captured_at from gex_snapshots order by captured_at desc limit 5"
            ).fetchall()
        latest = max((parse_dt(row[1]) for row in rows), default=None)
        if not latest:
            return {"ok": False, "reason": "no_timestamps", "status": "STALE", "detail": "no latest timestamp"}
        age_min = (datetime.now(timezone.utc) - latest).total_seconds() / 60
        status = "healthy" if age_min < 15 else "STALE"
        return {
            "ok": True,
            "latest": latest,
            "age_min": round(age_min, 1),
            "status": status,
            "detail": f"latest {age_min:.1f} minutes old (threshold <15 min)",
            "rows": rows
        }
    except Exception as e:
        return {"ok": False, "reason": "error", "status": "ERROR", "detail": str(e)}

def check_live_option_chains():
    live_dir = DATA / "live_option_chains"
    tickers = ["NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT", "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ"]
    
    results = {}
    stale_tickers = []
    for ticker in tickers:
        latest_files = sorted(live_dir.glob(f"latest_{ticker}.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if latest_files:
            mtime = datetime.fromtimestamp(latest_files[0].stat().st_mtime, tz=timezone.utc)
            age_min = (datetime.now(timezone.utc) - mtime).total_seconds() / 60
            status = "healthy" if age_min < 15 else "STALE"
            results[ticker] = {"status": status, "age_min": round(age_min, 1), "file": latest_files[0].name}
            if status == "STALE":
                stale_tickers.append(f"{ticker} {age_min:.1f} min")
        else:
            results[ticker] = {"status": "MISSING", "age_min": None}
            stale_tickers.append(f"{ticker} MISSING")
    
    overall_status = "healthy" if not stale_tickers else "STALE"
    detail = f"stale/missing: {', '.join(stale_tickers)}; threshold <15 min" if stale_tickers else "all tickers healthy"
    return {"status": overall_status, "detail": detail, "tickers": results}

def hermes_send(message, target="telegram"):
    hermes_bin = os.environ.get("HERMES_BIN") or shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")
    proc = subprocess.run(
        [hermes_bin, "send", "--to", target, message],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    if proc.stdout:
        print(proc.stdout.strip())
    return proc.returncode

def main():
    now = utcnow()
    print(f"Checking Cipher data stream health at {now} UTC")
    print(f"NY time: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    tradier = check_tradier()
    gex = check_gex()
    live_chains = check_live_option_chains()
    
    print(f"\nTradier: {tradier.get('status')} - {tradier.get('detail')}")
    print(f"GEX: {gex.get('status')} - {gex.get('detail')}")
    print(f"Live Option Chains: {live_chains.get('status')} - {live_chains.get('detail')}")
    
    # Build alert message
    alerts = []
    if tradier.get("status") in ("STALE", "MISSING", "ERROR"):
        alerts.append(f"TRADIER: {tradier.get('status')} - {tradier.get('detail')}")
        if tradier.get("rows"):
            alerts.append("  Latest: " + ", ".join(f"{r[0]} {r[1]}" for r in tradier["rows"][:3]))
    
    if gex.get("status") in ("STALE", "MISSING", "ERROR"):
        alerts.append(f"GEX: {gex.get('status')} - {gex.get('detail')}")
        if gex.get("rows"):
            alerts.append("  Latest: " + ", ".join(f"{r[0]} {r[1]}" for r in gex["rows"][:3]))
    
    if live_chains.get("status") in ("STALE", "MISSING", "ERROR"):
        alerts.append(f"LIVE_OPTION_CHAINS: {live_chains.get('status')} - {live_chains.get('detail')}")
    
    if alerts:
        message = "Cipher Data Stream Health Alert\n" + "="*40 + f"\nChecked: {now} UTC\n\n" + "\n".join(alerts) + "\n\nNote: Currently Saturday (non-market hours). Last capture was Friday Jul 31."
        print(f"\n--- Sending alert ---\n{message}\n")
        rc = hermes_send(message)
        if rc != 0:
            print(f"Failed to send alert (return code: {rc})")
        else:
            print("Alert sent successfully")
    else:
        print("\nAll streams healthy - no alert needed")
    
    # Print detailed results
    print("\n=== Detailed Results ===")
    print(json.dumps({
        "tradier": tradier,
        "gex": gex,
        "live_option_chains": live_chains
    }, indent=2, default=str))

if __name__ == "__main__":
    main()