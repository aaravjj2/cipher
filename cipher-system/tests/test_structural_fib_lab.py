"""Structural Fib lab: the mechanics the claims rest on.

The point of these tests is that the earlier version of this study measured a
different strategy than the one taught — a fixed anchor instead of a trailed one,
and continuation folded together with reversal. So the tests here pin the two
things that distinguish them, plus the arithmetic that decides a verdict.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core import structural_fib_lab as lab

NY = ZoneInfo("America/New_York")


def bar(hhmm: str, o: float, h: float, l: float, c: float, day: str = "2026-03-02") -> lab.Bar:
    y, m, d = (int(x) for x in day.split("-"))
    hh, mm = (int(x) for x in hhmm.split(":"))
    return lab.Bar(datetime(y, m, d, hh, mm, tzinfo=NY), o, h, l, c, 1000.0)


def minute_series(start: str, count: int, price: float, day: str = "2026-03-02") -> list[lab.Bar]:
    hh, mm = (int(x) for x in start.split(":"))
    y, mo, d = (int(x) for x in day.split("-"))
    base = datetime(y, mo, d, hh, mm, tzinfo=NY)
    return [lab.Bar(base + timedelta(minutes=i), price, price, price, price, 100.0)
            for i in range(count)]


# ────────────────────────────────────────────────────── bars and sessions

def test_resample_takes_first_open_last_close_and_the_extremes() -> None:
    ones = [
        bar("09:30", 10.0, 11.0, 9.5, 10.5),
        bar("09:31", 10.5, 12.0, 10.4, 11.0),
        bar("09:32", 11.0, 11.5, 8.0, 9.0),
        bar("09:35", 9.0, 9.2, 8.9, 9.1),   # next bucket
    ]
    five = lab.resample_5m(ones)
    assert len(five) == 2
    first = five[0]
    assert (first.o, first.h, first.l, first.c) == (10.0, 12.0, 8.0, 9.0)
    assert first.v == 3000.0
    assert first.t.minute == 30


def test_buckets_align_to_the_exchange_clock_not_utc() -> None:
    """A UTC-aligned grid straddles the 09:30 open and would mix pre-market prints
    into the first regular bar."""
    five = lab.resample_5m(minute_series("09:28", 6, 100.0))
    starts = [b.t.strftime("%H:%M") for b in five]
    assert starts == ["09:25", "09:30"]


def test_sessions_split_premarket_from_regular_and_drop_overnight() -> None:
    bars = [bar("04:00", *(1,) * 4), bar("09:29", *(2,) * 4),
            bar("09:30", *(3,) * 4), bar("15:55", *(4,) * 4),
            bar("16:00", *(5,) * 4), bar("21:00", *(6,) * 4)]
    sessions = lab.split_sessions(bars)
    day = sessions[date(2026, 3, 2)]
    assert len(day["pre"]) == 2
    assert len(day["reg"]) == 2  # 16:00 and 21:00 belong to neither


# ────────────────────────────────────────────────────── the regime filter

def test_a_tight_premarket_uses_its_own_leg_and_a_wide_one_falls_back() -> None:
    sessions = {
        date(2026, 3, 2): {"pre": [bar("09:00", 100, 101, 100, 100)], "reg": []},
        date(2026, 3, 3): {"pre": [bar("09:00", 100, 110, 100, 100, "2026-03-03")], "reg": []},
    }
    tight, wide = lab.classify_days(sessions)
    assert tight.regime == "trending" and tight.leg_source == "today"
    assert tight.unit == pytest.approx(1.0)
    # 10% is far over the 1.5% gate, so it borrows the previous qualifying leg.
    assert wide.regime == "choppy" and wide.leg_source == "fallback"
    assert wide.unit == pytest.approx(1.0)


def test_a_wide_day_with_no_prior_qualifying_session_gets_no_leg() -> None:
    sessions = {date(2026, 3, 2): {"pre": [bar("09:00", 100, 110, 100, 100)], "reg": []}}
    (ctx,) = lab.classify_days(sessions)
    assert ctx.unit is None and ctx.leg_source == "none"


def test_trend_is_judged_independently_of_the_fib_levels() -> None:
    """The 1.5% filter cannot be scored against a definition it helped construct."""
    trending = [bar(f"09:{30 + i}", 100 + i, 100.5 + i, 99.5 + i, 100 + i) for i in range(10)]
    assert lab.measure_trend(trending) is True
    # Travels up then comes all the way back: closes mid-range, which is chop.
    out = [bar("09:30", 100, 105, 99, 104), bar("09:35", 104, 106, 98, 99),
           bar("09:40", 99, 104, 98, 102), bar("09:45", 102, 105, 99, 102),
           bar("09:50", 102, 104, 99, 101.5), bar("09:55", 101, 103, 99, 102),
           bar("10:00", 102, 104, 98, 102), bar("10:05", 102, 103, 99, 102)]
    assert lab.measure_trend(out) is False


def test_too_few_bars_is_unknown_rather_than_a_guess() -> None:
    assert lab.measure_trend([bar("09:30", 1, 1, 1, 1)]) is None


# ────────────────────────────────────────────────────── the trailed anchor

def test_the_anchor_trails_a_new_low_so_the_level_moves_with_price() -> None:
    """The distinguishing mechanic. With a fixed anchor the long level would be set
    off the first bar's low and would trigger; trailing to a lower low pushes the
    0.5 level further away, so it must not."""
    reg = [
        bar("09:30", 100.0, 100.2, 100.0, 100.1),
        bar("09:35", 100.1, 100.2, 98.0, 98.1),   # new session low: anchor moves down
        bar("09:40", 98.1, 100.6, 98.1, 100.5),
    ] + [bar(f"10:{i:02d}", 100.5, 100.6, 100.4, 100.5) for i in range(0, 40, 5)]
    signals = lab.evaluate_day("T", date(2026, 3, 2), reg, unit=1.0,
                               regime="trending", leg_source="today", pm_range_pct=1.0)
    legs = {s.leg: s for s in signals
            if s.direction == "long" and s.setup == "continuation"}
    # Every level is measured from the trailed 98.0, never the stale 100.0 open low.
    assert legs["0.5->1"].stop == pytest.approx(98.0)      # 0.5 leg stops at the anchor
    assert legs["0.5->1"].target == pytest.approx(99.0)    # anchor + 1R
    assert legs["1->2"].stop == pytest.approx(98.5)        # 1 leg stops at 0.5R
    assert legs["1->2"].target == pytest.approx(100.0)     # anchor + 2R
    # The stale anchor would have put these at 100.0/101.0 and 100.5/102.0.
    assert all(s.target < 100.5 for s in legs.values())


def test_an_overextended_opening_candle_is_skipped_as_an_anchor() -> None:
    wild = bar("09:30", 100.0, 106.0, 99.0, 105.0)          # range 7.0 > 0.5 * unit
    base = datetime(2026, 3, 2, 9, 35, tzinfo=NY)
    calm = [lab.Bar(base + timedelta(minutes=5 * i), 105.0, 105.2, 104.8, 105.0, 100.0)
            for i in range(10)]
    signals = lab.evaluate_day("T", date(2026, 3, 2), [wild] + calm, unit=10.0,
                               regime="trending", leg_source="today", pm_range_pct=1.0)
    # Nothing may anchor to the 99.0 low of the discarded opening candle: the
    # surviving anchor is 104.8, so no level may be derived from 99.0.
    for s in signals:
        assert s.stop != pytest.approx(99.0)
        assert s.stop >= 104.0, f"{s.leg} {s.direction} anchored to the discarded candle"


# ────────────────────────────────────────────────────── reversal vs continuation

def _rally_then_collapse() -> list[lab.Bar]:
    """Up 3R from the open, then straight down through every level."""
    up = [bar(f"09:{30 + 5 * i}", 100 + i, 100.4 + i, 99.9 + i, 100.3 + i) for i in range(4)]
    down = [bar(f"1{h}:{m:02d}", 103 - i, 103.1 - i, 102.6 - i, 102.7 - i)
            for i, (h, m) in enumerate([(0, 0), (0, 5), (0, 10), (0, 15), (0, 20),
                                        (0, 25), (0, 30), (0, 35), (0, 40), (0, 45)])]
    return up + down


def test_a_reversal_requires_the_day_to_have_advanced_first() -> None:
    reg = _rally_then_collapse()
    lenient = lab.evaluate_day("T", date(2026, 3, 2), reg, 1.0, "trending", "today", 1.0,
                               min_advance_r=0.5)
    # An advance requirement larger than the whole rally must suppress the reversal.
    strict = lab.evaluate_day("T", date(2026, 3, 2), reg, 1.0, "trending", "today", 1.0,
                              min_advance_r=50.0)
    assert any(s.setup == "reversal" for s in lenient)
    assert not any(s.setup == "reversal" for s in strict)


def test_a_reversal_never_fires_on_the_half_level() -> None:
    """The method forbids the 0.5 entry on a reversal; only the 1 level counts."""
    reg = _rally_then_collapse()
    signals = lab.evaluate_day("T", date(2026, 3, 2), reg, 1.0, "trending", "today", 1.0)
    assert {s.leg for s in signals if s.setup == "reversal"} <= {"1->2", "2->3"}


def test_continuation_is_gated_to_the_morning() -> None:
    late = [bar(f"14:{m:02d}", 100 + m / 10, 100.5 + m / 10, 99.9 + m / 10, 100.4 + m / 10)
            for m in range(0, 50, 5)]
    signals = lab.evaluate_day("T", date(2026, 3, 2), late, 0.2, "trending", "today", 1.0)
    assert not any(s.setup == "continuation" for s in signals), \
        "afternoon is reversal-only in the method as taught"


def test_one_signal_per_setup_leg_direction_per_day() -> None:
    reg = _rally_then_collapse()
    signals = lab.evaluate_day("T", date(2026, 3, 2), reg, 1.0, "trending", "today", 1.0)
    keys = [(s.setup, s.leg, s.direction) for s in signals]
    assert len(keys) == len(set(keys))


# ────────────────────────────────────────────────────── outcome arithmetic

def test_an_ambiguous_bar_is_scored_as_the_stop() -> None:
    """Five-minute bars carry no intrabar sequence. The reading that cannot flatter
    the strategy is the one to take."""
    spans_both = [bar("10:00", 100.0, 110.0, 90.0, 100.0)]
    outcome, ret = lab._race(100.0, 105.0, 95.0, spans_both, "long")
    assert outcome == "stop" and ret < 0


def test_touch_ignores_the_stop_but_the_race_does_not() -> None:
    dips_then_runs = [bar("10:00", 100.0, 100.1, 94.0, 95.0),
                      bar("10:05", 95.0, 106.0, 95.0, 106.0)]
    assert lab._touched(105.0, dips_then_runs, "long") is True
    assert lab._race(100.0, 105.0, 95.0, dips_then_runs, "long")[0] == "stop"


def test_a_position_open_at_the_close_is_marked_out_not_dropped() -> None:
    flat = [bar("10:00", 100.0, 100.4, 99.6, 100.2)]
    outcome, ret = lab._race(100.0, 105.0, 95.0, flat, "long")
    assert outcome == "close" and ret == pytest.approx(0.2, abs=1e-9)


def test_short_side_returns_are_signed_the_right_way() -> None:
    falls = [bar("10:00", 100.0, 100.1, 94.0, 95.0)]
    outcome, ret = lab._race(100.0, 95.0, 105.0, falls, "short")
    assert outcome == "target" and ret == pytest.approx(5.0)


# ────────────────────────────────────────────────────── verdict machinery

def test_wilson_interval_brackets_the_point_estimate() -> None:
    lo, hi = lab.wilson(80, 100)
    assert lo < 0.8 < hi
    assert (lo, hi) == lab.wilson(80, 100)


def test_a_claim_outside_the_interval_is_refuted_and_inside_is_not() -> None:
    """The verdict has to be an interval statement. A point estimate below a claim
    is not on its own a refutation."""
    refuted = lab.summarise(
        [lab.Signal("T", "2026-03-02", "reversal", "1->2", "short", "trending", "today",
                    1.0, "10:00", 100.0, 98.0, 101.0, i < 44, "target", 0.1)
         for i in range(100)], "reversal 1->2")
    assert refuted["touch_rate"] == pytest.approx(0.44)
    assert refuted["claim_excluded"] is True

    consistent = lab.summarise(
        [lab.Signal("T", "2026-03-02", "continuation", "1->2", "long", "trending", "today",
                    1.0, "10:00", 100.0, 102.0, 99.0, i < 63, "target", 0.1)
         for i in range(100)], "continuation 1->2")
    assert consistent["claim_excluded"] is False


def test_a_leg_with_no_published_claim_returns_no_verdict() -> None:
    s = lab.summarise(
        [lab.Signal("T", "2026-03-02", "reversal", "2->3", "short", "trending", "today",
                    1.0, "10:00", 100.0, 98.0, 101.0, True, "target", 0.1)], "reversal 2->3")
    assert s["claim_excluded"] is None


def test_thin_samples_are_flagged_underpowered() -> None:
    one = lab.summarise(
        [lab.Signal("T", "2026-03-02", "reversal", "1->2", "short", "trending", "today",
                    1.0, "10:00", 100.0, 98.0, 101.0, True, "target", 0.1)], "reversal 1->2")
    assert one["underpowered"] is True
    assert one["n"] == 1


def test_an_empty_group_reports_nothing_rather_than_a_rate() -> None:
    assert lab.summarise([], "continuation 1->2") == {"n": 0}


# ────────────────────────────────────────────────────── entry mode

def test_level_entry_fills_at_the_level_and_confirmed_entry_pays_the_close() -> None:
    """This is what the confirmation rule costs: the same trigger, a worse fill.

    The method waits for a body close past the level, so the fill is the close, not
    the level. That moves the target nearer and the stop further on every trade.
    """
    # The trigger bar opens and closes past the 0.5 level (100.5) but short of the
    # 1 level (101.0), so both entry modes fire the same leg at different prices.
    reg = [bar("09:30", 100.0, 100.1, 100.0, 100.05),
           bar("09:35", 100.05, 100.1, 100.0, 100.05),
           bar("09:40", 100.60, 100.8, 100.55, 100.70)]
    reg += [bar(f"10:{m:02d}", 100.7, 100.8, 100.6, 100.7) for m in range(0, 40, 5)]
    kw = dict(unit=1.0, regime="trending", leg_source="today", pm_range_pct=1.0)
    confirmed = lab.evaluate_day("T", date(2026, 3, 2), reg, **kw, entry_mode="confirmed")
    at_level = lab.evaluate_day("T", date(2026, 3, 2), reg, **kw, entry_mode="level")

    def first_long(sigs):
        return next(s for s in sigs
                    if s.direction == "long" and s.leg == "0.5->1"
                    and s.setup == "continuation")

    c, l = first_long(confirmed), first_long(at_level)
    assert l.entry_price == pytest.approx(100.5)   # the 0.5 level itself
    assert c.entry_price == pytest.approx(100.70)  # the bar close, 0.2 past it
    # Same target and stop, strictly worse entry.
    assert c.target == pytest.approx(l.target) and c.stop == pytest.approx(l.stop)
    reward_c, reward_l = abs(c.target - c.entry_price), abs(l.target - l.entry_price)
    assert reward_c < reward_l


# ────────────────────────────────────────────────────── control

def test_the_control_holds_geometry_constant_and_only_moves_the_entry_time() -> None:
    reg = [bar(f"10:{m:02d}", 100 + m * 0.01, 100.2 + m * 0.01,
               99.8 + m * 0.01, 100.1 + m * 0.01) for m in range(0, 55, 5)]
    sig = lab.Signal("T", "2026-03-02", "continuation", "0.5->1", "long", "trending",
                     "today", 1.0, "10:00", 100.0, 100.5, 99.5, True, "target", 0.5)
    out = lab.matched_control([sig], {"T": {date(2026, 3, 2): {"pre": [], "reg": reg}}},
                              replicates=5)
    row = out["continuation 0.5->1"]
    assert row["n"] == 5
    assert 0.0 <= row["touch_rate"] <= 1.0


def test_the_control_is_reproducible_for_a_fixed_seed() -> None:
    reg = [bar(f"10:{m:02d}", 100.0, 100.5, 99.5, 100.0) for m in range(0, 55, 5)]
    sig = lab.Signal("T", "2026-03-02", "continuation", "0.5->1", "long", "trending",
                     "today", 1.0, "10:00", 100.0, 100.5, 99.5, True, "target", 0.5)
    sessions = {"T": {date(2026, 3, 2): {"pre": [], "reg": reg}}}
    a = lab.matched_control([sig], sessions, seed=7, replicates=10)
    b = lab.matched_control([sig], sessions, seed=7, replicates=10)
    assert a == b


def test_a_signal_whose_session_is_missing_is_skipped_not_invented() -> None:
    sig = lab.Signal("T", "2026-03-02", "continuation", "0.5->1", "long", "trending",
                     "today", 1.0, "10:00", 100.0, 100.5, 99.5, True, "target", 0.5)
    assert lab.matched_control([sig], {}) == {}
