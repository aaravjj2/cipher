"""Point-in-time AMZN setup research on adjusted Alpaca SIP bars.

The study intentionally separates signal formation from execution:
- daily signals enter at the next session open;
- intraday signals enter at the next minute/5-minute bar open;
- same-bar target/stop ambiguity resolves to the stop;
- every fill receives adverse side-aware slippage.

This is a bar-based research approximation, not historical NBBO evidence.
"""
from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


CORE = Path(__file__).resolve().parent
DEFAULT_DB = CORE.parent / "data" / "historical_equities" / "alpaca_amzn" / "equity_bars.sqlite"
DEFAULT_OUTPUT = CORE.parent / "data" / "historical_equities" / "amzn_setup_lab"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def day(self) -> date:
        return self.timestamp.astimezone(NY).date()


@dataclass(frozen=True, slots=True)
class Trade:
    strategy: str
    family: str
    direction: str
    signal_date: date
    entry_at: datetime
    exit_at: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    exit_reason: str
    gross_return: float
    net_return: float
    hold_minutes: int
    risk_pct: float
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OrbSpec:
    name: str
    minutes: int
    direction: str
    target_r: float
    trend_filter: bool
    volume_filter: bool
    retest: bool = False
    deadline: time = time(11, 30)


@dataclass(frozen=True, slots=True)
class IntradayEmaSpec:
    name: str
    tolerance: float
    target_r: float
    require_slope: bool


@dataclass(frozen=True, slots=True)
class DailyEmaSpec:
    name: str
    setup: str
    tolerance: float
    target_r: float
    max_hold_days: int


@dataclass(frozen=True, slots=True)
class SimpleIntradaySpec:
    name: str
    family: str
    target_r: float
    trend_filter: bool


@dataclass(frozen=True, slots=True)
class ExecutionAssumption:
    name: str
    slippage_bps_per_side: float


EXECUTIONS = (
    ExecutionAssumption("base", 2.0),
    ExecutionAssumption("worse", 5.0),
    ExecutionAssumption("severe", 10.0),
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ema(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    current = seed
    for index in range(period, len(values)):
        current = alpha * values[index] + (1.0 - alpha) * current
        result[index] = current
    return result


def _atr(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    tr = []
    for index, bar in enumerate(bars):
        if index == 0:
            tr.append(bar.high - bar.low)
        else:
            previous = bars[index - 1].close
            tr.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
    result: list[float | None] = [None] * len(bars)
    if len(tr) < period:
        return result
    current = sum(tr[:period]) / period
    result[period - 1] = current
    for index in range(period, len(tr)):
        current = ((period - 1) * current + tr[index]) / period
        result[index] = current
    return result


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _rth(value: datetime) -> bool:
    local = value.astimezone(NY)
    return local.weekday() < 5 and time(9, 30) <= local.time() < time(16, 0)


class AmznDataset:
    def __init__(self, database_path: str | Path = DEFAULT_DB) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.exists():
            raise FileNotFoundError(self.database_path)
        self.daily = self._load("1Day", regular_hours_only=False)
        minute = self._load("1Min", regular_hours_only=True)
        self.minutes_by_day: dict[date, list[Bar]] = defaultdict(list)
        for bar in minute:
            self.minutes_by_day[bar.day].append(bar)
        self.minutes_by_day = {
            day: sorted(rows, key=lambda row: row.timestamp)
            for day, rows in sorted(self.minutes_by_day.items())
            if len(rows) >= 300
        }
        self.daily = [bar for bar in self.daily if bar.day in self.minutes_by_day]
        self.daily_by_day = {bar.day: bar for bar in self.daily}
        self.daily_dates = [bar.day for bar in self.daily]
        closes = [bar.close for bar in self.daily]
        self.daily_ema200_values = _ema(closes, 200)
        self.daily_atr14_values = _atr(self.daily, 14)
        self.daily_index = {bar.day: index for index, bar in enumerate(self.daily)}
        self.daily_ema200 = {
            bar.day: self.daily_ema200_values[index]
            for index, bar in enumerate(self.daily)
        }
        self.daily_atr14 = {
            bar.day: self.daily_atr14_values[index]
            for index, bar in enumerate(self.daily)
        }
        self.five_minute = self._resample_five_minute()
        five_closes = [bar.close for bar in self.five_minute]
        self.five_ema200_values = _ema(five_closes, 200)
        self.five_index = {bar.timestamp: index for index, bar in enumerate(self.five_minute)}
        self.five_by_day: dict[date, list[Bar]] = defaultdict(list)
        for bar in self.five_minute:
            self.five_by_day[bar.day].append(bar)
        self.opening_volume_history = self._opening_volume_history()

    def _load(self, timeframe: str, *, regular_hours_only: bool) -> list[Bar]:
        rows: list[Bar] = []
        with sqlite3.connect(self.database_path) as db:
            cursor = db.execute(
                """select timestamp,open,high,low,close,volume from bars
                   where symbol='AMZN' and timeframe=? order by timestamp""",
                (timeframe,),
            )
            for raw in cursor:
                timestamp = _parse_timestamp(str(raw[0]))
                if regular_hours_only and not _rth(timestamp):
                    continue
                values = [_finite(value, math.nan) for value in raw[1:5]]
                if not all(math.isfinite(value) and value > 0 for value in values):
                    continue
                rows.append(
                    Bar(
                        timestamp,
                        values[0],
                        values[1],
                        values[2],
                        values[3],
                        max(0.0, _finite(raw[5])),
                    )
                )
        return rows

    def _resample_five_minute(self) -> list[Bar]:
        output = []
        for day, bars in sorted(self.minutes_by_day.items()):
            buckets: dict[int, list[Bar]] = defaultdict(list)
            for bar in bars:
                local = bar.timestamp.astimezone(NY)
                offset = (local.hour * 60 + local.minute) - (9 * 60 + 30)
                if 0 <= offset < 390:
                    buckets[offset // 5].append(bar)
            for bucket in sorted(buckets):
                rows = buckets[bucket]
                if not rows:
                    continue
                output.append(
                    Bar(
                        rows[0].timestamp,
                        rows[0].open,
                        max(row.high for row in rows),
                        min(row.low for row in rows),
                        rows[-1].close,
                        sum(row.volume for row in rows),
                    )
                )
        return output

    def _opening_volume_history(self) -> dict[tuple[date, int], float | None]:
        result: dict[tuple[date, int], float | None] = {}
        history: dict[int, list[float]] = defaultdict(list)
        for day, bars in sorted(self.minutes_by_day.items()):
            for minutes in (5, 15, 30):
                cutoff = datetime.combine(day, time(9, 30), tzinfo=NY) + timedelta(minutes=minutes)
                volume = sum(bar.volume for bar in bars if bar.timestamp.astimezone(NY) < cutoff)
                prior = history[minutes][-20:]
                result[(day, minutes)] = sum(prior) / len(prior) if len(prior) >= 10 else None
                history[minutes].append(volume)
        return result

    def previous_daily(self, day: date) -> tuple[Bar, float | None, float | None] | None:
        index = self.daily_index.get(day)
        if index is None or index == 0:
            return None
        previous = self.daily[index - 1]
        return previous, self.daily_ema200_values[index - 1], self.daily_atr14_values[index - 1]


def fixed_orb_specs() -> tuple[OrbSpec, ...]:
    specs = []
    for minutes in (5, 15, 30):
        for direction in ("long", "short"):
            for target_r in (1.0, 1.5, 2.0):
                for trend_filter in (False, True):
                    for volume_filter in (False, True):
                        suffix = f"orb{minutes}_{direction}_r{target_r:g}"
                        suffix += "_trend" if trend_filter else "_all"
                        suffix += "_vol" if volume_filter else ""
                        specs.append(
                            OrbSpec(
                                suffix,
                                minutes,
                                direction,
                                target_r,
                                trend_filter,
                                volume_filter,
                            )
                        )
    for minutes in (15, 30):
        for target_r in (1.5, 2.0):
            specs.append(
                OrbSpec(
                    f"orb{minutes}_long_retest_r{target_r:g}_trend",
                    minutes,
                    "long",
                    target_r,
                    True,
                    False,
                    retest=True,
                    deadline=time(13, 0),
                )
            )
    return tuple(specs)


def fixed_intraday_ema_specs() -> tuple[IntradayEmaSpec, ...]:
    return tuple(
        IntradayEmaSpec(
            f"ema200_5m_bounce_tol{int(tolerance * 10000)}bp_r{target:g}" + ("_slope" if slope else ""),
            tolerance,
            target,
            slope,
        )
        for tolerance in (0.001, 0.0025, 0.005)
        for target in (1.5, 2.0)
        for slope in (False, True)
    )


def fixed_daily_ema_specs() -> tuple[DailyEmaSpec, ...]:
    specs = []
    for setup in ("touch_reclaim", "cross_reclaim", "confirmed_bounce"):
        tolerances = (0.005, 0.01) if setup != "cross_reclaim" else (0.0,)
        for tolerance in tolerances:
            for target in (1.5, 2.0):
                specs.append(
                    DailyEmaSpec(
                        f"daily_ema200_{setup}_tol{int(tolerance * 10000)}bp_r{target:g}",
                        setup,
                        tolerance,
                        target,
                        15,
                    )
                )
    return tuple(specs)


def fixed_simple_specs() -> tuple[SimpleIntradaySpec, ...]:
    return (
        SimpleIntradaySpec("vwap_reclaim_r1_5_trend", "vwap_reclaim", 1.5, True),
        SimpleIntradaySpec("vwap_reclaim_r2_trend", "vwap_reclaim", 2.0, True),
        SimpleIntradaySpec("gap_go_r1_5", "gap_go", 1.5, False),
        SimpleIntradaySpec("gap_go_r2", "gap_go", 2.0, False),
        SimpleIntradaySpec("prior_day_high_breakout_r1_5_trend", "pdh_breakout", 1.5, True),
        SimpleIntradaySpec("prior_day_high_breakout_r2_trend", "pdh_breakout", 2.0, True),
        SimpleIntradaySpec("first_hour_breakout_r1_5_trend", "first_hour_breakout", 1.5, True),
        SimpleIntradaySpec("first_hour_breakout_r2_trend", "first_hour_breakout", 2.0, True),
    )


def _adverse(price: float, direction: str, action: str, bps: float) -> float:
    fraction = bps / 10000.0
    buying = (direction == "long" and action == "entry") or (
        direction == "short" and action == "exit"
    )
    return price * (1.0 + fraction if buying else 1.0 - fraction)


def _return(direction: str, entry: float, exit_price: float) -> float:
    return (exit_price - entry) / entry if direction == "long" else (entry - exit_price) / entry


def _simulate_intraday(
    *,
    strategy: str,
    family: str,
    direction: str,
    signal_date: date,
    entry_bar: Bar,
    later_bars: Sequence[Bar],
    stop: float,
    target: float,
    execution: ExecutionAssumption,
    metadata: dict[str, Any],
) -> Trade | None:
    raw_entry = entry_bar.open
    if raw_entry <= 0:
        return None
    if direction == "long" and stop >= raw_entry:
        return None
    if direction == "short" and stop <= raw_entry:
        return None
    risk_pct = abs(raw_entry - stop) / raw_entry
    if not 0.001 <= risk_pct <= 0.05:
        return None
    exit_raw = later_bars[-1].close if later_bars else entry_bar.close
    exit_at = later_bars[-1].timestamp if later_bars else entry_bar.timestamp
    reason = "close"
    for bar in later_bars:
        if direction == "long":
            if bar.open <= stop:
                exit_raw, reason = bar.open, "stop_gap"
                exit_at = bar.timestamp
                break
            if bar.open >= target:
                exit_raw, reason = target, "target"
                exit_at = bar.timestamp
                break
            if bar.low <= stop:
                exit_raw, reason = stop, "stop"
                exit_at = bar.timestamp
                break
            if bar.high >= target:
                exit_raw, reason = target, "target"
                exit_at = bar.timestamp
                break
        else:
            if bar.open >= stop:
                exit_raw, reason = bar.open, "stop_gap"
                exit_at = bar.timestamp
                break
            if bar.open <= target:
                exit_raw, reason = target, "target"
                exit_at = bar.timestamp
                break
            if bar.high >= stop:
                exit_raw, reason = stop, "stop"
                exit_at = bar.timestamp
                break
            if bar.low <= target:
                exit_raw, reason = target, "target"
                exit_at = bar.timestamp
                break
    entry = _adverse(raw_entry, direction, "entry", execution.slippage_bps_per_side)
    exit_price = _adverse(exit_raw, direction, "exit", execution.slippage_bps_per_side)
    gross = _return(direction, raw_entry, exit_raw)
    net = _return(direction, entry, exit_price)
    hold = max(0, int((exit_at - entry_bar.timestamp).total_seconds() // 60))
    return Trade(
        strategy,
        family,
        direction,
        signal_date,
        entry_bar.timestamp,
        exit_at,
        entry,
        exit_price,
        stop,
        target,
        reason,
        gross,
        net,
        hold,
        risk_pct,
        metadata,
    )


def _trend_allows(dataset: AmznDataset, day: date, direction: str) -> bool:
    previous = dataset.previous_daily(day)
    if not previous:
        return False
    bar, ema200, _atr14 = previous
    if ema200 is None:
        return False
    return bar.close > ema200 if direction == "long" else bar.close < ema200


def simulate_orb(dataset: AmznDataset, spec: OrbSpec, execution: ExecutionAssumption) -> list[Trade]:
    trades = []
    for day, bars in sorted(dataset.minutes_by_day.items()):
        if spec.trend_filter and not _trend_allows(dataset, day, spec.direction):
            continue
        open_at = datetime.combine(day, time(9, 30), tzinfo=NY)
        range_end = open_at + timedelta(minutes=spec.minutes)
        opening = [bar for bar in bars if bar.timestamp.astimezone(NY) < range_end]
        after = [bar for bar in bars if bar.timestamp.astimezone(NY) >= range_end]
        if len(opening) < max(3, int(spec.minutes * 0.7)) or len(after) < 2:
            continue
        or_high = max(bar.high for bar in opening)
        or_low = min(bar.low for bar in opening)
        width = or_high - or_low
        width_pct = width / opening[0].open
        if not 0.002 <= width_pct <= 0.04:
            continue
        if spec.volume_filter:
            average = dataset.opening_volume_history.get((day, spec.minutes))
            if average is None or sum(bar.volume for bar in opening) < 1.2 * average:
                continue
        breakout_index = None
        if not spec.retest:
            for index, bar in enumerate(after[:-1]):
                local_time = bar.timestamp.astimezone(NY).time()
                if local_time > spec.deadline:
                    break
                if spec.direction == "long" and bar.close > or_high:
                    breakout_index = index
                    break
                if spec.direction == "short" and bar.close < or_low:
                    breakout_index = index
                    break
        else:
            broke = False
            for index, bar in enumerate(after[:-1]):
                local_time = bar.timestamp.astimezone(NY).time()
                if local_time > spec.deadline:
                    break
                if not broke and bar.close > or_high:
                    broke = True
                    continue
                if broke and bar.low <= or_high * 1.001 and bar.close > or_high:
                    breakout_index = index
                    break
        if breakout_index is None or breakout_index + 1 >= len(after):
            continue
        entry_bar = after[breakout_index + 1]
        stop = or_low if spec.direction == "long" else or_high
        raw_entry = entry_bar.open
        risk = abs(raw_entry - stop)
        target = raw_entry + spec.target_r * risk if spec.direction == "long" else raw_entry - spec.target_r * risk
        trade = _simulate_intraday(
            strategy=spec.name,
            family="orb_retest" if spec.retest else "orb",
            direction=spec.direction,
            signal_date=day,
            entry_bar=entry_bar,
            later_bars=after[breakout_index + 2 :],
            stop=stop,
            target=target,
            execution=execution,
            metadata={
                "opening_minutes": spec.minutes,
                "opening_range_pct": width_pct,
                "trend_filter": spec.trend_filter,
                "volume_filter": spec.volume_filter,
                "retest": spec.retest,
            },
        )
        if trade:
            trades.append(trade)
    return trades


def simulate_intraday_ema(
    dataset: AmznDataset, spec: IntradayEmaSpec, execution: ExecutionAssumption
) -> list[Trade]:
    trades = []
    for day, bars in sorted(dataset.five_by_day.items()):
        if not _trend_allows(dataset, day, "long"):
            continue
        for index in range(2, len(bars) - 1):
            bar = bars[index]
            local_time = bar.timestamp.astimezone(NY).time()
            if not time(10, 0) <= local_time <= time(14, 30):
                continue
            global_index = dataset.five_index[bar.timestamp]
            ema200 = dataset.five_ema200_values[global_index]
            prior_ema = dataset.five_ema200_values[max(0, global_index - 10)]
            if ema200 is None or prior_ema is None:
                continue
            if spec.require_slope and ema200 <= prior_ema:
                continue
            prior = bars[index - 1]
            touched = bar.low <= ema200 * (1.0 + spec.tolerance)
            reclaimed = bar.close > ema200 and bar.close > bar.open
            was_above = prior.close >= (dataset.five_ema200_values[global_index - 1] or ema200)
            if not (touched and reclaimed and was_above):
                continue
            entry_bar = bars[index + 1]
            stop = min(bar.low, ema200 * (1.0 - 0.001))
            risk = entry_bar.open - stop
            if risk <= 0:
                continue
            target = entry_bar.open + spec.target_r * risk
            trade = _simulate_intraday(
                strategy=spec.name,
                family="intraday_ema200_bounce",
                direction="long",
                signal_date=day,
                entry_bar=entry_bar,
                later_bars=bars[index + 2 :],
                stop=stop,
                target=target,
                execution=execution,
                metadata={"ema200": ema200, "tolerance": spec.tolerance, "target_r": spec.target_r},
            )
            if trade:
                trades.append(trade)
            break
    return trades


def _daily_trade(
    dataset: AmznDataset,
    spec: DailyEmaSpec,
    signal_index: int,
    execution: ExecutionAssumption,
) -> Trade | None:
    signal = dataset.daily[signal_index]
    if signal_index + 1 >= len(dataset.daily):
        return None
    entry_bar = dataset.daily[signal_index + 1]
    raw_entry = entry_bar.open
    atr14 = dataset.daily_atr14_values[signal_index]
    if atr14 is None:
        return None
    stop = min(signal.low, raw_entry - 0.75 * atr14)
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
        "daily_ema200_bounce",
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
        {"setup": spec.setup, "target_r": spec.target_r, "tolerance": spec.tolerance},
    )


def simulate_daily_ema(dataset: AmznDataset, spec: DailyEmaSpec, execution: ExecutionAssumption) -> list[Trade]:
    trades = []
    active_until: datetime | None = None
    for index in range(200, len(dataset.daily) - 1):
        bar = dataset.daily[index]
        previous = dataset.daily[index - 1]
        ema200 = dataset.daily_ema200_values[index]
        prior_ema = dataset.daily_ema200_values[index - 1]
        if ema200 is None or prior_ema is None:
            continue
        if active_until is not None and bar.timestamp <= active_until:
            continue
        touch = bar.low <= ema200 * (1.0 + spec.tolerance)
        close_above = bar.close > ema200
        bullish = bar.close > bar.open
        if spec.setup == "touch_reclaim":
            signal = previous.close > prior_ema and touch and close_above and bullish
        elif spec.setup == "cross_reclaim":
            signal = previous.close < prior_ema and bar.close > ema200
        elif spec.setup == "confirmed_bounce":
            signal = previous.close > prior_ema and touch and close_above and bar.close > previous.high
        else:
            raise ValueError(spec.setup)
        if not signal:
            continue
        trade = _daily_trade(dataset, spec, index, execution)
        if trade:
            trades.append(trade)
            active_until = trade.exit_at
    return trades


def _intraday_vwap(bars: Sequence[Bar]) -> list[float | None]:
    output = []
    volume = value = 0.0
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3.0
        volume += bar.volume
        value += typical * bar.volume
        output.append(value / volume if volume > 0 else None)
    return output


def simulate_simple(
    dataset: AmznDataset, spec: SimpleIntradaySpec, execution: ExecutionAssumption
) -> list[Trade]:
    trades = []
    for day, bars in sorted(dataset.minutes_by_day.items()):
        if spec.trend_filter and not _trend_allows(dataset, day, "long"):
            continue
        previous = dataset.previous_daily(day)
        if not previous:
            continue
        previous_bar, _ema, atr14 = previous
        if atr14 is None:
            continue
        signal_index = None
        stop = None
        if spec.family == "vwap_reclaim":
            vwaps = _intraday_vwap(bars)
            for index in range(15, min(len(bars) - 1, 180)):
                local_time = bars[index].timestamp.astimezone(NY).time()
                if local_time > time(12, 30):
                    break
                current_vwap = vwaps[index]
                previous_vwap = vwaps[index - 1]
                if current_vwap is None or previous_vwap is None:
                    continue
                if bars[index - 1].close <= previous_vwap and bars[index].close > current_vwap:
                    signal_index = index
                    stop = min(bars[index].low, current_vwap - 0.15 * atr14)
                    break
        elif spec.family == "gap_go":
            gap = bars[0].open / previous_bar.close - 1.0
            opening = bars[:15]
            if 0.01 <= gap <= 0.08 and len(opening) >= 12:
                opening_high = max(bar.high for bar in opening)
                opening_low = min(bar.low for bar in opening)
                if opening[-1].close > opening[0].open:
                    for index in range(15, min(len(bars) - 1, 90)):
                        if bars[index].close > opening_high:
                            signal_index, stop = index, opening_low
                            break
        elif spec.family == "pdh_breakout":
            for index in range(5, min(len(bars) - 1, 180)):
                if bars[index].close > previous_bar.high:
                    signal_index = index
                    stop = min(bar.low for bar in bars[max(0, index - 5) : index + 1])
                    break
        elif spec.family == "first_hour_breakout":
            opening = bars[:60]
            if len(opening) >= 50:
                opening_high = max(bar.high for bar in opening)
                opening_low = min(bar.low for bar in opening)
                for index in range(60, min(len(bars) - 1, 210)):
                    if bars[index].close > opening_high:
                        signal_index, stop = index, opening_low
                        break
        if signal_index is None or stop is None or signal_index + 1 >= len(bars):
            continue
        entry_bar = bars[signal_index + 1]
        risk = entry_bar.open - stop
        if risk <= 0:
            continue
        target = entry_bar.open + spec.target_r * risk
        trade = _simulate_intraday(
            strategy=spec.name,
            family=spec.family,
            direction="long",
            signal_date=day,
            entry_bar=entry_bar,
            later_bars=bars[signal_index + 2 :],
            stop=stop,
            target=target,
            execution=execution,
            metadata={"target_r": spec.target_r, "trend_filter": spec.trend_filter},
        )
        if trade:
            trades.append(trade)
    return trades


def _max_drawdown(returns: Sequence[float]) -> float | None:
    if not returns:
        return None
    equity = peak = 0.0
    drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _profit_factor(values: Sequence[float]) -> float | str | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses == 0:
        return "Infinity" if gains > 0 else None
    return gains / losses


def _bootstrap_mean_ci(values: Sequence[float], *, seed: int = 1729, samples: int = 2000) -> list[float] | None:
    if len(values) < 8:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        draw = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(draw) / len(draw))
    means.sort()
    return [means[int(samples * 0.025)], means[int(samples * 0.975) - 1]]


def summarize(trades: Sequence[Trade], start: date, end: date) -> dict[str, Any]:
    selected = sorted(
        (trade for trade in trades if start <= trade.signal_date <= end),
        key=lambda trade: trade.entry_at,
    )
    returns = [trade.net_return for trade in selected]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "trades": len(selected),
        "total_return_sum": sum(returns),
        "mean_return": _mean(returns),
        "median_return": median(returns) if returns else None,
        "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
        "worst_trade": min(returns) if returns else None,
        "best_trade": max(returns) if returns else None,
        "profit_factor": _profit_factor(returns),
        "max_drawdown_return_sum": _max_drawdown(returns),
        "average_hold_minutes": _mean([float(trade.hold_minutes) for trade in selected]),
        "bootstrap_mean_return_ci_95": _bootstrap_mean_ci(returns),
        "exit_reasons": dict(Counter(trade.exit_reason for trade in selected)),
        "year_returns": {
            str(year): sum(trade.net_return for trade in selected if trade.signal_date.year == year)
            for year in sorted({trade.signal_date.year for trade in selected})
        },
    }


def _fixed_runs(dataset: AmznDataset) -> dict[tuple[str, str], list[Trade]]:
    runs: dict[tuple[str, str], list[Trade]] = {}
    for execution in EXECUTIONS:
        for spec in fixed_orb_specs():
            runs[(spec.name, execution.name)] = simulate_orb(dataset, spec, execution)
        for spec in fixed_intraday_ema_specs():
            runs[(spec.name, execution.name)] = simulate_intraday_ema(dataset, spec, execution)
        for spec in fixed_daily_ema_specs():
            runs[(spec.name, execution.name)] = simulate_daily_ema(dataset, spec, execution)
        for spec in fixed_simple_specs():
            runs[(spec.name, execution.name)] = simulate_simple(dataset, spec, execution)
    return runs


def run_lab(
    database_path: str | Path = DEFAULT_DB,
    output_directory: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    dataset = AmznDataset(database_path)
    runs = _fixed_runs(dataset)
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
        row = {
            "strategy": strategy,
            "family": family,
            "execution_assumption": execution_name,
        }
        for name, (start, end) in windows.items():
            row[name] = summarize(trades, start, end)
        rows.append(row)
    severe = [row for row in rows if row["execution_assumption"] == "severe"]
    eligible = [
        row
        for row in severe
        if row["validation_2022_2024"]["trades"] >= 10
        and row["holdout_2025_2026"]["trades"] >= 8
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
        "status": "AMZN_BAR_BASED_EXPLORATORY_ONLY",
        "research_claims_allowed": False,
        "dataset": {
            "database": str(Path(database_path).resolve()),
            "daily_bars": len(dataset.daily),
            "minute_bars_rth": sum(len(rows) for rows in dataset.minutes_by_day.values()),
            "trading_days": len(dataset.minutes_by_day),
            "start": dataset.daily_dates[0].isoformat(),
            "end": dataset.daily_dates[-1].isoformat(),
            "adjustment": "all",
            "historical_nbbo": False,
        },
        "fixed_strategy_count": len({key[0] for key in runs}),
        "execution_assumptions": [asdict(item) for item in EXECUTIONS],
        "windows": {name: [start.isoformat(), end.isoformat()] for name, (start, end) in windows.items()},
        "rankings": eligible,
        "all_results": rows,
        "caveats": [
            "Minute and daily OHLCV bars are not historical bid/ask quotes.",
            "The strategy family was fixed before results were ranked, but multiple-testing risk remains.",
            "Intraday entries occur at the next bar open and same-bar target/stop ambiguity resolves to the stop.",
            "No earnings-calendar exclusion is applied in this first pass.",
            "Returns are per-trade percentage returns and are not a continuously compounded portfolio curve.",
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
    (output / "amzn_setup_strategy_report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    with (output / "amzn_setup_strategy_rankings.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = [
            "strategy",
            "family",
            "execution_assumption",
            "validation_trades",
            "validation_return_sum",
            "holdout_trades",
            "holdout_return_sum",
            "holdout_mean_return",
            "holdout_win_rate",
            "holdout_worst_trade",
            "recent_trades",
            "recent_return_sum",
            "full_trades",
            "full_return_sum",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in payload["all_results"]:
            writer.writerow(
                {
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
                }
            )
    with (output / "amzn_setup_strategy_trades.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = [
            "strategy",
            "execution_assumption",
            *Trade.__dataclass_fields__.keys(),
        ]
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
    "AmznDataset",
    "Bar",
    "DailyEmaSpec",
    "ExecutionAssumption",
    "IntradayEmaSpec",
    "OrbSpec",
    "Trade",
    "fixed_daily_ema_specs",
    "fixed_intraday_ema_specs",
    "fixed_orb_specs",
    "fixed_simple_specs",
    "run_lab",
    "simulate_daily_ema",
    "simulate_intraday_ema",
    "simulate_orb",
]
