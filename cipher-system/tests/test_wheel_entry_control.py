"""The matched random-entry control for the leveraged-ETF wheel.

The point of the control is that it changes *only* entry timing. These tests pin that:
the two arms must agree on which days were ever eligible, the control must take entries
at the measured rate, and it must be reproducible from its seed. A control that quietly
differs in universe, sizing, or eligibility would produce a comparison that looks
rigorous and means nothing.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from leveraged_etf_csp_wheel import DEFAULT_MODES, DailyBar, WheelConfig  # noqa: E402
from wheel_entry_control import (  # noqa: E402
    RandomEntryBacktester,
    SignalRateProbe,
    summarize_control,
)

from test_leveraged_etf_csp_wheel import (  # noqa: E402
    InMemoryData,
    add_rising_weekly_history,
    put_candidate,
    quality_asset,
)


def _config() -> WheelConfig:
    return WheelConfig(mode=DEFAULT_MODES["standard"], enforce_target_pop=False)


def _data_with_down_days(down_day_indices: set[int], sessions: int = 40):
    """A rising weekly history, then daily bars where chosen days drop hard.

    Every daily session gets a put chain, so option availability never differs between
    the arms and the only thing that varies is which days the entry logic picks.
    """
    data = InMemoryData()
    start = add_rising_weekly_history(data, "NVDL")
    days: list[date] = []
    close = 80.0
    for index in range(sessions):
        day = start + timedelta(days=index)
        prior_close = close
        # A -6% day clears the default -5% down-day threshold; +0.5% does not.
        close = prior_close * (0.94 if index in down_day_indices else 1.005)
        data.add_bar("NVDL", DailyBar(day, prior_close, max(prior_close, close), min(prior_close, close), close))
        data.add_chain("NVDL", day, "put", (put_candidate(day, strike=close * 0.85),))
        days.append(day)
    return data, days


def _probe(data, days):
    probe = SignalRateProbe(data, (quality_asset(),), _config(), initial_cash=200_000.0)
    result = probe.run(days[0], days[-1])
    return probe, result


def test_probe_measures_the_rate_the_real_filters_actually_fire():
    # 4 hard down days out of 40 sessions.
    data, days = _data_with_down_days({5, 12, 20, 31})
    probe, result = _probe(data, days)

    rates = probe.entry_rates()
    assert set(rates) == {"NVDL"}
    opportunities = probe.opportunities["NVDL"]
    fires = probe.fires["NVDL"]
    assert opportunities > 0
    assert fires == pytest.approx(rates["NVDL"] * opportunities)
    # The filter is selective: it fires on a minority of eligible days.
    assert 0.0 < rates["NVDL"] < 0.5
    assert result.summary["research_grade"] is False


def test_control_is_reproducible_from_its_seed():
    data, days = _data_with_down_days({5, 12, 20, 31})
    probe, _ = _probe(data, days)
    rates = probe.entry_rates()

    def run(seed: int):
        control = RandomEntryBacktester(
            data, (quality_asset(),), _config(), initial_cash=200_000.0,
            entry_rates=rates, seed=seed,
        )
        outcome = control.run(days[0], days[-1])
        return dict(control.entries_taken), outcome.summary["ending_equity"]

    assert run(7) == run(7)
    # And different seeds explore different entry days, or the control is not random.
    assert run(7) != run(8)


def test_control_takes_entries_at_the_measured_rate_not_on_every_day():
    """A control that entered daily would beat or lose to the signal for the wrong reason."""
    data, days = _data_with_down_days({5, 12, 20, 31})
    probe, _ = _probe(data, days)
    rates = probe.entry_rates()

    taken = []
    for seed in range(25):
        control = RandomEntryBacktester(
            data, (quality_asset(),), _config(), initial_cash=200_000.0,
            entry_rates=rates, seed=seed,
        )
        control.run(days[0], days[-1])
        taken.append(control.entries_taken.get("NVDL", 0))

    expected = rates["NVDL"] * probe.opportunities["NVDL"]
    average = sum(taken) / len(taken)
    # Bernoulli draws scatter, so this checks the centre rather than each replicate.
    # Position limits can cap entries below the draw count, so allow generous slack.
    assert average <= expected + 3.0
    assert max(taken) <= probe.opportunities["NVDL"]


def test_a_zero_rate_symbol_never_enters_in_the_control():
    """Guards the lookup default: an unmeasured symbol must not become a daily entry."""
    data, days = _data_with_down_days({5, 12})
    control = RandomEntryBacktester(
        data, (quality_asset(),), _config(), initial_cash=200_000.0,
        entry_rates={}, seed=1,
    )
    result = control.run(days[0], days[-1])
    assert control.entries_taken == {}
    assert result.summary["ending_equity"] == pytest.approx(200_000.0)


def test_eligibility_is_identical_across_both_arms():
    """The matched denominator: same eligible days, whatever the timing decision."""
    data, days = _data_with_down_days({5, 12, 20, 31})
    asset = quality_asset()
    probe = SignalRateProbe(data, (asset,), _config(), initial_cash=200_000.0)
    control = RandomEntryBacktester(
        data, (asset,), _config(), initial_cash=200_000.0, entry_rates={"NVDL": 0.5}, seed=3,
    )

    for day in days:
        assert probe._evaluate_gates(asset, day).eligible == control._evaluate_gates(asset, day).eligible

    # A day that fails only on timing is still eligible -- that is what makes it a
    # denominator entry rather than a day neither arm could have traded.
    timing_only = [
        g for g in (probe._evaluate_gates(asset, d) for d in days)
        if g.reason == "down_day_threshold_failed"
    ]
    assert timing_only, "expected at least one day rejected purely on timing"
    assert all(g.eligible and not g.fired for g in timing_only)


def test_summary_refuses_a_verdict_without_enough_replicates():
    class _Result:
        def __init__(self, value):
            self.summary = {"total_return_pct": value, "closed_option_events": 1,
                            "research_grade_blockers": ["nbbo unavailable"]}

    thin = summarize_control(_Result(5.0), [_Result(1.0)])
    assert thin["beat_control_pct"] is None
    assert "no comparison is supported" in thin["interpretation"]

    controls = [_Result(v) for v in (1.0, 2.0, 3.0, 4.0)]
    strong = summarize_control(_Result(5.0), controls)
    assert strong["beat_control_pct"] == 100.0
    assert strong["control_median"] == pytest.approx(2.5)
    assert strong["research_grade"] is False
    # The data limitation is carried forward, never dropped because a control was run.
    assert any("nbbo" in b for b in strong["research_grade_blockers"])

    middling = summarize_control(_Result(2.5), controls)
    assert middling["beat_control_pct"] == 50.0


def test_a_starved_control_is_reported_invalid_not_as_a_100_percent_win():
    """The confound found on the real archive.

    data/historical_options was populated by downloading chains for the days the signal
    fired, so option availability correlates with the signal. Random entry then lands on
    days with no chain, takes no position, and returns 0 -- which a naive percentile reads
    as the strategy beating 100% of controls. It must be refused instead.
    """
    class _Result:
        def __init__(self, value, events):
            self.summary = {"total_return_pct": value, "events": events,
                            "closed_option_events": events // 2,
                            "research_grade_blockers": ["nbbo unavailable"]}

    starved = summarize_control(_Result(1.80, 8), [_Result(0.0, 0) for _ in range(3)])
    assert starved["control_starved"] is True
    assert starved["comparison_valid"] is False
    assert starved["beat_control_pct"] is None
    assert "INVALID" in starved["interpretation"]
    assert any("starved" in b for b in starved["research_grade_blockers"])

    # A control that actually traded is compared normally.
    live = summarize_control(_Result(1.80, 8), [_Result(v, 6) for v in (0.5, 1.0, 3.0)])
    assert live["control_starved"] is False
    assert live["comparison_valid"] is True
    assert live["beat_control_pct"] == pytest.approx(66.667, abs=0.01)
