#!/usr/bin/env python3
"""
Comprehensive Cipher live data stream health check.
Checks:
1. Tradier equity stream (tradier_stream.sqlite) - <5 min during market hours
2. Alpaca GEX snapshots (gex_history.sqlite) - <15 min during market hours
3. Live option chains (live_option_chains/) - <5 min 24/7 (12 tickers)
Sends Telegram alert if any stream is stale (with market-hours gating for Tradier/GEX).
"""
import sqlite3
import sys
from datetime import datetime, timezone, time
from pathlib import Path

# Market hours: 13:30-20:00 UTC, Mon-Fri
MARKET_OPEN = time(13, 30)
MARKET_CLOSE = time(20, 0)

# Thresholds
TRADIER_THRESHOLD_MIN = 5    # during market hours
GEX_THRESHOLD_MIN = 15       # during market hours
CHAINS_THRESHOLD_MIN = 5     # 24/7

# Active tickers (12 scanner + SPY/QQQ/IWM)
ACTIVE_TICKERS = ['NVDA','MSFT','AAPL','AVGO','AMZN','IBIT','GOOGL','TSLA','META','MU','AMD','QQQ','SPY','IWM']
CHAINS_TICKERS = ['NVDA','MSFT','AAPL','AVGO','AMZN','IBIT','GOOGL','TSLA','META','MU','AMD','QQQ']

now = datetime.now(timezone.utc)
current_time = now.time()
is_weekday = now.weekday() < 5
in_market_hours = is_weekday and MARKET_OPEN <= current_time < MARKET_CLOSE

print(f"Check time (UTC): {now}")
print(f"Market hours (13:30-20:00 UTC, Mon-Fri): {in_market_hours}")
print()

alerts = []
warnings = []

# ============================================================
# 1. Check Tradier equity stream
# ============================================================
TRADIER_DB = Path('/home/aarav/Aarav/cipher/runtime/data/tradier_stream.sqlite')
if TRADIER_DB.exists():
    conn = sqlite3.connect(TRADIER_DB)
    c = conn.cursor()
    # Get latest quote timestamp from tradier_latest_quotes for equity symbols only (asset_class = 'underlying')
    c.execute("""
        SELECT symbol, updated_at FROM tradier_latest_quotes
        WHERE asset_class = 'underlying'
        ORDER BY updated_at DESC LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    
    if rows:
        latest_ts = rows[0][1]
        dt = datetime.fromisoformat(latest_ts.replace('Z', '+00:00'))
        age_min = (now - dt).total_seconds() / 60
        
        # Check if we have quotes for key symbols
        symbols = [r[0] for r in rows[:10]]
        key_symbols = {'SPY', 'QQQ', 'IWM', 'NVDA', 'AAPL', 'MSFT'}
        have_key = [s for s in key_symbols if s in symbols]
        
        status = 'OK' if age_min <= TRADIER_THRESHOLD_MIN else 'STALE'
        if status == 'STALE' and in_market_hours:
            alerts.append(f"Tradier equity stream: {age_min:.1f} min old (latest: {latest_ts}) - key symbols: {', '.join(have_key)}")
        elif status == 'STALE' and not in_market_hours:
            warnings.append(f"Tradier equity stream: {age_min:.1f} min old (off-hours, expected)")
        else:
            print(f"  Tradier equity: {age_min:.1f} min old [OK] - key symbols: {', '.join(have_key)}")
    else:
        if in_market_hours:
            alerts.append("Tradier equity stream: NO DATA")
        else:
            warnings.append("Tradier equity stream: NO DATA (off-hours)")
else:
    if in_market_hours:
        alerts.append("Tradier equity stream: DATABASE NOT FOUND")
    else:
        warnings.append("Tradier equity stream: DATABASE NOT FOUND (off-hours)")

# ============================================================
# 2. Check GEX snapshots
# ============================================================
GEX_DB = Path('/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite')
if GEX_DB.exists():
    conn = sqlite3.connect(GEX_DB)
    c = conn.cursor()
    placeholders = ','.join('?' for _ in ACTIVE_TICKERS)
    c.execute(f'SELECT ticker, MAX(captured_at) FROM gex_snapshots WHERE ticker IN ({placeholders}) GROUP BY ticker ORDER BY MAX(captured_at) DESC', ACTIVE_TICKERS)
    rows = c.fetchall()
    conn.close()
    
    gex_stale = []
    for ticker, ts in rows:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        age_min = (now - dt).total_seconds() / 60
        status = 'OK' if age_min <= GEX_THRESHOLD_MIN else 'STALE'
        print(f"  GEX {ticker}: {age_min:.1f} min old [{status}] ({ts})")
        if status == 'STALE':
            gex_stale.append(f"{ticker}: {age_min:.1f} min old")
    
    if gex_stale:
        if in_market_hours:
            alerts.append(f"STALE GEX SNAPSHOTS (threshold: {GEX_THRESHOLD_MIN} min):\n" + "\n".join(f"  {s}" for s in gex_stale))
        else:
            warnings.append(f"STALE GEX SNAPSHOTS (off-hours, expected):\n" + "\n".join(f"  {s}" for s in gex_stale))
    elif in_market_hours:
        print("  All GEX snapshots healthy ��")
else:
    if in_market_hours:
        alerts.append("GEX database: NOT FOUND")
    else:
        warnings.append("GEX database: NOT FOUND (off-hours)")

# ============================================================
# 3. Check Live option chains (24/7 stream)
# ============================================================
CHAINS_DIR = Path('/home/aarav/Aarav/cipher/runtime/data/live_option_chains')
chains_stale = []
chains_missing = []

if CHAINS_DIR.exists():
    for ticker in CHAINS_TICKERS:
        latest_file = CHAINS_DIR / f'latest_{ticker}.json'
        if latest_file.exists():
            # Get file modification time
            mtime = datetime.fromtimestamp(latest_file.stat().st_mtime, tz=timezone.utc)
            age_min = (now - mtime).total_seconds() / 60
            status = 'OK' if age_min <= CHAINS_THRESHOLD_MIN else 'STALE'
            print(f"  Chains {ticker}: {age_min:.1f} min old [{status}]")
            if status == 'STALE':
                chains_stale.append(f"{ticker}: {age_min:.1f} min old")
        else:
            chains_missing.append(ticker)
            print(f"  Chains {ticker}: MISSING [STALE]")
    
    if chains_stale or chains_missing:
        msg_parts = []
        if chains_stale:
            msg_parts.append(f"STALE LIVE OPTION CHAINS (threshold: {CHAINS_THRESHOLD_MIN} min, 24/7 stream):\n" + "\n".join(f"  {s}" for s in chains_stale))
        if chains_missing:
            msg_parts.append(f"MISSING LIVE OPTION CHAINS:\n" + "\n".join(f"  {t}" for t in chains_missing))
        # Live chains are 24/7 - always alert
        alerts.append("\n".join(msg_parts))
    else:
        print("  All live option chains healthy ��")
else:
    alerts.append("Live option chains directory: NOT FOUND")

# ============================================================
# Summary and alert
# ============================================================
print()
if warnings:
    print("WARNINGS (off-hours, expected):")
    for w in warnings:
        print(f"  {w}")
    print()

if alerts:
    print("ALERTS:")
    for a in alerts:
        print(f"  {a}")
    print()
    
    # Send Telegram alert
    sys.path.insert(0, str(Path('/home/aarav/Aarav/cipher/cipher-system/scripts')))
    from hermes_delivery import send_hermes_message
    
    msg = f"""Cipher Data Health Alert ���

Checked: {now.strftime('%Y-%m-%d %H:%M UTC')}
Market hours: {'YES' if in_market_hours else 'NO (off-hours)'}

"""
    for a in alerts:
        msg += f"\n{a}\n"
    
    if warnings:
        msg += "\n---\nOff-hours context (not alerting):\n"
        for w in warnings:
            msg += f"\n{w}\n"
    
    msg += """
Expected during market hours:
- Tradier equity quotes: <5 min
- GEX snapshots (14 tickers): <15 min
- Live option chains (12 tickers, 24/7): <5 min
"""
    
    print("Sending Telegram alert...")
    rc = send_hermes_message(msg, target='telegram')
    print(f"Telegram send return code: {rc}")
    sys.exit(1)
else:
    print("All streams healthy ��")
    sys.exit(0)