from datetime import datetime, time
from zoneinfo import ZoneInfo

ny = datetime.now(ZoneInfo('America/New_York'))
print(f'NY time: {ny}')
print(f'Weekday: {ny.weekday()} (0=Mon, 4=Fri, 5=Sat, 6=Sun)')
print(f'Is weekend: {ny.weekday() >= 5}')
print(f'Current ET time: {ny.time()}')

# Tradier: 7:30 - 17:00 ET
tradier_active = (ny.weekday() < 5) and (time(7, 30) <= ny.time() <= time(17, 0))
print(f'Tradier active: {tradier_active}')

# GEX: 9:30 - 16:10 ET
gex_active = (ny.weekday() < 5) and (time(9, 30) <= ny.time() <= time(16, 10))
print(f'GEX active: {gex_active}')

# Live option chains: 24/7
print(f'Live option chains: 24/7 (always active)')
