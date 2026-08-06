from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from core.research_platform.auxiliary_research_refresh import summarize_auxiliary_reports
from core.research_platform.factor_rotation import (
    FactorRotationSpec,
    adaptive_factor_rotation_specs,
    build_desired_weights,
    default_factor_rotation_specs,
    simulate_rotation,
)
from core.research_platform.regime_allocator import (
    AllocatorSpec,
    apply_allocator,
    block_sign_flip_p_value,
    build_allocator_weights,
)

ROOT = Path(__file__).resolve().parents[1]


def test_regime_allocator_weights_do_not_depend_on_future_returns():
    dates = pd.bdate_range("2020-01-02", periods=320)
    frame = pd.DataFrame(
        {
            "trend": np.linspace(0.0002, 0.0010, len(dates)),
            "reversal": np.linspace(0.0008, -0.0002, len(dates)),
        },
        index=dates,
    )
    spec = AllocatorSpec("test", "dynamic", lookback=63, rebalance=21, top_k=1, objective="sharpe")
    baseline = build_allocator_weights(frame, spec)
    cutoff = dates[220]
    altered = frame.copy()
    altered.loc[altered.index > cutoff, "trend"] = -0.20
    altered.loc[altered.index > cutoff, "reversal"] = 0.20
    changed = build_allocator_weights(altered, spec)
    pd.testing.assert_frame_equal(baseline.loc[:cutoff], changed.loc[:cutoff])


def test_regime_allocator_charges_switching_cost_without_exceeding_gross_one():
    dates = pd.bdate_range("2025-01-02", periods=6)
    returns = pd.DataFrame(0.0, index=dates, columns=["a", "b"])
    weights = pd.DataFrame(
        {
            "a": [1.0, 1.0, 0.0, 0.0, 1.0, 1.0],
            "b": [0.0, 0.0, 1.0, 1.0, 0.0, 0.0],
        },
        index=dates,
    )
    result = apply_allocator(returns, weights, switching_cost_bps=50.0)
    assert result["total_turnover"] > 0
    assert float(result["returns"].sum()) < 0
    assert bool((result["weights"].abs().sum(axis=1) <= 1.0 + 1e-12).all())


def test_factor_rotation_is_shifted_to_following_session_open():
    dates = pd.bdate_range("2024-01-02", periods=80)
    closes = pd.DataFrame(
        {
            "SPY": np.linspace(100.0, 120.0, len(dates)),
            "QQQ": np.linspace(100.0, 140.0, len(dates)),
            "BIL": np.linspace(100.0, 100.5, len(dates)),
        },
        index=dates,
    )
    opens = closes * 1.001
    categories = {
        "equity_core": ["SPY", "QQQ"],
        "sectors": [],
        "international": [],
        "bonds": ["BIL"],
        "real_assets": [],
        "defensive": ["BIL"],
    }
    spec = FactorRotationSpec("test", "rank", lookback=20, skip=0, top_k=1, rebalance=5)
    desired = build_desired_weights(closes, spec, categories)
    result = simulate_rotation(opens, desired, slippage_bps_per_side=0.0)
    pd.testing.assert_frame_equal(result["weights"], desired.shift(1).fillna(0.0))
    assert bool((result["weights"].abs().sum(axis=1) <= 1.0 + 1e-12).all())


def test_factor_rotation_grid_is_bounded_unique_and_matches_frozen_raw_lineage():
    initial = default_factor_rotation_specs()
    adaptive = adaptive_factor_rotation_specs()
    assert len(initial) == 16
    assert len(adaptive) == 24
    all_specs = initial + adaptive
    assert len({spec.strategy_id for spec in all_specs}) == 40
    with sqlite3.connect(ROOT / "data" / "governance" / "research_registry.sqlite") as db:
        row = db.execute(
            """
            select r.payload_json
            from dataset_raw_objects l
            join raw_objects r on r.raw_object_id=l.raw_object_id
            where l.dataset_id=?
            limit 1
            """,
            ("ds_796df562a29d2b01d2e1ca24",),
        ).fetchone()
    assert row is not None
    raw = json.loads(row[0])["request_metadata"]["strategy_grid_pre_download"]
    assert [spec.strategy_id for spec in initial] == [item["strategy_id"] for item in raw]


def test_factor_rotation_overlays_keep_weights_bounded():
    dates = pd.bdate_range("2019-01-02", periods=620)
    base = np.arange(len(dates), dtype=float)
    closes = pd.DataFrame(
        {
            "SPY": 100.0 + base * 0.08 + np.sin(base / 17.0),
            "QQQ": 100.0 + base * 0.11 + np.sin(base / 13.0),
            "XLK": 100.0 + base * 0.10 + np.cos(base / 11.0),
            "EFA": 100.0 + base * 0.04 + np.sin(base / 23.0),
            "TLT": 100.0 + base * 0.02 + np.cos(base / 19.0),
            "BIL": 100.0 + base * 0.003,
            "GLD": 100.0 + base * 0.05 + np.cos(base / 29.0),
        },
        index=dates,
    )
    categories = {
        "equity_core": ["SPY", "QQQ"],
        "sectors": ["XLK"],
        "international": ["EFA"],
        "bonds": ["TLT", "BIL"],
        "real_assets": ["GLD"],
        "defensive": ["TLT", "BIL", "GLD"],
    }
    spec = FactorRotationSpec(
        "bounded",
        "category_core_satellite",
        lookback=252,
        skip=0,
        top_k=1,
        core_weight=0.70,
        relative_overlay_lookback=126,
        target_volatility=0.12,
    )
    weights = build_desired_weights(closes, spec, categories)
    assert not weights.isna().any().any()
    assert bool((weights.abs().sum(axis=1) <= 1.0 + 1e-10).all())
    assert bool((weights >= -1e-12).all().all())


def test_block_sign_flip_preserves_guarded_one_sided_behavior():
    dates = pd.bdate_range("2020-01-02", periods=252)
    positive = pd.Series(0.002, index=dates)
    nonpositive = pd.Series(0.0, index=dates)
    assert block_sign_flip_p_value(positive, seed=7, block_size=21, simulations=1024) < 0.10
    assert block_sign_flip_p_value(nonpositive, seed=7, block_size=21, simulations=1024) == 1.0


def test_auxiliary_summary_has_no_promotion_authority():
    summary = summarize_auxiliary_reports(
        {"summary": {"allocator_specs": 22, "screening_passes": 0, "leader_name": "a"}, "effective_hypothesis_count": 22},
        {"summary": {"strategies": 40, "screening_passes": 0, "leader_name": "b"}, "effective_hypothesis_count": 38},
        {"dominant_failure_category": "benchmark_consistency", "aggregate_failure_category_counts": {"benchmark_consistency": 3}},
    )
    assert summary["regime_allocator_specs"] == 22
    assert summary["factor_rotation_effective_hypotheses"] == 38
    assert summary["promotion_eligible"] is False
    assert summary["allowed_claim"] == "no_auxiliary_strategy_clears_complete_contract"


def test_current_auxiliary_artifacts_preserve_safety_boundaries():
    status = json.loads(
        (ROOT / "data" / "governance" / "strategy_research" / "latest_auxiliary_research_status.json").read_text(encoding="utf-8")
    )
    diagnostics = json.loads(
        (ROOT / "data" / "governance" / "research_failure_attribution.json").read_text(encoding="utf-8")
    )
    assert status["status"] in {"seeded_existing_reports", "not_due_inputs_unchanged", "completed"}
    assert status["summary"]["regime_allocator_passes"] == 0
    assert status["summary"]["factor_rotation_passes"] == 0
    assert status["summary"]["factor_rotation_raw_lineage_freeze_verified"] is True
    assert status["branch_actions"] == {
        "factor_rotation": "not_due_inputs_unchanged",
        "regime_allocator": "not_due_inputs_unchanged",
    }
    assert status["diagnostic_action"] == "not_due_inputs_unchanged"
    assert status["processes"] is None
    assert status["automatic_promotion"] is False
    assert status["source_code_auto_edit"] is False
    assert status["execution_authority"] is False
    assert diagnostics["automatic_promotion"] is False
    assert diagnostics["execution_authority"] is False
