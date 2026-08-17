"""Wave Lock: the pivot geometry, the anchor stop, and the conditional-rate trap."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from core import wave_lock_lab as wl

NY = ZoneInfo("America/New_York")


def bar(hhmm, o, h, l, c, day="2026-03-02"):
    y, m, d = (int(x) for x in day.split("-"))
    hh, mm = (int(x) for x in hhmm.split(":"))
    return wl.Bar(datetime(y, m, d, hh, mm, tzinfo=NY), o, h, l, c, 1000.0)


def series(prices, start_min=30, day="2026-03-02"):
    """One bar per price, 1-minute apart, starting 09:30."""
    out = []
    for i, p in enumerate(prices):
        total = start_min + i
        out.append(bar(f"{9 + total // 60}:{total % 60:02d}", p, p + 0.05, p - 0.05, p, day))
    return out


# ───────────────────────────────────────────────────── pivots

def test_a_pivot_high_is_reported_on_the_bar_that_confirms_it_not_when_it_forms() -> None:
    """Pine reports ta.pivothigh `rightbars` later, because that is when it is knowable.
    Reporting it at the pivot bar would be lookahead."""
    bars = series([1, 2, 3, 5, 3, 2, 1])
    found = wl.pivots(bars, 3, 1, "high")
    assert list(found) == [4], "confirmation index = pivot index 3 + right 1"
    pivot_index, price = found[4]
    assert pivot_index == 3
    assert price == pytest.approx(5.05)


def test_more_right_bars_delays_the_confirmation_index() -> None:
    bars = series([1, 2, 3, 5, 3, 2, 1])
    assert list(wl.pivots(bars, 3, 3, "high")) == [6]


def test_a_pivot_low_mirrors_the_high() -> None:
    bars = series([5, 4, 3, 1, 3, 4, 5])
    found = wl.pivots(bars, 3, 1, "low")
    assert list(found) == [4]
    assert found[4][1] == pytest.approx(0.95)


def test_a_flat_run_produces_no_pivot() -> None:
    """Strict comparison on both sides: equal highs are not turning points."""
    assert wl.pivots(series([2, 2, 2, 2, 2, 2, 2]), 3, 1, "high") == {}


# ───────────────────────────────────────────────────── ATR

def test_atr_is_none_until_the_window_fills_then_wilder_smoothed() -> None:
    bars = series(list(range(1, 30)))
    values = wl.atr(bars, 14)
    assert values[:13] == [None] * 13
    assert values[13] is not None
    # Wilder smoothing carries the previous value, so it is not a plain window mean.
    assert values[20] != pytest.approx(values[19])


def test_true_range_uses_the_prior_close_not_just_the_bar() -> None:
    gap = [bar("09:30", 10, 10.1, 9.9, 10.0), bar("09:31", 20, 20.1, 19.9, 20.0)]
    values = wl.atr(gap, 2)
    # The second bar's TR must span the gap from 10.0 to 19.9, not merely its own 0.2 range.
    assert values[1] is not None and values[1] > 4.0


# ───────────────────────────────────────────────────── PM context and filter

def test_the_pm_proximity_window_extends_beyond_the_range_by_a_share_of_it() -> None:
    pre = [bar("09:00", 100, 104, 100, 102)]
    ctx = wl.day_context(pre, proximity_pct=25.0)
    assert ctx.pm_range == pytest.approx(4.0)
    assert ctx.permitted_low == pytest.approx(99.0)    # PML 100 - 25% of 4
    assert ctx.permitted_high == pytest.approx(105.0)  # PMH 104 + 25% of 4


def test_a_pivot_outside_the_pm_window_is_rejected() -> None:
    pre = [bar("09:00", 100, 104, 100, 102)]
    ctx = wl.day_context(pre, proximity_pct=25.0)
    assert wl._pm_ok(104.9, ctx) is True
    assert wl._pm_ok(105.1, ctx) is False
    assert wl._pm_ok(98.9, ctx) is False


def test_a_zero_width_premarket_yields_no_context() -> None:
    flat = [wl.Bar(datetime(2026, 3, 2, 9, 0, tzinfo=NY), 100, 100, 100, 100, 1.0)]
    assert wl.day_context(flat, proximity_pct=25.0) is None
    assert wl.day_context([], proximity_pct=25.0) is None


# ───────────────────────────────────────────────────── the anchor is a stop

def test_a_setup_that_trades_back_through_its_pivot_fails_before_reaching_05() -> None:
    """The defining rule of this strategy, and the one Structural Fib lacked."""
    reg = series([100.0] * 3 + [99.0, 99.5, 98.0])
    setup = wl.Setup(symbol="T", day="2026-03-02", engine="early", direction="long",
                     signal_index=4, signal_time="09:34", anchor=98.95,
                     entry_price=99.5, pm_range=4.0,
                     t05=100.95, t10=102.95, t20=106.95)
    wl.track([setup], reg, include_signal_bar=False)
    assert setup.failed_before_05 is True
    assert setup.hit05 is False


def test_reaching_05_before_the_anchor_is_not_a_failure() -> None:
    reg = series([100.0, 100.0, 100.0, 100.0, 101.0, 102.0])
    setup = wl.Setup(symbol="T", day="2026-03-02", engine="early", direction="long",
                     signal_index=3, signal_time="09:33", anchor=99.0,
                     entry_price=100.0, pm_range=2.0,
                     t05=100.9, t10=101.9, t20=103.9)
    wl.track([setup], reg, include_signal_bar=False)
    assert setup.failed_before_05 is False
    assert setup.hit05 is True and setup.hit10 is True


def test_unconditional_touch_is_recorded_separately_from_the_tracked_rate() -> None:
    """The tracked rate stops counting at failure; a random-entry control has no anchor to
    fail against, so comparing tracked-against-control would credit the control with paths
    the strategy was never allowed to finish."""
    reg = series([100.0, 100.0, 100.0, 98.0, 101.0, 105.0])
    setup = wl.Setup(symbol="T", day="2026-03-02", engine="early", direction="long",
                     signal_index=2, signal_time="09:32", anchor=99.0,
                     entry_price=100.0, pm_range=2.0,
                     t05=100.9, t10=101.9, t20=103.9)
    wl.track([setup], reg, include_signal_bar=False)
    assert setup.failed_before_05 is True      # died at the anchor first
    assert setup.touch_10_uncond is True       # but price did later reach 1R


# ───────────────────────────────────────────────────── the conditional-rate trap

def test_the_conditional_rate_is_reported_beside_the_unconditional_one() -> None:
    """The indicator's headline number is P(1R | reached 0.5R). Its denominator is the
    confirmation event, which it never displays, so the figure reads far better than the
    unconditional rate it is easily mistaken for."""
    def made(hit05, hit10):
        s = wl.Setup(symbol="T", day="2026-03-02", engine="early", direction="long",
                     signal_index=0, signal_time="09:30", anchor=99.0, entry_price=100.0,
                     pm_range=1.0, t05=100.5, t10=101.0, t20=102.0)
        s.hit05, s.hit10, s.race, s.return_pct = hit05, hit10, "stop", -0.1
        return s
    setups = [made(True, True)] * 3 + [made(True, False)] * 7 + [made(False, False)] * 90
    summary = wl.summarise(setups)
    assert summary["reach_05_rate"] == pytest.approx(0.10)
    assert summary["reach_10_rate"] == pytest.approx(0.03)
    assert summary["reach_10_given_05"] == pytest.approx(0.30)


def test_no_confirmations_means_no_conditional_rate_rather_than_a_divide_by_zero() -> None:
    s = wl.Setup(symbol="T", day="2026-03-02", engine="early", direction="long",
                 signal_index=0, signal_time="09:30", anchor=99.0, entry_price=100.0,
                 pm_range=1.0, t05=100.5, t10=101.0, t20=102.0)
    s.race, s.return_pct = "stop", -0.1
    assert wl.summarise([s])["reach_10_given_05"] is None


def test_an_empty_group_reports_nothing_rather_than_a_rate() -> None:
    assert wl.summarise([]) == {"n": 0}


def test_thin_samples_are_flagged_underpowered() -> None:
    s = wl.Setup(symbol="T", day="2026-03-02", engine="early", direction="long",
                 signal_index=0, signal_time="09:30", anchor=99.0, entry_price=100.0,
                 pm_range=1.0, t05=100.5, t10=101.0, t20=102.0)
    s.race, s.return_pct = "stop", -0.1
    assert wl.summarise([s])["underpowered"] is True


# ───────────────────────────────────────────────────── same-bar accounting

def test_including_the_signal_bar_can_record_a_zero_bar_target() -> None:
    """The Pine checks targets on the bar that created the setup, so a register-confirm-target
    chain can complete inside one bar at zero elapsed bars."""
    reg = [bar("09:30", 100, 100, 100, 100), bar("09:31", 100, 103, 100, 103),
           bar("09:32", 103, 103, 103, 103)]
    def fresh():
        return wl.Setup(symbol="T", day="2026-03-02", engine="early", direction="long",
                        signal_index=1, signal_time="09:31", anchor=99.0,
                        entry_price=103.0, pm_range=2.0, t05=100.0, t10=101.0, t20=103.0)
    included, excluded = fresh(), fresh()
    wl.track([included], reg, include_signal_bar=True)
    wl.track([excluded], reg, include_signal_bar=False)
    assert included.bars_to_05 == 0
    assert excluded.bars_to_05 != 0
