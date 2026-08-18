#!/usr/bin/env python3
import sqlite3
import os
import glob
import json
from datetime import datetime, timezone

print("=" * 60)
print("CIPHER STREAM HEALTH CHECK")
print("=" * 60)

now = datetime.now(timezone.utc)
alerts = []

# Check tradier_stream.sqlite - tradier_latest_quotes table
conn = sqlite3.connect('cipher-system/data/tradier_stream.sqlite')
cursor = conn.cursor()
cursor.execute('SELECT MAX(provider_ts) FROM tradier_latest_quotes WHERE provider_ts IS NOT NULL')
max_ts = cursor.fetchone()[0]
conn.close()
if max_ts:
    dt = datetime.fromisoformat(max_ts.replace('Z', '+00:00'))
    age_min = (now - dt).total_seconds() / 60
    status = "OK" if age_min < 5 else "STALE"
    print(f'Tradier equity stream: latest quote at {dt} UTC (age: {age_min:.1f} min) [{status}]')
    if age_min >= 5:
        alerts.append(f"Tradier equity stream STALE: {age_min:.1f} min old (last: {dt} UTC)")
else:
    print('Tradier equity stream: NO DATA')
    alerts.append("Tradier equity stream: NO DATA")

# Check gex_history.sqlite
conn = sqlite3.connect('cipher-system/data/gex_history.sqlite')
cursor = conn.cursor()
cursor.execute('SELECT MAX(captured_at) FROM gex_snapshots WHERE captured_at IS NOT NULL')
max_ts = cursor.fetchone()[0]
conn.close()
if max_ts:
    dt = datetime.fromisoformat(max_ts.replace('Z', '+00:00'))
    age_min = (now - dt).total_seconds() / 60
    status = "OK" if age_min < 15 else "STALE"
    print(f'GEX history: latest snapshot at {dt} UTC (age: {age_min:.1f} min) [{status}]')
    if age_min >= 15:
        alerts.append(f"GEX history STALE: {age_min:.1f} min old (last: {dt} UTC)")
else:
    print('GEX history: NO DATA')
    alerts.append("GEX history: NO DATA")

# Check live_option_chains
chain_dir = 'cipher-system/data/live_option_chains'
files = glob.glob(os.path.join(chain_dir, 'latest_*.json'))
expected_tickers = ['NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ']

if files:
    latest = max(files, key=os.path.getmtime)
    mtime = datetime.fromtimestamp(os.path.getmtime(latest), tz=timezone.utc)
    age_min = (now - mtime).total_seconds() / 60
    status = "OK" if age_min < 10 else "STALE"
    print(f'Live option chains: latest file {os.path.basename(latest)} at {mtime} UTC (age: {age_min:.1f} min) [{status}]')
    
    # Count unique tickers
    tickers = set()
    for f in files:
        ticker = os.path.basename(f).replace('latest_', '').replace('.json', '')
        tickers.add(ticker)
    print(f'  Tickers with chains: {sorted(tickers)} ({len(tickers)} total)')
    
    # Check each expected ticker's age
    print("  Per-ticker freshness:")
    stale_tickers = []
    missing_tickers = []
    for ticker in expected_tickers:
        fpath = os.path.join(chain_dir, f'latest_{ticker}.json')
        if os.path.exists(fpath):
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
            age_min = (now - mtime).total_seconds() / 60
            ticker_status = "OK" if age_min < 10 else "STALE"
            print(f'    {ticker}: {age_min:.1f} min [{ticker_status}]')
            if age_min >= 10:
                stale_tickers.append(f"{ticker} ({age_min:.1f} min)")
        else:
            print(f'    {ticker}: MISSING')
            missing_tickers.append(ticker)
    
    if stale_tickers:
        alerts.append(f"Live option chains STALE tickers: {', '.join(stale_tickers)}")
    if missing_tickers:
        alerts.append(f"Live option chains MISSING tickers: {', '.join(missing_tickers)}")
else:
    print('Live option chains: NO FILES')
    alerts.append("Live option chains: NO FILES")

print("=" * 60)

# Determine if we're in market hours (roughly 13:30-20:00 UTC for US market)
# But also consider pre-market (08:00-13:30 UTC) and after-hours (20:00-00:00 UTC)
# For now, check if any stream is stale and alert
if alerts:
    print("ALERTS:")
    for a in alerts:
        print(f"  - {a}")
    
    # Send Telegram alert if configured
    # Check for telegram bot token and chat id
    import os
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    
    if bot_token and chat_id:
        import urllib.request
        import urllib.parse
        
        message = "���� Cipher Stream Health Alert\n\n" + "\n".join(f"• {a}" for a in alerts)
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }).encode()
        
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.load(resp)
                if result.get('ok'):
                    print("Telegram alert sent successfully")
                else:
                    print(f"Telegram alert failed: {result}")
        except Exception as e:
            print(f"Telegram alert error: {e}")
    else:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set)")
else:
    print("All streams healthy ��")

print("=" * 60)