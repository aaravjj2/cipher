"""A swept parameter must reach the detector, and must not share cached state.

Two silent failures were possible when the grids grew beyond entry timing:

1. `detector_params` was built from three hardcoded fields
   (`arm_minutes`, `sig_mult`, `clps_thresh`). Any other swept key -- `mode`, the EMA lengths,
   `trend_len` -- was dropped on the floor, so a full-session grid would have run every
   candidate as EOD Focus with default indicators and reported identical numbers under
   different labels.

2. The state cache was keyed on those same three fields. Even with the parameters passed
   through, two candidates differing only in an unkeyed field would share precomputed
   indicator states, and the second would be scored with the first's indicators.

Neither raises. Both produce a plausible report, which is why they are tested rather than
left to review.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

from scripts import experiment_obsidian_eod as experiment  # noqa: E402
from scripts.experiment_obsidian_eod import (  # noqa: E402
    INDICATOR_VARIANTS,
    STRATEGY_ONLY_KEYS,
    TREND_LENGTHS,
    _detector_params,
    _params_key,
)
from scripts.run_obsidian_pine_ytd import PINE_DEFAULTS  # noqa: E402


def test_every_swept_signal_parameter_reaches_the_detector():
    row = {
        "mode": "Full Session",
        "sig_mult": 1.0,
        "clps_thresh": 0.5,
        "trend_len": 200,
        "fast_len": 12,
        "slow_len": 26,
        "sig_len": 9,
        "entry_delay": 2,
        "min_signal_lead": 4,
    }
    detector = _detector_params(row)
    for key in ("mode", "sig_mult", "clps_thresh", "trend_len", "fast_len", "slow_len", "sig_len"):
        assert detector[key] == row[key], f"{key} was not forwarded to the detector"


def test_entry_timing_stays_out_of_the_detector_parameters():
    """Entry timing belongs to the strategy; leaking it in would change the cache key for
    candidates whose signals are identical, silently tripling the sweep's cost."""
    row = {"sig_mult": 1.1, "clps_thresh": 0.6, "entry_delay": 3, "min_signal_lead": 5}
    detector = _detector_params(row)
    for key in STRATEGY_ONLY_KEYS:
        assert key not in detector, f"{key} should not be a detector parameter"


def test_unspecified_parameters_fall_back_to_the_pasted_defaults():
    detector = _detector_params({"sig_mult": 1.0})
    assert detector["mode"] == PINE_DEFAULTS["mode"]
    assert detector["clps_thresh"] == PINE_DEFAULTS["clps_thresh"]
    assert detector["sig_mult"] == 1.0


def test_cache_key_separates_candidates_differing_only_in_indicator_lengths():
    """The regression: these two shared a cache entry under the old three-field key."""
    base = {"arm_minutes": 30, "sig_mult": 1.0, "clps_thresh": 0.5, "entry_delay": 1}
    slow = _detector_params({**base, "fast_len": 8, "slow_len": 21, "sig_len": 5})
    fast = _detector_params({**base, "fast_len": 5, "slow_len": 13, "sig_len": 4})
    assert _params_key(slow) != _params_key(fast)

    eod = _detector_params({**base, "mode": "EOD Focus"})
    full = _detector_params({**base, "mode": "Full Session"})
    assert _params_key(eod) != _params_key(full)

    trend_a = _detector_params({**base, "trend_len": 100})
    trend_b = _detector_params({**base, "trend_len": 200})
    assert _params_key(trend_a) != _params_key(trend_b)


def test_identical_signal_parameters_still_share_one_cache_entry():
    """Two entry delays over the same signal must not recompute indicators."""
    left = _detector_params({"sig_mult": 1.0, "clps_thresh": 0.5, "entry_delay": 1, "min_signal_lead": 4})
    right = _detector_params({"sig_mult": 1.0, "clps_thresh": 0.5, "entry_delay": 3, "min_signal_lead": 6})
    assert _params_key(left) == _params_key(right)


def test_indicator_variants_are_ordered_and_non_degenerate():
    """A fast length at or above the slow length inverts the histogram's meaning."""
    assert len(INDICATOR_VARIANTS) == len({tuple(sorted(v.items())) for v in INDICATOR_VARIANTS})
    for variant in INDICATOR_VARIANTS:
        assert variant["fast_len"] < variant["slow_len"], variant
        assert variant["sig_len"] < variant["slow_len"], variant
    assert PINE_DEFAULTS["trend_len"] in TREND_LENGTHS


def test_holdout_evaluate_forwards_full_selected_detector_configuration(monkeypatch):
    captured = []

    def fake_backtest(symbol, bars, **kwargs):
        captured.append(kwargs["detector_params"])
        return {"symbol": symbol, "summary": {"trades": 0}, "trades": []}

    monkeypatch.setattr(experiment, "backtest_symbol", fake_backtest)
    params = {
        "mode": "Full Session",
        "sig_mult": 1.0,
        "clps_thresh": 0.5,
        "fast_len": 5,
        "slow_len": 13,
        "sig_len": 4,
        "trend_len": 200,
        "entry_delay": 1,
        "min_signal_lead": 4,
    }
    experiment._evaluate(
        {"TEST": []},
        start=date(2026, 6, 1),
        end=date(2026, 6, 30),
        params=params,
        bar_minutes=1,
        eod_exit_minute=59,
    )
    assert captured == [_detector_params(params)]
