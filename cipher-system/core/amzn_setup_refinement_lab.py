"""Second-pass AMZN setup validation focused on EMA reclaims and ORB structure.

This module is intentionally narrower than the broad setup lab.  It tests
pre-declared refinements suggested by the first pass: daily EMA reclaim
confirmation, moving-average pullbacks, tighter opening-range stops, VWAP
confirmation, retests, and failed-breakout fades.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from amzn_setup_strategy_lab import (
    AmznDataset,
    Bar,
    EXECUTIONS,
    NY,
    ExecutionAssumption,
    Trade,
    _adverse,
    _bootstrap_mean_ci,
    _ema,
    _profit_factor,
    _return,
    _simulate_intraday,
    summarize,
)


CORE = Path(__file__).resolve().parent
DEFAULT_DB = CORE.parent / "data" / "historical_equities" / "alpaca_amzn" / "equity_bars.sqlite"
DEFAULT_OUTPUT = CORE.parent / "data" / "historical_equities" / "amzn_setup_refinement"
UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class DailyMaSpec:
    name: str
    period: int
    setup: str
    tolerance: float
    target_r: float
    require_slope: bool
    require_volume: bool
    max_hold_days: int = 20


@dataclass(frozen=True, slots=True)
class FixedHorizonSpec:
    name: str
    close_buffer: float
    require_slope: bool
    require_volume: bool
    hold_days: int


@dataclass(frozen=True, slots=True)
class RefinedOrbSpec:
    name: str
    minutes: int
    direction: str
    target_r: float
    stop_mode: str
    trend_filter: bool
    volume_filter: bool
    vwap_filter: bool
    pattern: str


def fixed_daily_specs() -> tuple[DailyMaSpec, ...]:
    specs = []
    for period in (20, 50, 200):
        for setup in ("touch_reclaim", "cross_reclaim"):
            tolerances = (0.003, 0.0075) if setup == "touch_reclaim" else (0.0,)
            for tolerance in tolerances:
                for target_r in (1.5, 2.0):
                    for require_slope, require_volume in (
                        (False, False),
                        (True, False),
                        (False, True),
                        (True, True),
                    ):
                        specs.append(
                            DailyMaSpec(
                                name=(
                                    f"daily_ema{period}_{setup}_tol{int(tolerance*10000)}bp_"
                                    f"r{target_r:g}"
                                    + ("_slope" if require_slope else "")
                                    + ("_vol" if require_volume else "")
                                ),
                                period=period,
                                setup=setup,
                                tolerance=tolerance,
                                target_r=target_r,
                                require_slope=require_slope,
                                require_volume=require_volume,
                            )
                        )
    return tuple(specs)


def fixed_horizon_specs() -> tuple[FixedHorizonSpec, ...]:
    return tuple(
        FixedHorizonSpec(
            name=(
                f"daily_ema200_cross_hold{hold_days}_buf{int(buffer*10000)}bp"
                + ("_slope" if slope else "")
                + ("_vol" if volume else "")
            ),
            close_buffer=buffer,
            require_slope=slope,
            require_volume=volume,
            hold_days=hold_days,
        )
        for hold_days in (5, 10, 20, 40)
        for buffer in (0.0, 0.005, 0.01)
        for slope, volume in ((False, False), (True, False), (False, True), (True, True))
    )


def fixed_orb_specs() -> tuple[RefinedOrbSpec, ...]:
    specs = []
    filter_sets = (
        (True, True, False, "trend_vol"),
        (True, False, True, "trend_vwap"),
        (False, True, True, "vol_vwap"),
        (True, True, True, "allfilters"),
    )
    for minutes in (15, 30):
        for direction in ("long", "short"):
            for stop_mode in ("midpoint", "signal"):
                for target_r in (1.0, 1.5, 2.0):
                    for trend, volume, vwap, label in filter_sets:
                        specs.append(
                            RefinedOrbSpec(
                                f"orb{minutes}_{direction}_{stop_mode}_r{target_r:g}_{label}",
                                minutes,
                                direction,
                                target_r,
                                stop_mode,
                                trend,
                                volume,
                                vwap,
                                "breakout",
                            )
                        )
    for minutes in (15, 30):
        for direction in ("long", "short"):
            for target_r in (1.0, 1.5, 2.0):
                specs.append(
                    RefinedOrbSpec(
                        f"orb{minutes}_{direction}_retest_signal_r{target_r:g}_trendvol",
                        minutes,
                        direction,
                        target_r,
                        "signal",
                        True,
                        True,
                        False,
                        "retest",
                    )
                )
                specs.append(
                    RefinedOrbSpec(
                        f"orb{minutes}_{direction}_failed_fade_r{target_r:g}",
                        minutes,
                        direction,
                        target_r,
                        "signal",
                        False,
                        False,
                        False,
                        "failed_fade",
                    )
                )
    return tuple(specs)


def _rolling_average(values: Sequence[float], index: int, lookback: int) -> float | None:
    start = max(0, index - lookback)
    prior = values[start:index]
    return sum(prior) / len(prior) if len(prior) >= max(5, lookback // 2) else None


def _daily_features(dataset: AmznDataset, period: int) -> tuple[list[float | None], list[float | None]]:
    closes = [bar.close for bar in dataset.daily]
    volumes = [bar.volume for bar in dataset.daily]
    return _ema(closes, period), [
        _rolling_average(volumes, index, 20) for index in range(len(volumes))
    ]


def _daily_exit_trade(
    dataset: AmznDataset,
    spec: DailyMaSpec,
    signal_index: int,
    execution: ExecutionAssumption,
    ema_value: float,
) -> Trade | None:
    signal = dataset.daily[signal_index]
    if signal_index + 1 >= len(dataset.daily):
        return None
    entry_bar = dataset.daily[signal_index + 1]
    raw_entry = entry_bar.open
    atr = dataset.daily_atr14_values[signal_index]
    if atr is None:
        return None
    stop = min(signal.low, ema_value * 0.995, raw_entry - 0.75 * atr)
    risk = raw_entry - stop
    if risk <= 0 or risk / raw_entry > 0.15:
        return None
    target = raw_entry + spec.target_r * risk
    last_index = min(len(dataset.daily) - 1, signal_index + 1 + spec.max_hold_days)
    exit_raw = dataset.daily[last_index].close
    exit_at = dataset.daily[last_index].timestamp
    reason = "time_exit"
    for index in range(signal_index + 1, last_index + 1):
        bar = dataset.daily[index]
        if bar.open <= stop:
            exit_raw, exit_at, reason = bar.open, bar.timestamp, "stop_gap"
            break
        if bar.open >= target:
            exit_raw, exit_at, reason = target, bar.timestamp, "target"
            break
        if bar.low <= stop:
            exit_raw, exit_at, reason = stop, bar.timestamp, "stop"
            break
        if bar.high >= target:
            exit_raw, exit_at, reason = target, bar.timestamp, "target"
            break
    entry = _adverse(raw_entry, "long", "entry", execution.slippage_bps_per_side)
    exit_price = _adverse(exit_raw, "long", "exit", execution.slippage_bps_per_side)
    return Trade(
        spec.name,
        f"daily_ema{spec.period}",
        "long",
        signal.day,
        entry_bar.timestamp,
        exit_at,
        entry,
        exit_price,
        stop,
        target,
        reason,
        _return("long", raw_entry, exit_raw),
        _return("long", entry, exit_price),
        max(0, int((exit_at - entry_bar.timestamp).total_seconds() // 60)),
        risk / raw_entry,
        {
            "period": spec.period,
            "setup": spec.setup,
            "tolerance": spec.tolerance,
            "target_r": spec.target_r,
            "require_slope": spec.require_slope,
            "require_volume": spec.require_volume,
        },
    )


def simulate_daily_ma(
    dataset: AmznDataset,
    spec: DailyMaSpec,
    execution: ExecutionAssumption,
) -> list[Trade]:
    ema_values, average_volume = _daily_features(dataset, spec.period)
    trades = []
    active_until: datetime | None = None
    for index in range(spec.period + 20, len(dataset.daily) - 1):
        bar = dataset.daily[index]
        previous = dataset.daily[index - 1]
        ema = ema_values[index]
        prior_ema = ema_values[index - 1]
        slope_ema = ema_values[max(spec.period - 1, index - 20)]
        if ema is None or prior_ema is None or slope_ema is None:
            continue
        if active_until is not None and bar.timestamp <= active_until:
            continue
        if spec.require_slope and ema <= slope_ema:
            continue
        if spec.require_volume:
            baseline = average_volume[index]
            if baseline is None or bar.volume < 1.2 * baseline:
                continue
        if spec.setup == "touch_reclaim":
            signal = (
                previous.close > prior_ema
                and bar.low <= ema * (1.0 + spec.tolerance)
                and bar.close > ema
                and bar.close > bar.open
            )
        elif spec.setup == "cross_reclaim":
            signal = previous.close < prior_ema and bar.close > ema
        else:
            raise ValueError(spec.setup)
        if not signal:
            continue
        trade = _daily_exit_trade(dataset, spec, index, execution, ema)
        if trade:
            trades.append(trade)
            active_until = trade.exit_at
    return trades


def simulate_fixed_horizon(
    dataset: AmznDataset,
    spec: FixedHorizonSpec,
    execution: ExecutionAssumption,
) -> list[Trade]:
    ema200 = dataset.daily_ema200_values
    volumes = [bar.volume for bar in dataset.daily]
    average_volume = [_rolling_average(volumes, index, 20) for index in range(len(volumes))]
    trades = []
    active_until: datetime | None = None
    for index in range(220, len(dataset.daily) - spec.hold_days - 1):
        bar = dataset.daily[index]
        previous = dataset.daily[index - 1]
        ema = ema200[index]
        prior_ema = ema200[index - 1]
        slope_ema = ema200[index - 20]
        if ema is None or prior_ema is None or slope_ema is None:
            continue
        if active_until is not None and bar.timestamp <= active_until:
            continue
        if previous.close >= prior_ema or bar.close < ema * (1.0 + spec.close_buffer):
            continue
        if spec.require_slope and ema <= slope_ema:
            continue
        if spec.require_volume:
            baseline = average_volume[index]
            if baseline is None or bar.volume < 1.2 * baseline:
                continue
        entry_bar = dataset.daily[index + 1]
        exit_bar = dataset.daily[index + spec.hold_days]
        entry = _adverse(entry_bar.open, "long", "entry", execution.slippage_bps_per_side)
        exit_price = _adverse(exit_bar.close, "long", "exit", execution.slippage_bps_per_side)
        trade = Trade(
            spec.name,
            "daily_ema200_fixed_horizon",
            "long",
            bar.day,
            entry_bar.timestamp,
            exit_bar.timestamp,
            entry,
            exit_price,
            0.0,
            0.0,
            "fixed_horizon",
            _return("long", entry_bar.open, exit_bar.close),
            _return("long", entry, exit_price),
            int((exit_bar.timestamp - entry_bar.timestamp).total_seconds() // 60),
            0.0,
            {
                "hold_days": spec.hold_days,
                "close_buffer": spec.close_buffer,
                "require_slope": spec.require_slope,
                "require_volume": spec.require_volume,
            },
        )
        trades.append(trade)
        active_until = trade.exit_at
    return trades


def _vwap_values(bars: Sequence[Bar]) -> list[float | None]:
    output = []
    volume = value = 0.0
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3.0
        volume += bar.volume
        value += typical * bar.volume
        output.append(value / volume if volume > 0 else None)
    return output


def _trend_allows(dataset: AmznDataset, day: date, direction: str) -> bool:
    previous = dataset.previous_daily(day)
    if not previous:
        return False
    bar, ema200, _atr = previous
    if ema200 is None:
        return False
    return bar.close > ema200 if direction == "long" else bar.close < ema200


def simulate_refined_orb(
    dataset: AmznDataset,
    spec: RefinedOrbSpec,
    execution: ExecutionAssumption,
) -> list[Trade]:
    trades = []
    for day, bars in sorted(dataset.minutes_by_day.items()):
        if spec.trend_filter and not _trend_allows(dataset, day, spec.direction):
            continue
        open_local = datetime.combine(day, time(9, 30), tzinfo=NY)
        range_end = open_local + timedelta(minutes=spec.minutes)
        opening = [bar for bar in bars if bar.timestamp.astimezone(NY) < range_end]
        after = [bar for bar in bars if bar.timestamp.astimezone(NY) >= range_end]
        if len(opening) < max(3, int(spec.minutes * 0.7)) or len(after) < 3:
            continue
        or_high = max(bar.high for bar in opening)
        or_low = min(bar.low for bar in opening)
        midpoint = (or_high + or_low) / 2.0
        width_pct = (or_high - or_low) / opening[0].open
        if not 0.002 <= width_pct <= 0.035:
            continue
        if spec.volume_filter:
            average = dataset.opening_volume_history.get((day, spec.minutes))
            if average is None or sum(bar.volume for bar in opening) < 1.2 * average:
                continue
        vwaps = _vwap_values(bars)
        bar_to_index = {bar.timestamp: index for index, bar in enumerate(bars)}
        signal_index = None
        stop = None
        trade_direction = spec.direction
        if spec.pattern == "breakout":
            for index, bar in enumerate(after[:-1]):
                if bar.timestamp.astimezone(NY).time() > time(12, 0):
                    break
                full_index = bar_to_index[bar.timestamp]
                vwap = vwaps[full_index]
                breakout = bar.close > or_high if spec.direction == "long" else bar.close < or_low
                vwap_ok = True if not spec.vwap_filter else (
                    vwap is not None and (bar.close > vwap if spec.direction == "long" else bar.close < vwap)
                )
                if breakout and vwap_ok:
                    signal_index = index
                    if spec.stop_mode == "midpoint":
                        stop = midpoint
                    else:
                        stop = bar.low if spec.direction == "long" else bar.high
                    break
        elif spec.pattern == "retest":
            broke = False
            for index, bar in enumerate(after[:-1]):
                if bar.timestamp.astimezone(NY).time() > time(13, 0):
                    break
                if not broke:
                    broke = bar.close > or_high if spec.direction == "long" else bar.close < or_low
                    continue
                if spec.direction == "long" and bar.low <= or_high * 1.001 and bar.close > or_high:
                    signal_index, stop = index, bar.low
                    break
                if spec.direction == "short" and bar.high >= or_low * 0.999 and bar.close < or_low:
                    signal_index, stop = index, bar.high
                    break
        elif spec.pattern == "failed_fade":
            broke = False
            extreme = None
            for index, bar in enumerate(after[:-1]):
                if bar.timestamp.astimezone(NY).time() > time(14, 0):
                    break
                if spec.direction == "long":
                    if not broke and bar.close > or_high:
                        broke, extreme = True, bar.high
                        continue
                    if broke:
                        extreme = max(extreme or bar.high, bar.high)
                        if bar.close < or_high:
                            signal_index, stop, trade_direction = index, extreme, "short"
                            break
                else:
                    if not broke and bar.close < or_low:
                        broke, extreme = True, bar.low
                        continue
                    if broke:
                        extreme = min(extreme or bar.low, bar.low)
                        if bar.close > or_low:
                            signal_index, stop, trade_direction = index, extreme, "long"
                            break
        if signal_index is None or stop is None or signal_index + 1 >= len(after):
            continue
        entry_bar = after[signal_index + 1]
        risk = abs(entry_bar.open - stop)
        if risk <= 0:
            continue
        target = entry_bar.open + spec.target_r * risk if trade_direction == "long" else entry_bar.open - spec.target_r * risk
        trade = _simulate_intraday(
            strategy=spec.name,
            family=f"refined_orb_{spec.pattern}",
            direction=trade_direction,
            signal_date=day,
            entry_bar=entry_bar,
            later_bars=after[signal_index + 2 :],
            stop=stop,
            target=target,
            execution=execution,
            metadata={
                "opening_minutes": spec.minutes,
                "source_direction": spec.direction,
                "stop_mode": spec.stop_mode,
                "pattern": spec.pattern,
                "target_r": spec.target_r,
                "opening_range_pct": width_pct,
            },
        )
        if trade:
            trades.append(trade)
    return trades


def ema200_event_study(dataset: AmznDataset) -> dict[str, Any]:
    horizons = (1, 5, 10, 20, 40)
    events = []
    for index in range(200, len(dataset.daily) - max(horizons)):
        bar = dataset.daily[index]
        previous = dataset.daily[index - 1]
        ema = dataset.daily_ema200_values[index]
        prior_ema = dataset.daily_ema200_values[index - 1]
        if ema is None or prior_ema is None:
            continue
        if previous.close < prior_ema and bar.close > ema:
            returns = {
                str(horizon): dataset.daily[index + horizon].close / bar.close - 1.0
                for horizon in horizons
            }
            events.append({"date": bar.day.isoformat(), "returns": returns})
    summary = {}
    for horizon in horizons:
        values = [event["returns"][str(horizon)] for event in events]
        summary[str(horizon)] = {
            "events": len(values),
            "mean_return": sum(values) / len(values) if values else None,
            "median_return": median(values) if values else None,
            "positive_rate": sum(value > 0 for value in values) / len(values) if values else None,
            "worst": min(values) if values else None,
            "best": max(values) if values else None,
            "bootstrap_mean_return_ci_95": _bootstrap_mean_ci(values),
        }
    return {"events": events, "summary": summary}


def _run_all(dataset: AmznDataset) -> dict[tuple[str, str], list[Trade]]:
    runs: dict[tuple[str, str], list[Trade]] = {}
    for execution in EXECUTIONS:
        for spec in fixed_daily_specs():
            runs[(spec.name, execution.name)] = simulate_daily_ma(dataset, spec, execution)
        for spec in fixed_horizon_specs():
            runs[(spec.name, execution.name)] = simulate_fixed_horizon(dataset, spec, execution)
        for spec in fixed_orb_specs():
            runs[(spec.name, execution.name)] = simulate_refined_orb(dataset, spec, execution)
    return runs


def run_refinement(
    database_path: str | Path = DEFAULT_DB,
    output_directory: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    dataset = AmznDataset(database_path)
    runs = _run_all(dataset)
    windows = {
        "discovery_2016_2021": (date(2016, 1, 1), date(2021, 12, 31)),
        "validation_2022_2024": (date(2022, 1, 1), date(2024, 12, 31)),
        "holdout_2025_2026": (date(2025, 1, 1), date(2026, 7, 24)),
        "recent_2026": (date(2026, 1, 1), date(2026, 7, 24)),
        "full": (dataset.daily_dates[0], dataset.daily_dates[-1]),
    }
    rows = []
    for (strategy, execution_name), trades in sorted(runs.items()):
        family = trades[0].family if trades else strategy.split("_")[0]
        row = {"strategy": strategy, "family": family, "execution_assumption": execution_name}
        for name, (start, end) in windows.items():
            row[name] = summarize(trades, start, end)
        rows.append(row)
    severe = [row for row in rows if row["execution_assumption"] == "severe"]
    eligible = [
        row for row in severe
        if row["validation_2022_2024"]["trades"] >= 8
        and row["holdout_2025_2026"]["trades"] >= 6
        and row["validation_2022_2024"]["total_return_sum"] > 0
        and row["holdout_2025_2026"]["total_return_sum"] > 0
    ]
    eligible.sort(
        key=lambda row: (
            row["holdout_2025_2026"]["total_return_sum"],
            row["validation_2022_2024"]["total_return_sum"],
        ),
        reverse=True,
    )
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "AMZN_REFINEMENT_BAR_BASED_EXPLORATORY_ONLY",
        "research_claims_allowed": False,
        "dataset": {
            "database": str(Path(database_path).resolve()),
            "daily_bars": len(dataset.daily),
            "minute_bars_rth": sum(len(rows) for rows in dataset.minutes_by_day.values()),
            "start": dataset.daily_dates[0].isoformat(),
            "end": dataset.daily_dates[-1].isoformat(),
            "historical_nbbo": False,
        },
        "fixed_strategy_count": len({key[0] for key in runs}),
        "event_study": ema200_event_study(dataset),
        "rankings": eligible,
        "all_results": rows,
        "caveats": [
            "This second pass is exploratory and follows a broad first-pass screen.",
            "Historical bid/ask and queue position are unavailable.",
            "The fixed-horizon EMA study has no protective stop and can suffer large drawdowns.",
            "Multiple-testing and selection bias remain material despite chronological validation and holdout windows.",
        ],
    }
    write_outputs(payload, runs, output_directory)
    return payload


def write_outputs(
    payload: Mapping[str, Any],
    runs: Mapping[tuple[str, str], Sequence[Trade]],
    output_directory: str | Path,
) -> None:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / "amzn_setup_refinement_report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    with (output / "amzn_setup_refinement_rankings.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = [
            "strategy", "family", "execution_assumption",
            "validation_trades", "validation_return_sum",
            "holdout_trades", "holdout_return_sum", "holdout_mean_return",
            "holdout_win_rate", "holdout_worst_trade",
            "recent_trades", "recent_return_sum", "full_trades", "full_return_sum",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in payload["all_results"]:
            writer.writerow({
                "strategy": row["strategy"],
                "family": row["family"],
                "execution_assumption": row["execution_assumption"],
                "validation_trades": row["validation_2022_2024"]["trades"],
                "validation_return_sum": row["validation_2022_2024"]["total_return_sum"],
                "holdout_trades": row["holdout_2025_2026"]["trades"],
                "holdout_return_sum": row["holdout_2025_2026"]["total_return_sum"],
                "holdout_mean_return": row["holdout_2025_2026"]["mean_return"],
                "holdout_win_rate": row["holdout_2025_2026"]["win_rate"],
                "holdout_worst_trade": row["holdout_2025_2026"]["worst_trade"],
                "recent_trades": row["recent_2026"]["trades"],
                "recent_return_sum": row["recent_2026"]["total_return_sum"],
                "full_trades": row["full"]["trades"],
                "full_return_sum": row["full"]["total_return_sum"],
            })
    with (output / "amzn_setup_refinement_trades.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = ["strategy", "execution_assumption", *Trade.__dataclass_fields__.keys()]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for (strategy, execution_name), trades in sorted(runs.items()):
            for trade in trades:
                row = asdict(trade)
                row["strategy"] = strategy
                row["execution_assumption"] = execution_name
                row["signal_date"] = trade.signal_date.isoformat()
                row["entry_at"] = trade.entry_at.isoformat()
                row["exit_at"] = trade.exit_at.isoformat()
                row["metadata"] = json.dumps(trade.metadata, sort_keys=True)
                writer.writerow(row)


__all__ = [
    "DailyMaSpec",
    "FixedHorizonSpec",
    "RefinedOrbSpec",
    "ema200_event_study",
    "fixed_daily_specs",
    "fixed_horizon_specs",
    "fixed_orb_specs",
    "run_refinement",
    "simulate_daily_ma",
    "simulate_fixed_horizon",
    "simulate_refined_orb",
]
