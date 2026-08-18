#!/usr/bin/env python3
from datetime import datetime, timezone, time

now = datetime.now(timezone.utc)
print(f"Current UTC: {now}")
print(f"Weekday: {now.weekday()} (0=Mon, 4=Fri)")

# Market hours: 13:30-20:00 UTC, Mon-Fri
market_open = time(13, 30)
market_close = time(20, 0)
current_time = now.time()
is_weekday = now.weekday() < 5
in_market_hours = is_weekday and market_open <= current_time < market_close
print(f"Market hours (13:30-20:00 UTC, Mon-Fri): {in_market_hours}")