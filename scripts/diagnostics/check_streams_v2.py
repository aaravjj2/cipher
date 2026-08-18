import sqlite3
import os
import json
from datetime import datetime, timezone, timedelta

now = datetime.now(timezone.utc)
print(f"Check time (UTC): {now}")

# Check if market hours (9:30-16:00 ET = 13:30-20:00 UTC)
# For simplicity, assume market hours on weekdays 13:30-20:00 UTC
is_market_hours = now.weekday() < 5 and 13.5 <= now.hour + now.minute/60 <= 20
print(f"Market hours (approx): {is_market_hours}")

alerts = []

# 1. Check tradier_stream.sqlite
db_path = '/home/aarav/Aarav/cipher/cipher-system/data/tradier_stream.sqlite'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"\nTradier tables: {[t[0] for t in tables]}")
    
    # Check schema first
    for table in tables:
        t = table[0]
        cursor.execute(f'PRAGMA table_info({t})')
        cols = cursor.fetchall()
        print(f"  {t} columns: {[c[1] for c in cols]}")
    
    latest_overall = None
    for table in tables:
        t = table[0]
        # Find timestamp-like columns
        cursor.execute(f'PRAGMA table_info({t})')
        cols = [c[1] for c in cursor.fetchall()]
        ts_cols = [c for c in cols if 'time' in c.lower() or 'date' in c.lower() or 'ts' in c.lower()]
        if not ts_cols:
            ts_cols = cols  # fallback
        
        for col in ts_cols:
            try:
                cursor.execute(f'SELECT MAX({col}) FROM {t}')
                max_ts = cursor.fetchone()[0]
                if max_ts is not None:
                    # Parse timestamp
                    try:
                        if isinstance(max_ts, (int, float)):
                            dt = datetime.fromtimestamp(max_ts / 1000, tz=timezone.utc) if max_ts > 1e12 else datetime.fromtimestamp(max_ts, tz=timezone.utc)
                        else:
                            dt = datetime.fromisoformat(str(max_ts).replace('Z', '+00:00'))
                        print(f"  {t}.{col}: max = {dt}")
                        if latest_overall is None or dt > latest_overall:
                            latest_overall = dt
                        break
                    except Exception as e:
                        print(f"  {t}.{col}: parse error - {e}")
            except Exception as e:
                pass
    
    if latest_overall:
        age_minutes = (now - latest_overall).total_seconds() / 60
        print(f"  Latest overall: {latest_overall} (age: {age_minutes:.1f} min)")
        if is_market_hours and age_minutes > 5:
            alerts.append(f"Tradier stream stale: {age_minutes:.1f} min old (threshold: 5 min)")
        elif not is_market_hours and age_minutes > 60:
            alerts.append(f"Tradier stream very stale: {age_minutes:.1f} min old (outside market hours)")
    conn.close()
else:
    alerts.append("Tradier DB not found")

# 2. Check gex_history.sqlite
db_path = '/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"\nGEX tables: {[t[0] for t in tables]}")
    
    latest_overall = None
    for table in tables:
        t = table[0]
        cursor.execute(f'PRAGMA table_info({t})')
        cols = [c[1] for c in cursor.fetchall()]
        ts_cols = [c for c in cols if 'time' in c.lower() or 'date' in c.lower() or 'captur' in c.lower() or 'creat' in c.lower()]
        if not ts_cols:
            ts_cols = cols
        
        for col in ts_cols:
            try:
                cursor.execute(f'SELECT MAX({col}) FROM {t}')
                max_ts = cursor.fetchone()[0]
                if max_ts is not None:
                    try:
                        if isinstance(max_ts, (int, float)):
                            dt = datetime.fromtimestamp(max_ts / 1000, tz=timezone.utc) if max_ts > 1e12 else datetime.fromtimestamp(max_ts, tz=timezone.utc)
                        else:
                            dt = datetime.fromisoformat(str(max_ts).replace('Z', '+00:00'))
                        print(f"  {t}.{col}: max = {dt}")
                        if latest_overall is None or dt > latest_overall:
                            latest_overall = dt
                        break
                    except Exception as e:
                        pass
            except Exception as e:
                pass
    
    if latest_overall:
        age_minutes = (now - latest_overall).total_seconds() / 60
        print(f"  Latest overall: {latest_overall} (age: {age_minutes:.1f} min)")
        if is_market_hours and age_minutes > 15:
            alerts.append(f"GEX snapshots stale: {age_minutes:.1f} min old (threshold: 15 min)")
        elif not is_market_hours and age_minutes > 120:
            alerts.append(f"GEX snapshots very stale: {age_minutes:.1f} min old (outside market hours)")
    conn.close()
else:
    alerts.append("GEX history DB not found")

# 3. Check live_option_chains/
live_dir = '/home/aarav/Aarav/cipher/cipher-system/data/live_option_chains'
if os.path.exists(live_dir):
    tickers = ['NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ']
    print(f"\nLive option chains directory exists")
    
    stale_tickers = []
    for ticker in tickers:
        ticker_dir = os.path.join(live_dir, ticker)
        if os.path.exists(ticker_dir):
            files = sorted([f for f in os.listdir(ticker_dir) if f.endswith('.json')])
            if files:
                latest_file = files[-1]
                file_path = os.path.join(ticker_dir, latest_file)
                mtime = datetime.fromtimestamp(os.path.getmtime(file_path), tz=timezone.utc)
                age_minutes = (now - mtime).total_seconds() / 60
                print(f"  {ticker}: latest {latest_file} (mtime: {mtime}, age: {age_minutes:.1f} min)")
                if is_market_hours and age_minutes > 15:
                    stale_tickers.append(f"{ticker} ({age_minutes:.1f} min)")
            else:
                stale_tickers.append(f"{ticker} (no files)")
        else:
            stale_tickers.append(f"{ticker} (no dir)")
    
    if stale_tickers:
        alerts.append(f"Live option chains stale: {', '.join(stale_tickers)} (threshold: 15 min)")
else:
    alerts.append("Live option chains directory not found")

# Print summary
print("\n" + "="*50)
if alerts:
    print("ALERTS:")
    for alert in alerts:
        print(f"  - {alert}")
else:
    print("All streams healthy")

# Save alerts to file
os.makedirs('/home/aarav/Aarav/cipher/cipher-system/data/alerts', exist_ok=True)
with open('/home/aarav/Aarav/cipher/cipher-system/data/alerts/stream_health.json', 'w') as f:
    json.dump({
        'check_time': now.isoformat(),
        'market_hours': is_market_hours,
        'alerts': alerts
    }, f, indent=2)

print("\nResults saved to stream_health.json")