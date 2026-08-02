#!/usr/bin/env python3
"""Close the Alpaca recovery attempt without weakening Holdout C rules."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUALITY, GOV = ROOT / "data" / "market_quality", ROOT / "data" / "governance"

def latest(directory: Path, pattern: str) -> Path:
    return sorted(directory.glob(pattern))[-1]

def main() -> None:
    scope = latest(QUALITY, "alpaca_holdout_c_price_only_scope_*.json")
    cohort = latest(GOV, "holdout_c_alpaca_cohort_construction_*.json")
    freeze = latest(GOV, "holdout_c_alpaca_panel_freeze_*.json")
    diagnostic = sorted((QUALITY / "alpaca_holdout_c_pilot").glob("pilot_report_*.json"))[-2:]
    scope_data, cohort_data = json.loads(scope.read_text()), json.loads(cohort.read_text())
    bad_days = []
    for day in scope_data["common_eligible_by_day"]:
        if day["count"] < 8:
            bad_days.append(day)
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "status": "blocked_source_coverage_insufficient",
               "provider": "Alpaca SIP", "feed": "sip", "period": "2017-01 through 2019-12", "raw_regular_sessions_persisted": 746,
               "requirements_unchanged": {"minimum_common_tickers": 8, "strict_independent_origins": 12, "context_sessions": 32, "outcome_horizon_sessions": 20,
                                          "price_only_gate": "exactly 391 bars plus close ratio strictly between 0.5 and 2.0", "full_volume_gate_changed": False},
               "observed_result": {"maximum_common_tickers": max(x["count"] for x in scope_data["common_eligible_by_day"]),
                                   "maximum_strict_independent_origins": cohort_data["selected_block"]["strict_independent_origins"],
                                   "strongest_block": cohort_data["selected_block"], "common_universe_break_dates": bad_days},
               "root_cause": "Provider responses contain incomplete minute sessions across broad, pre-outcome diagnostic securities on 2018-05-02, 2018-05-03, and 2019-08-12; no filling, vendor mixing, or gate relaxation was performed.",
               "artifacts": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [scope, cohort, freeze, *diagnostic]},
               "ranking_study_run": False, "ranking_outcomes_evaluated": False, "paper_or_live_execution": False,
               "next_required_action": "Obtain a single higher-quality licensed historical source for the same frozen 2017-2019 panel, pilot it, then rerun this unchanged gate and cohort construction before any ranking evaluation."}
    output = QUALITY / f"alpaca_holdout_c_recovery_closeout_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)

if __name__ == "__main__":
    main()
