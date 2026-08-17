"""Exit policies, and the clustering estimator that decides whether any of them matter."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core import wave_lock_exits as ex
from core.wave_lock_lab import Bar, Setup

NY = ZoneInfo("America/New_York")


def bar(minute, o, h, l, c):
    return Bar(datetime(2026, 3, 2, 10, minute, tzinfo=NY), o, h, l, c, 1000.0)


def long_setup(**kw):
    base = dict(symbol="T", day="2026-03-02", engine="early", direction="long",
                signal_index=0, signal_time="10:00", anchor=99.0, entry_price=100.0,
                pm_range=2.0, t05=101.0, t10=102.0, t20=104.0)
    base.update(kw)
    return Setup(**base)


# ───────────────────────────────────────────── the estimator

def test_clustered_se_exceeds_the_iid_se_when_returns_correlate_within_a_session() -> None:
    """Setups in one session share a regime and overlapping paths, so the honest standard
    error must be wider than the independent one."""
    correlated = {f"d{i}": [1.0] * 10 if i % 2 else [-1.0] * 10 for i in range(20)}
    mean, se, t, n = ex.cluster_robust_t(correlated)
    assert n == 200
    assert mean == pytest.approx(0.0)
    iid_se = 1.0 / (n ** 0.5)
    assert se > iid_se, "perfectly correlated clusters must widen the SE"


def test_equal_weighting_sessions_is_not_the_same_estimand_as_the_mean() -> None:
    """The bug this estimator replaces. One session with a single winning setup carried the
    same weight as a session with 55, inflating the mean of daily means thirteenfold and
    turning a t of 0.74 into an apparent 3.47."""
    lopsided = {"tiny": [10.0], **{f"big{i}": [-0.1] * 55 for i in range(20)}}
    values = [r for rows in lopsided.values() for r in rows]
    true_mean = sum(values) / len(values)
    mean_of_means = sum(sum(v) / len(v) for v in lopsided.values()) / len(lopsided)
    assert true_mean < 0 < mean_of_means, "the two estimands disagree even in sign here"
    mean, _se, _t, _n = ex.cluster_robust_t(lopsided)
    assert mean == pytest.approx(true_mean), "must weight per setup, not per session"


def test_a_single_cluster_yields_no_inference() -> None:
    mean, se, t, n = ex.cluster_robust_t({"only": [1.0, 2.0, 3.0]})
    assert (se, t) == (0.0, 0.0) and n == 3


def test_an_empty_input_is_safe() -> None:
    assert ex.cluster_robust_t({}) == (0.0, 0.0, 0.0, 0)


# ───────────────────────────────────────────── policies

def test_the_anchor_stop_wins_a_bar_that_spans_both_barriers() -> None:
    spans = [bar(1, 100.0, 103.0, 98.0, 100.0)]
    assert ex.baseline_1r(long_setup(), spans).outcome == "stop"


def test_baseline_marks_out_at_the_session_close_when_neither_barrier_trades() -> None:
    """This bucket is the whole point: the strategy specifies no exit for it, so the
    mark-out convention silently becomes the exit rule."""
    quiet = [bar(1, 100.0, 100.4, 99.6, 100.3)]
    fill = ex.baseline_1r(long_setup(), quiet)
    assert fill.outcome == "close"
    assert fill.return_pct == pytest.approx(0.3)


def test_the_nearer_target_pays_less_but_resolves_where_1R_would_not() -> None:
    reaches_05_only = [bar(1, 100.0, 101.2, 99.5, 101.0)]
    at_1r = ex.baseline_1r(long_setup(), reaches_05_only)
    at_05 = ex.target_05r(long_setup(), reaches_05_only)
    assert at_1r.outcome == "close"
    assert at_05.outcome == "target"
    assert at_05.return_pct == pytest.approx(1.0)


def test_breakeven_arms_only_after_05_trades_and_then_stops_at_entry() -> None:
    bars = [bar(1, 100.0, 101.2, 100.0, 101.0),   # arms breakeven
            bar(2, 101.0, 101.1, 99.5, 99.8)]     # would have run to the anchor
    fill = ex.breakeven_after_05(long_setup(), bars)
    assert fill.outcome == "stop_be"
    assert fill.return_pct == pytest.approx(0.0), "stopped at entry, not at the anchor"


def test_breakeven_before_05_still_stops_at_the_anchor() -> None:
    fill = ex.breakeven_after_05(long_setup(), [bar(1, 100.0, 100.2, 98.5, 98.8)])
    assert fill.outcome == "stop"
    assert fill.return_pct == pytest.approx(-1.0)


def test_the_scale_out_banks_half_at_05_and_runs_the_rest() -> None:
    bars = [bar(1, 100.0, 101.2, 100.0, 101.0),   # half off at 0.5R = +1.0%
            bar(2, 101.0, 102.5, 101.0, 102.2)]   # rest reaches 1R = +2.0%
    fill = ex.partial_05_then_1r(long_setup(), bars)
    assert fill.outcome == "target"
    assert fill.return_pct == pytest.approx(0.5 * 1.0 + 0.5 * 2.0)


def test_the_scale_out_keeps_the_banked_half_when_the_rest_stops_out() -> None:
    bars = [bar(1, 100.0, 101.2, 100.0, 101.0),   # banks +0.5%
            bar(2, 101.0, 101.1, 99.0, 99.5)]     # rest stopped at entry for 0
    fill = ex.partial_05_then_1r(long_setup(), bars)
    assert fill.outcome == "stop_partial"
    assert fill.return_pct == pytest.approx(0.5)


def test_a_time_stop_marks_out_at_its_horizon_rather_than_holding() -> None:
    quiet = [bar(m, 100.0, 100.3, 99.8, 100.1) for m in range(1, 40)]
    fill = ex._time_stop(30)(long_setup(), quiet)
    assert fill.outcome == "timeout"


def test_a_time_stop_still_honours_both_barriers_inside_its_window() -> None:
    hits = [bar(1, 100.0, 100.2, 99.9, 100.1), bar(2, 100.1, 102.5, 100.0, 102.4)]
    assert ex._time_stop(30)(long_setup(), hits).outcome == "target"


def test_short_side_signs_are_mirrored() -> None:
    setup = long_setup(direction="short", anchor=101.0, t05=99.0, t10=98.0, t20=96.0)
    fill = ex.baseline_1r(setup, [bar(1, 100.0, 100.1, 97.5, 98.0)])
    assert fill.outcome == "target"
    assert fill.return_pct == pytest.approx(2.0)


def test_every_policy_is_registered_so_none_can_be_quietly_dropped() -> None:
    """The sweep's honesty depends on reporting all of them, so the registry is the contract."""
    assert set(ex.POLICIES) >= {
        "baseline_1R", "target_0.5R", "breakeven_after_0.5R",
        "partial_0.5R_then_1R", "time_stop_30m", "time_stop_60m", "time_stop_120m",
    }
    for name, policy in ex.POLICIES.items():
        fill = policy(long_setup(), [bar(1, 100.0, 100.2, 99.9, 100.0)])
        assert isinstance(fill, ex.Fill), f"{name} did not return a Fill"
