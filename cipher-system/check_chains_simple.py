#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

LIVE_OPTION_CHAINS_DIR = Path('/home/aarav/Aarav/cipher/runtime/data/live_option_chains')

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

for f in sorted(LIVE_OPTION_CHAINS_DIR.glob('latest_*.json')):
    try:
        d = json.loads(f.read_text())
        ts = parse_dt(d.get('as_of') or d.get('timestamp'))
        if ts:
            age = (datetime.now(timezone.utc) - ts).total_seconds() / 60
            print(f"{f.name}: {ts} (age: {age:.1f} min)")
        else:
            print(f"{f.name}: NO TIMESTAMP")
    except Exception as e:
        print(f"{f.name}: ERROR {e}")
