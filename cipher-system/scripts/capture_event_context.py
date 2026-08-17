#!/usr/bin/env python3
"""Capture read-only corporate actions and estimated upcoming earnings."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import event_context  # noqa: E402
from core import earnings_sources  # noqa: E402
from core.market_research_agent import universe  # noqa: E402
from core.research_platform.market_data_providers import fetch_alpaca_corporate_actions  # noqa: E402


def main() -> int:
    symbols = universe()
    today = date.today()
    actions = fetch_alpaca_corporate_actions(
        symbols, start=(today - timedelta(days=30)).isoformat(),
        end=(today + timedelta(days=180)).isoformat(),
    )
    observed_at = datetime.now(timezone.utc).isoformat()
    earnings = earnings_sources.collect(symbols, observed_at=observed_at)
    payload = event_context.snapshot(symbols, actions, observed_at=observed_at, earnings=earnings)
    path = event_context.save(payload)
    print(json.dumps({"path": str(path), "symbols": len(symbols), "actions": len(actions),
                      "earnings_status": payload["earnings"]["status"],
                      "earnings_events": len(payload["earnings"].get("events") or []),
                      "read_only": True, "live_order_authority": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
