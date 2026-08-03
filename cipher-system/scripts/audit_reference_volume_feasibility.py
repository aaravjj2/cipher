#!/usr/bin/env python3
"""Audit no-spend feasibility for an independent regular-session volume source.

This is intentionally a feasibility record, not a downloader or reconciliation
tool.  It must never add vendor prices to the Alpaca price dataset or cause a
backtest to run.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FROZEN_SYMBOLS = ["SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE"]
SAMPLE_DIR = ROOT / "data" / "reference_volume" / "raw" / "first_rate_samples"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample_evidence() -> list[dict[str, str]]:
    """Return only hashes and paths: raw vendor samples remain immutable."""
    return [
        {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
        for path in sorted(SAMPLE_DIR.glob("*.zip"))
    ]

def main() -> None:
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "frozen_cases": {"symbols": FROZEN_SYMBOLS, "period": "2023-01-01..2025-12-31", "session": "09:30-16:00 America/New_York; 391 bars inclusive of 16:00"},
        "excluded": {"Massive_Polygon": "not considered; entitlement cannot cover required requests", "Alpaca_daily": "rejected; daily session scope is not comparable"},
        "Databento": {
            "stage": "public_metadata_and_cost_check_complete",
            "minute_candidate": "EQUS.MINI",
            "finding": "minute OHLCV aggregates component ATS and Reg NMS venues, but public metadata does not establish full SIP-comparable coverage",
            "rejected_candidate": "EQUS.SUMMARY: explicitly consolidated but only daily schemas, so cannot be filtered to the required regular session",
            "free_credit_advertised_usd": 125,
            "status": "rejected_semantic_coverage_not_proven_no_pilot",
        },
        "FirstRate": {
            "stage": "public_schema_and_sample_check_complete",
            "sample_evidence": sample_evidence(),
            "schema": ["timestamp", "open", "high", "low", "close", "volume"],
            "timestamp_and_volume_semantics": "US Eastern minute-start timestamps; volume is individual shares; zero-volume bars omitted",
            "published_coverage": "AAPL and SPY product pages advertise 1-minute OHLCV across the target period with multi-venue aggregation and out-of-hours bars; exact 09:30-16:00 filtering is possible",
            "remaining_requirement": "exact no-purchase quote for all nine frozen symbols and written confirmation that volume is sufficiently comparable to Alpaca SIP",
            "status": "conditionally_feasible_but_blocked_pending_quote_and_semantic_confirmation",
        },
        "acceptance": {"immutable_raw_evidence": True, "vendor_patches_price_data": False, "volume_scaling_or_inference": False, "daily_bar_reference": False, "max_relative_difference": 0.05, "trading_or_signal_evaluation": False},
        "status": "blocked_pending_first_rate_quote_and_volume_semantic_confirmation",
    }
    output = ROOT / "data" / "governance" / f"reference_volume_feasibility_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(output)

if __name__ == "__main__": main()
