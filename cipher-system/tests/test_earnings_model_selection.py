from __future__ import annotations

import numpy as np
import pandas as pd

from earnings_model.model import FEATURE_COLS, MODEL_VERSION, train_earnings_models


def _dataset(rows: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    frame = pd.DataFrame({name: rng.normal(size=rows) for name in FEATURE_COLS})
    frame["earnings_date"] = pd.date_range("2020-01-01", periods=rows, freq="D").astype(str)
    frame["target_day1_up"] = rng.integers(0, 2, rows)
    frame["target_day5_up"] = rng.integers(0, 2, rows)
    frame["target_reversal"] = rng.integers(0, 2, rows)
    frame["target_abs_gap"] = np.maximum(0.1, 2.0 + frame[FEATURE_COLS[0]] * 0.8)
    return frame


def test_model_selection_uses_three_chronological_windows_and_baselines():
    result = train_earnings_models(_dataset(), save_artifacts=False)
    assert result["model_version"] == MODEL_VERSION
    assert (result["train_samples"], result["validation_samples"], result["test_samples"]) == (360, 120, 120)
    direction = result["models"]["day5_direction"]
    assert direction["selected_candidate"] in direction["validation_candidates"]
    assert direction["baseline_accuracy"] is not None
    assert direction["baseline_brier"] is not None
    assert direction["trade_eligible"] is False
    assert result["strategy_gate"]["paper_entry_allowed"] is False


def test_expected_gap_requires_holdout_improvement_over_naive_median():
    result = train_earnings_models(_dataset(), save_artifacts=False)
    gap = result["models"]["expected_abs_gap"]
    assert gap["mae_pct"] < gap["baseline_mae_pct"]
    assert gap["mae_improvement_pct"] >= 5.0
    assert gap["trade_eligible"] is True
