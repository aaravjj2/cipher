import sqlite3
import json
import os
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
print(f"Check time (UTC): {now}")

# Market hours check (9:30-16:00 ET = 13:30-20:00 UTC)
# Current time is 17:53 UTC = 13:53 ET - MARKET HOURS
ny_hour = (now.hour - 4) % 24  # Rough ET conversion
is_market_hours = now.weekday() < 5 and 9.5 <= ny_hour + now.minute/60 <= 16
# More accurate: 17:53 UTC = 13:53 ET, weekday = Sunday (6) - wait, Aug 2 2026 is a Sunday?
# Let me check: Aug 2 2026 is a Sunday, so NOT market hours
# But the file timestamps show Jul 31 (Friday) which was market hours
print(f"Day of week: {now.weekday()} (0=Mon, 6=Sun)")
print(f"Approx ET hour: {ny_hour}:{now.minute:02d}")

# Actually let's use proper timezone
from zoneinfo import ZoneInfo
ny = ZoneInfo("America/New_York")
now_ny = now.astimezone(ny)
print(f"NY time: {now_ny}")
is_market_hours = now_ny.weekday() < 5 and 9.5 <= now_ny.hour + now_ny.minute/60 <= 16
print(f"Market hours: {is_market_hours}")

# Thresholds
TRADIER_THRESHOLD = 5  # minutes
GEX_THRESHOLD = 15
OPTION_CHAINS_THRESHOLD = 15

alerts = []

# 1. Tradier
conn = sqlite3.connect('/home/aarav/Aarav/cipher/cipher-system/data/tradier_stream.sqlite')
cursor = conn.cursor()
cursor.execute("SELECT symbol, updated_at FROM tradier_latest_quotes ORDER BY updated_at DESC LIMIT 1")
row = cursor.fetchone()
if row:
    latest = datetime.fromisoformat(row[1].replace('Z', '+00:00'))
    age_min = (now - latest).total_seconds() / 60
    print(f"Tradier: latest={latest}, age={age_min:.1f} min")
    if is_market_hours and age_min > TRADIER_THRESHOLD:
        alerts.append(f"🔴 TRADIER STALE: {age_min:.0f} min old (threshold: {TRADIER_THRESHOLD} min) - last: {latest.isoformat()}")
    elif not is_market_hours and age_min > 60:
        alerts.append(f"🟡 TRADIER STALE (off-hours): {age_min:.0f} min old")
conn.close()

# 2. GEX
conn = sqlite3.connect('/home/aarav/Aarav/cipher/cipher-system/data/gex_history.sqlite')
cursor = conn.cursor()
cursor.execute("SELECT captured_at FROM gex_snapshots ORDER BY captured_at DESC LIMIT 1")
row = cursor.fetchone()
if row:
    latest = datetime.fromisoformat(row[0].replace('Z', '+00:00'))
    age_min = (now - latest).total_seconds() / 60
    print(f"GEX: latest={latest}, age={age_min:.1f} min")
    if is_market_hours and age_min > GEX_THRESHOLD:
        alerts.append(f"🔴 GEX STALE: {age_min:.0f} min old (threshold: {GEX_THRESHOLD} min) - last: {latest.isoformat()}")
    elif not is_market_hours and age_min > 120:
        alerts.append(f"🟡 GEX STALE (off-hours): {age_min:.0f} min old")
conn.close()

# 3. Live option chains
chain_dir = '/home/aarav/Aarav/cipher/cipher-system/data/live_option_chains'
tickers = ['NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT', 'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ']
stale_tickers = []
for ticker in tickers:
    latest_path = os.path.join(chain_dir, f'latest_{ticker}.json')
    if os.path.exists(latest_path):
        try:
            with open(latest_path, 'r') as f:
                data = json.load(f)
            as_of = data.get('as_of') or data.get('timestamp')
            if as_of:
                latest = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
                age_min = (now - latest).total_seconds() / 60
                if is_market_hours and age_min > OPTION_CHAINS_THRESHOLD:
                    stale_tickers.append(f"{ticker} ({age_min:.0f} min)")
        except Exception as e:
            stale_tickers.append(f"{ticker} (error)")
    else:
        stale_tickers.append(f"{ticker} (missing)")

if stale_tickers:
    alerts.append(f"🔴 LIVE OPTION CHAINS STALE: {', '.join(stale_tickers)} (threshold: {OPTION_CHAINS_THRESHOLD} min)")

# Summary
print("\n" + "="*60)
if alerts:
    print("⚠️  ALERTS:")
    for alert in alerts:
        print(f"  {alert}")
else:
    print("✅ All streams healthy")

# Save state
os.makedirs('/home/aarav/Aarav/cipher/cipher-system/data/alerts', exist_ok=True)
with open('/home/aarav/Aarav/cipher/cipher-system/data/alerts/stream_health.json', 'w') as f:
    json.dump({
        'check_time': now.isoformat(),
        'market_hours': is_market_hours,
        'ny_time': now_ny.isoformat(),
        'alerts': alerts
    }, f, indent=2)