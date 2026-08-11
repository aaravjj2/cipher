"""Matched random-entry control for the leveraged-ETF wheel.

`data/leveraged_etf_wheel/` holds dozens of completed runs, and every one of them
reports what the strategy earned. None of them answers the question that decides whether
the strategy's *entry timing* is worth anything: what would the same machinery have
earned entering on unrelated days?

That matters here more than usual. The wheel sells puts into a persistent upward drift
with leveraged ETFs as collateral, so a positive total return is the default outcome of
being short puts at all. A down-day plus weekly-cloud filter can look profitable while
contributing nothing, and the existing runs cannot distinguish the two.

This module answers it by holding everything constant except entry timing:

  * same universe, quality whitelist, and archives
  * same sizing, contract selection, IV band, and POP enforcement
  * same exit, roll, assignment, averaging-down, and covered-call logic
  * same execution-cost approximation
  * same *expected number of entries*, matched per symbol

and randomizing only *which* eligible days an entry happens on. The comparison is a
placebo test: if the real result does not sit in the tail of the control distribution,
the filters are decoration.

Both classes below share one definition of "opportunity" -- a day where the strategy
could have entered had timing allowed -- because a control matched against a different
denominator is not matched at all. `_evaluate_gates` is that single definition, and the
probe and the control both call it rather than re-deriving it.

The inherited research-grade blockers still apply: these archives carry trade bars, not
NBBO, so a control result is exploratory evidence about the *filter*, not a tradeable
finding. A control cannot repair a data limitation; it can only tell you whether the
signal beat chance under the same limitation.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean, median
from typing import Any, Sequence

from leveraged_etf_csp_wheel import (
    BacktestResult,
    DailyBar,
    LeveragedEtfWheelBacktester,
    UniverseAsset,
    WeeklyTrendState,
    weekly_trend_state,
)


# A control arm that trades far less often than the real arm is measuring option-chain
# coverage, not entry timing. Below this share of the real arm's activity the
# comparison is refused rather than reported.
MIN_CONTROL_ACTIVITY_RATIO = 0.5


@dataclass(frozen=True, slots=True)
class _Gates:
    """Outcome of the checks that are identical in the real run and the control."""

    eligible: bool
    fired: bool
    weekly: WeeklyTrendState | None
    bar: DailyBar | None
    reason: str | None


class _SharedGates(LeveragedEtfWheelBacktester):
    """Base for the probe and the control, so both agree on what an opportunity is."""

    def _evaluate_gates(self, asset: UniverseAsset, day: date) -> _Gates:
        """Split `_entry_signal`'s checks into eligibility versus timing.

        Eligibility is everything that has nothing to do with when the trade happens:
        the quality whitelist, the presence of a bar and a prior close, and an available
        weekly RSI -- that last one because position size is derived from it, so a day
        without it could not be traded no matter what the timing filters said.

        Timing is the down-day threshold and the weekly-cloud filter. `_entry_signal`
        short-circuits on the down-day check and therefore never learns whether the
        cloud filter or RSI would also have failed; evaluating all of them here keeps the
        denominator honest rather than inflating it with days that were never tradeable.
        """
        quality, reasons = asset.quality_check()
        if not quality:
            return _Gates(False, False, None, None, ";".join(reasons))
        bar = self.data.daily_bar(asset.symbol, day)
        history = self.data.daily_history(asset.symbol, day)
        if bar is None or len(history) < 2:
            return _Gates(False, False, None, bar, "missing_daily_history")
        prior = next((row for row in reversed(history[:-1]) if row.day < day), None)
        if prior is None:
            return _Gates(False, False, None, bar, "missing_prior_close")
        weekly = weekly_trend_state(history, day, self.config)
        if weekly.weekly_rsi is None:
            # Not a timing failure: sizing is impossible, so this day was never available
            # to either arm and must not count toward the matched rate.
            return _Gates(False, False, weekly, bar, "weekly_rsi_unavailable")

        daily_return = bar.close / prior.close - 1.0
        down_day = daily_return <= self.config.down_day_threshold
        clouds = weekly.bullish_clouds >= self.config.required_bullish_clouds
        if not down_day:
            return _Gates(True, False, weekly, bar, "down_day_threshold_failed")
        if not clouds:
            return _Gates(True, False, weekly, bar, "weekly_cloud_filter_failed")
        return _Gates(True, True, weekly, bar, None)


class SignalRateProbe(_SharedGates):
    """The real strategy, instrumented to record how often its timing filters fire."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.opportunities: dict[str, int] = defaultdict(int)
        self.fires: dict[str, int] = defaultdict(int)

    def _entry_signal(
        self, asset: UniverseAsset, day: date
    ) -> tuple[bool, WeeklyTrendState | None, DailyBar | None, str | None]:
        gates = self._evaluate_gates(asset, day)
        if gates.eligible:
            self.opportunities[asset.symbol] += 1
            if gates.fired:
                self.fires[asset.symbol] += 1
        return gates.fired, gates.weekly, gates.bar, gates.reason

    def entry_rates(self) -> dict[str, float]:
        """Per-symbol probability that the timing filters fire on an eligible day."""
        return {
            symbol: self.fires[symbol] / count
            for symbol, count in self.opportunities.items()
            if count > 0
        }


class RandomEntryBacktester(_SharedGates):
    """The same engine, entering on random eligible days at the measured rate.

    The rate is per symbol rather than pooled: a filter that fires often on one ETF and
    never on another would otherwise be compared against a control that redistributes
    entries between them, which changes the universe exposure instead of only the timing.
    """

    def __init__(
        self,
        *args: Any,
        entry_rates: dict[str, float],
        seed: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._entry_rates = dict(entry_rates)
        self._rng = random.Random(seed)
        self.entries_taken: dict[str, int] = defaultdict(int)

    def _entry_signal(
        self, asset: UniverseAsset, day: date
    ) -> tuple[bool, WeeklyTrendState | None, DailyBar | None, str | None]:
        gates = self._evaluate_gates(asset, day)
        if not gates.eligible:
            return False, gates.weekly, gates.bar, gates.reason
        rate = self._entry_rates.get(asset.symbol, 0.0)
        # Draw unconditionally so the RNG stream depends only on the eligible-day
        # sequence, not on how many draws happened to succeed. Consuming a variable
        # number of values would make replicates incomparable across seeds.
        draw = self._rng.random()
        if draw >= rate:
            return False, gates.weekly, gates.bar, "control_random_declined"
        self.entries_taken[asset.symbol] += 1
        return True, gates.weekly, gates.bar, None


def _percentile_of(value: float, sample: Sequence[float]) -> float:
    """Share of the control sample at or below `value`, as a percentage."""
    if not sample:
        return float("nan")
    return sum(1 for item in sample if item <= value) / len(sample) * 100.0


def summarize_control(
    actual: BacktestResult,
    controls: Sequence[BacktestResult],
    *,
    metric: str = "total_return_pct",
    entry_rates: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compare one real run against a control distribution.

    `beat_control_pct` is the fraction of control replicates the real run beat. It is
    reported instead of a p-value from an assumed distribution because these replicates
    are the whole sample: nothing here justifies a normality assumption, and the count
    is small enough that the empirical position is the honest statistic.
    """
    control_values = [
        float(result.summary[metric])
        for result in controls
        if result.summary.get(metric) is not None
    ]
    actual_value = actual.summary.get(metric)

    # A control that never traded is starved, not beaten. On an archive assembled by
    # downloading option chains for the days the signal fired -- which is how
    # data/historical_options was built -- chain availability is correlated with the
    # signal, so random entry lands on days with no chain and takes no position. The
    # percentile would then read 100% and mean only that one arm had data. Detect it
    # instead of publishing it.
    # Comparing on activity rather than only on "did it trade at all": with 8 real events
    # against controls managing 0-2, the arms differ mostly in how often a chain existed,
    # and the percentile measures data coverage wearing a statistic's clothing. The 0.5
    # threshold is a judgement call, not a derived bound, and is reported so a reader can
    # disagree with it.
    actual_events = int(actual.summary.get("events") or 0)
    control_events = [int(r.summary.get("events") or 0) for r in controls]
    control_events_median = median(control_events) if control_events else 0.0
    activity_ratio = (control_events_median / actual_events) if actual_events > 0 else None
    empty_sample = actual_events == 0
    starved = activity_ratio is not None and activity_ratio < MIN_CONTROL_ACTIVITY_RATIO

    verdict_available = (
        actual_value is not None
        and len(control_values) >= 2
        and not starved
        and not empty_sample
    )
    beat_pct = _percentile_of(float(actual_value), control_values) if verdict_available else None

    if empty_sample:
        interpretation = (
            "INVALID: the real arm produced no events in the locked window, so the "
            "control comparison has no empirical sample. A zero-return real arm and "
            "zero-return controls cannot support a 100% win claim; check the selected "
            "equity/archive coverage before interpreting the strategy."
        )
    elif starved:
        interpretation = (
            f"INVALID: control replicates traded a median of {control_events_median} times "
            f"against the real arm's {actual_events}, below the {MIN_CONTROL_ACTIVITY_RATIO:.0%} "
            "activity floor. The arms therefore differ in option-chain availability rather "
            "than only in entry timing. This archive was populated for the days the signal "
            "fired, so a valid control needs chains downloaded for the control's random "
            "dates too; until then no conclusion about the entry filters is supported."
        )
    elif verdict_available:
        interpretation = (
            "beat_control_pct near 50 means the entry filters performed like random "
            "entry at the same rate; only a value in the upper tail is evidence the "
            "timing carries information."
        )
    else:
        interpretation = "insufficient replicates or missing metric: no comparison is supported"

    return {
        "comparison_valid": verdict_available,
        "control_starved": starved,
        "empty_sample": empty_sample,
        "actual_events": actual_events,
        "control_events_max": max(control_events, default=0),
        "control_events_median": control_events_median,
        "control_activity_ratio": activity_ratio,
        "min_control_activity_ratio": MIN_CONTROL_ACTIVITY_RATIO,
        "metric": metric,
        "actual": actual_value,
        "control_replicates": len(control_values),
        "control_mean": mean(control_values) if control_values else None,
        "control_median": median(control_values) if control_values else None,
        "control_min": min(control_values) if control_values else None,
        "control_max": max(control_values) if control_values else None,
        "beat_control_pct": beat_pct,
        "actual_entries": actual.summary.get("closed_option_events"),
        "control_entries_mean": (
            mean([float(r.summary.get("closed_option_events") or 0) for r in controls])
            if controls else None
        ),
        "matched_entry_rates": entry_rates or {},
        # Deliberately not a verdict. With a handful of replicates this establishes
        # whether the signal is distinguishable from chance at all, not by how much, and
        # it inherits every research-grade blocker from the underlying run.
        "interpretation": interpretation,
        "research_grade": False,
        "research_grade_blockers": list(
            actual.summary.get("research_grade_blockers") or []
        ) + ["control replicates share one historical path, so this tests entry timing, not regime robustness"]
        + (
            ["option-chain availability is conditional on the signal, so this control is starved"]
            if starved else []
        )
        + (
            ["the real arm produced no events in the locked window, so no comparison is supported"]
            if empty_sample else []
        ),
    }
