#!/usr/bin/env python3
"""Create an immutable preregistration before any new strategy outcomes exist."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research_platform.artifact_store import ArtifactStore  # noqa: E402


def main() -> None:
    protocol = {
        "schema_version": 1,
        "protocol_id": "volume_sensitive_cross_sectional_momentum_v1",
        "hypothesis": "A pre-specified cross-sectional momentum factor may be evaluated only on a fully volume-reconciled, independently held-out cohort.",
        "universe": "Holdout C: one qualified source, >=8 common tickers, >=12 strict independent 32+20-session origins.",
        "dates": "Frozen only after the qualified source passes existing cohort construction.",
        "features": ["20-session close return", "full-gate volume/liquidity fields only after reconciliation"],
        "parameters": {"lookback_sessions": 20, "outcome_sessions": 20, "context_sessions": 32},
        "benchmarks": ["equal_weight", "buy_and_hold"],
        "walk_forward": "chronological non-overlapping train/test folds; no optimizer access to later test folds",
        "promotion_thresholds": ["full gate passes", "walk-forward evidence passes", "LEAN replication reconciles", "prospective shadow passes"],
        "price_only_separation": "Price-only forecast studies cannot supply features, outcomes, promotion evidence, or a promotion path for this protocol.",
        "execution_authority": False,
        "status": "frozen_before_new_holdout_outcomes",
    }
    artifact = ArtifactStore(ROOT / "data" / "artifacts").put_json(protocol, metadata={"kind": "backtest_preregistration", "frozen": True})
    print(json.dumps({"artifact": artifact.to_dict(), "protocol": protocol}, indent=2))


if __name__ == "__main__":
    main()
