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
            "stage": "excluded_by_no_purchase_policy",
            "sample_evidence": sample_evidence(),
            "schema": ["timestamp", "open", "high", "low", "close", "volume"],
            "timestamp_and_volume_semantics": "US Eastern minute-start timestamps; volume is individual shares; zero-volume bars omitted",
            "published_coverage": "AAPL and SPY product pages advertise 1-minute OHLCV across the target period with multi-venue aggregation and out-of-hours bars; exact 09:30-16:00 filtering is possible",
            "status": "excluded; full historical access is purchasable and is not an eligible path",
        },
        "LondonStrategicEdge": {
            "stage": "free_api_candidate",
            "published_access": "free key; REST API; CSV/Parquet slices; up to 10 downloads/hour and 1,000,000 rows/download",
            "required_before_ingestion": ["locally configured API key", "dataset metadata for each frozen symbol", "written or metadata evidence that minute volume is comparable to Alpaca SIP", "small immutable 2023 pilot"],
            "pilot_result": "2023-06-01: 4/9 pass; 2023-06-02: 3/9 pass at unchanged 5% threshold",
            "status": "rejected_pilot_material_volume_mismatch",
        },
        "HuggingFace_OHLCV_1m": {
            "stage": "free_price_only_candidate_pilot_complete",
            "dataset": "mito0o852/OHLCV-1m",
            "pilot_result": "2023-06-01: 9/9 volume pass but GE had 387 bars; 2023-06-02: 5/9 volume pass with repeated material mismatches",
            "status": "rejected_as_independent_volume_source_accepted_for_price_only_supplement",
        },
        "provider_neutral_pipeline": {
            "module": "core/research_platform/reference_volume.py",
            "importer": "scripts/import_reference_volume_csv.py",
            "reconciler": "scripts/reconcile_reference_volume_manifest.py",
            "documentation": "docs/reference_volume_reconciliation_pipeline.md",
            "status": "ready_for_future_authorized_reference_evidence",
            "unblocks_gate_by_itself": False,
        },
        "acceptance": {"immutable_raw_evidence": True, "vendor_patches_price_data": False, "volume_scaling_or_inference": False, "daily_bar_reference": False, "max_relative_difference": 0.05, "trading_or_signal_evaluation": False},
        "status": "blocked_reference_volume_access_after_free_sources_rejected",
    }
    output = ROOT / "data" / "governance" / f"reference_volume_feasibility_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(output)

if __name__ == "__main__": main()
