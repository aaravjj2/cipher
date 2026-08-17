#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

import app  # noqa: E402
from core.weekly_radar_audit import RadarIdea, evaluate_radar  # noqa: E402


IDEAS = (
    RadarIdea("SPY", 774.55, (778.78, 782.70), (767.16, 760.66), option_note="780-790 calls / 770-755 puts"),
    RadarIdea("AAPL", 316.56, (323.55, 335.03), (307.41, 298.63), option_note="325-335 calls / 310-305 puts"),
    RadarIdea("AMZN", 278.70, (286.85, 295.13), (269.63, 263.09), option_note="290-295 calls / 270-255 puts"),
    RadarIdea("NFLX", 74.94, (78.28, 82.17), (72.40, 70.34), option_note="78-80 calls / 72-70 puts"),
    RadarIdea("META", 603.10, (626.66, 642.16), (579.54, 557.22), option_note="625-635 calls / 580-560 puts"),
    RadarIdea("GOOGL", 354.94, (372.73, 384.34), (344.81, 328.99), option_note="375-380 calls / 345-330 puts"),
    RadarIdea("TSLA", 334.16, (346.32, 367.47), bullish_only=True, option_note="350 calls"),
    RadarIdea("ADBE", 276.10, bullish_only=True, option_note="280-290 calls"),
    RadarIdea("AAOI", 140.50, bullish_only=True, option_note="150-170 calls"),
    RadarIdea("LULU", 129.03, bullish_only=True, option_note="130-140 calls"),
)


def main() -> int:
    bars = {}
    feeds = {}
    for idea in IDEAS:
        result = app.bars(idea.ticker, "5m", limit=1000, start="2026-08-10")
        bars[idea.ticker] = result.get("bars") or []
        feeds[idea.ticker] = result.get("feed")
    payload = evaluate_radar(IDEAS, bars, start="2026-08-10", end="2026-08-14")
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    payload["data_source"] = {ticker: f"Alpaca {feed.upper()} 5-minute bars" for ticker, feed in feeds.items()}
    payload["option_quote_coverage"] = {
        "status": "INSUFFICIENT_FOR_WEEKLY_OPTION_PNL",
        "detail": (
            "The exact Aug-14 contracts in the email were not captured from the Monday/Tuesday trigger times. "
            "Observed histories begin Aug 13-14 for the subset present, so weekly entry-to-exit returns would be fabricated."
        ),
    }
    output = ROOT / "data" / "weekly_radar_audits"
    output.mkdir(parents=True, exist_ok=True)
    (output / "latest_aug10_2026_radar_audit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
