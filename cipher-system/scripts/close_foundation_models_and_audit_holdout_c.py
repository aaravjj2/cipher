#!/usr/bin/env python3
"""Persist foundation-model retirement decisions and Holdout C availability.

This does not delete model code or artifacts.  It records that rejected
formulations are excluded from ordinary research workflows while preserving
them for reproducibility.  The audit is price-only and cannot enable trading.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUALITY = ROOT / "data" / "market_quality"
GOVERNANCE = ROOT / "data" / "governance"
DOCS = ROOT / "docs"


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    closeout = sorted(QUALITY.glob("cross_sectional_ranking_closeout_*.json"))[-1]
    registry = {
        "schema_version": 1, "updated_at": now,
        "default_workflow_policy": "Components marked rejected_current_formulation or archived_reproducibility_only must not be invoked by ordinary Cipher research workflows.",
        "components": [
            {"name": "timesfm_raw_price", "target_formulation": "terminal raw close", "research_status": "rejected_current_formulation", "reason": "failed prior gated raw-price studies", "supporting_artifacts": ["market_quality/expanded_price_only_summary_20260802T1858Z.json"], "conditions_before_reconsideration": "materially new, preregistered formulation and a new untouched holdout"},
            {"name": "timesfm_cross_sectional_ranking", "target_formulation": "cross-sectional future-return rank", "research_status": "rejected_current_formulation", "reason": "negative sealed Holdout B IC versus momentum_20", "supporting_artifacts": [f"market_quality/{closeout.name}"], "conditions_before_reconsideration": "new formulation, not a rerun of this hypothesis"},
            {"name": "kronos_mini_cross_sectional_ranking", "target_formulation": "cross-sectional future-return rank", "research_status": "rejected_current_formulation", "reason": "negative sealed Holdout B IC versus momentum_20", "supporting_artifacts": [f"market_quality/{closeout.name}"], "conditions_before_reconsideration": "materially new preregistered formulation and untouched holdout"},
            {"name": "kronos_mini_raw_price", "target_formulation": "terminal raw close", "research_status": "archived_reproducibility_only", "reason": "retained to reproduce prior research but excluded from default workflows", "supporting_artifacts": ["market_quality/expanded_price_only_summary_20260802T1858Z.json"], "conditions_before_reconsideration": "separate formulation and independent validation"},
            {"name": "momentum_20", "target_formulation": "20-session cross-sectional future-return rank", "research_status": "exploratory_unvalidated", "reason": "positive comparator in prior data but no adequate untouched Holdout C", "supporting_artifacts": [f"market_quality/{closeout.name}"], "conditions_before_reconsideration": "pre-registered Holdout C with >=12 strict-independent origins"},
        ],
        "promotion_eligible": False, "live_execution": False,
    }
    contamination = {
        "schema_version": 1, "created_at": now,
        "gate": "price_forecast_research_only_no_volume_features",
        "studies": [
            {"name": "small_n_and_expanded_model_studies", "period": "2020-06..2020-12, 2022-02..2022-05", "classification": "development_contaminated", "artifacts": ["expanded_price_only_results_20260802T1830Z.json"]},
            {"name": "point_forecast_holdout_a", "period": "2015-04..2015-08", "classification": "holdout_a_sealed", "artifacts": ["forecast_utility_holdout_results_20260802T1918Z.json"]},
            {"name": "ranking_development", "period": "2020-06..2020-12, 2022-02..2022-05", "classification": "development_contaminated", "artifacts": ["cross_sectional_ranking_development_20260802T194605Z.json"]},
            {"name": "ranking_holdout_b", "period": "2016-04..2016-08", "classification": "holdout_b_sealed", "artifacts": ["cross_sectional_ranking_holdout_b_results_20260802T194608Z.json"]},
            {"name": "forecast_utility_decomposition", "period": "uses prior realized-outcome studies", "classification": "development_contaminated", "artifacts": ["forecast_utility_holdout_summary_20260802T1920Z.json"]},
        ],
        "untouched_catalog_blocks": [{"period": "2026-03", "classification": "insufficient_common_universe_coverage", "reason": "one calendar month cannot provide 32-session lookback plus 20-session outcome, much less 12 strict-independent origins"}],
        "holdout_c_requirement": {"strict_independent_origins_per_horizon": 12, "minimum_tickers_per_origin": 8, "minimum_ticker_origin_observations": 150},
        "current_shortage": {"maximum_available_strict_20_session_origins_in_any_continuous_catalog_block": 2, "shortfall_origins": 10, "viable_holdout_c": False, "required_action": "ingest one continuous untouched block of at least 24 months, rerun price-only gate and contamination audit before selection"},
        "holdout_a_accessed": False, "holdout_b_reused": False, "volume_used": False, "full_volume_gate_changed": False,
    }
    GOVERNANCE.mkdir(parents=True, exist_ok=True); QUALITY.mkdir(parents=True, exist_ok=True); DOCS.mkdir(parents=True, exist_ok=True)
    registry_path = GOVERNANCE / "research_status_registry.json"
    audit_path = QUALITY / f"holdout_c_contamination_availability_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    audit_path.write_text(json.dumps(contamination, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (DOCS / "foundation_model_branch_closeout.md").write_text("# Foundation-Model Branch Close-Out\n\nKronos-mini and TimesFM are retained for reproducibility but are not default research components. Their raw-price/ranking formulations are not supported by the sealed evidence. `momentum_20` remains exploratory only until an adequately sized, untouched Holdout C is pre-registered and evaluated.\n", encoding="utf-8")
    print(json.dumps({"registry": str(registry_path), "audit": str(audit_path), "viable_holdout_c": False, "shortfall_origins": 10}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
