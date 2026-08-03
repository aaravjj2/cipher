#!/usr/bin/env python3
"""Construct and freeze Holdout C eligibility before any ranking outcomes."""
from __future__ import annotations
import json
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research_platform.market_quality import require_holdout_c_cohort
QUALITY = ROOT / "data" / "market_quality"
GOV = ROOT / "data" / "governance"
MIN_TICKERS, CONTEXT, HORIZON, MIN_ORIGINS = 8, 32, 20, 12

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", help="Explicit price-only scope artifact.")
    parser.add_argument("--period", default="2017-01 through 2019-12")
    args = parser.parse_args()
    scope_path = Path(args.scope) if args.scope else sorted(QUALITY.glob("alpaca_holdout_c_price_only_scope_*.json"))[-1]
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    all_days = sorted({row["date"] for row in scope["daily_results"]})
    eligible = {row["date"]: sorted(row["tickers"]) for row in scope["common_eligible_by_day"] if row["count"] >= MIN_TICKERS}
    blocks, current = [], []
    for day in all_days:
        if day in eligible:
            current.append(day)
        else:
            if current:
                blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    # A strict independent case consumes a 32-session context and a disjoint
    # 20-session outcome, matching the original 52-session window definition.
    findings = []
    for block in blocks:
        if len(block) >= CONTEXT + HORIZON:
            findings.append({"start": block[0], "end": block[-1], "sessions": len(block),
                             "minimum_common_tickers": min(len(eligible[day]) for day in block),
                             "strict_independent_origins": len(block) // (CONTEXT + HORIZON),
                             "origin_windows": [{"origin": block[offset + CONTEXT - 1], "outcome_start": block[offset + CONTEXT], "outcome_end": block[offset + CONTEXT + HORIZON - 1], "tickers": eligible[block[offset + CONTEXT - 1]]}
                                                for offset in range(0, len(block) - (CONTEXT + HORIZON) + 1, CONTEXT + HORIZON)]})
    best = max(findings, key=lambda item: (item["strict_independent_origins"], item["minimum_common_tickers"], item["sessions"]), default=None)
    cohort_gate = require_holdout_c_cohort(
        source_count=1,
        common_tickers=(best["minimum_common_tickers"] if best else 0),
        strict_independent_origins=(best["strict_independent_origins"] if best else 0),
    )
    passed = cohort_gate["eligible"]
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "scope_artifact": str(scope_path),
               "holdout_period": args.period, "requirements": {"minimum_common_tickers": MIN_TICKERS, "context_sessions": CONTEXT, "outcome_horizon_sessions": HORIZON, "minimum_strict_independent_origins": MIN_ORIGINS},
               "candidate_blocks": findings, "selected_block": best, "cohort_gate": cohort_gate, "pass": passed,
               "cohort_frozen_before_ranking_outcomes": passed, "ranking_outcomes_evaluated": False,
               "full_volume_reconciled_gate_changed": False, "volume_features_or_evaluation": False, "live_execution": False}
    GOV.mkdir(parents=True, exist_ok=True)
    output = GOV / f"holdout_c_alpaca_cohort_construction_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(output), "pass": passed, "selected_block": best}, indent=2))

if __name__ == "__main__":
    main()
