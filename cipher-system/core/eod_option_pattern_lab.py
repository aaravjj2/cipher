"""Options translation of every SPY/QQQ/IWM EOD pattern.

The lab imports all 429 point-in-time pattern definitions from
:mod:`eod_pattern_lab` and evaluates both the tested and inverse direction using
three defined-risk option structures across four expiration buckets.

Data limitations
----------------
Alpaca historical option NBBO quotes are unavailable. Execution is therefore a
conservative approximation from one-minute OPRA trade bars:

* long buys use the minute high plus a slippage haircut;
* long sells use the minute low minus a slippage haircut;
* spread short-leg sales use the minute low minus a haircut;
* spread short-leg repurchases use the minute high plus a haircut;
* per-contract fees are charged on every leg and side;
* missing bars are never forward-filled;
* expiration-close exits may fall back to intrinsic value.

The results are research approximations, not research-grade executable option
returns and not an order-routing system.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np
from scipy import stats

from eod_pattern_lab import (
    IDX_1400,
    IDX_1500,
    IDX_CLOSE,
    MIN_REPORT_N,
    NY,
    PatternSpec,
    SYMBOLS,
    block_bootstrap_ci,
    build_patterns,
    hac_mean_se,
    load_sessions,
)


# Explicit paths keep this module runnable as a standalone script.
CORE = Path(__file__).resolve().parent
CIPHER_ROOT = CORE.parent
DEFAULT_EQUITY_DB = CIPHER_ROOT / "data" / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite"
DEFAULT_ARCHIVE_ROOT = CIPHER_ROOT / "data" / "historical_options" / "eod_indices_targeted"
DEFAULT_OUT = CIPHER_ROOT / "data" / "eod_option_pattern_lab"
ANALYSIS_START_DAY = date(2026, 1, 26)
ANALYSIS_END_DAY = date(2026, 7, 24)
RECENT_START_DAY = date(2026, 4, 25)
UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class ExecutionModel:
    name: str
    fraction: float
    floor: float
    fee_per_contract_side: float

    def buy(self, observed_high: float) -> float:
        return observed_high + max(self.floor, observed_high * self.fraction)

    def sell(self, observed_low: float) -> float:
        return max(0.0, observed_low - max(self.floor, observed_low * self.fraction))


EXECUTION_MODELS: tuple[ExecutionModel, ...] = (
    ExecutionModel("base", 0.03, 0.03, 0.75),
    ExecutionModel("worse", 0.05, 0.05, 0.75),
    ExecutionModel("severe", 0.10, 0.10, 1.00),
)
BUCKETS = ("0dte", "front", "weekly", "swing")
STRUCTURES = ("atm_debit", "otm075_debit", "atm_otm075_spread")
DIRECTION_MODES = ("as_tested", "inverse")


@dataclass(frozen=True, slots=True)
class SelectedContract:
    symbol: str
    expiration_date: str
    strike: float
    dte: int
    option_type: str
    target_style: str
    checkpoint: str


@dataclass(slots=True)
class OptionOutcome:
    cache_key: str
    symbol: str
    day: str
    actual_side: str
    option_type: str
    bucket: str
    structure: str
    execution_model: str
    entry_day: str
    entry_time_et: str
    exit_day: str
    exit_time_et: str
    selection_checkpoint: str
    close_entry_hypothetical: bool
    long_contract: str | None
    long_expiration: str | None
    long_strike: float | None
    short_contract: str | None
    short_strike: float | None
    entry_debit: float | None
    exit_value: float | None
    fees_dollars: float | None
    risk_capital_dollars: float | None
    pnl_dollars: float | None
    return_on_risk_pct: float | None
    entry_delay_minutes: int | None
    exit_delay_minutes: int | None
    status: str
    skip_reason: str | None


@dataclass(slots=True)
class OptionPatternResult:
    result_id: str
    pattern_id: str
    symbol: str
    family: str
    name: str
    signal_time: str
    holding_period: str
    tested_side: str
    direction_mode: str
    actual_side: str
    bucket: str
    structure: str
    execution_model: str
    signal_n: int
    executed_n: int
    coverage_pct: float | None
    mean_return_pct: float | None
    median_return_pct: float | None
    trimmed_mean_return_pct: float | None
    exclude_best_3_mean_return_pct: float | None
    first_half_mean_return_pct: float | None
    second_half_mean_return_pct: float | None
    win_rate_pct: float | None
    profit_factor: float | None
    mean_pnl_dollars: float | None
    total_pnl_dollars: float | None
    median_risk_capital_dollars: float | None
    hac_t_stat: float | None
    hac_p_value: float | None
    global_fdr_q: float | None
    variant_fdr_q: float | None
    positive_global_fdr_q: float | None
    positive_variant_fdr_q: float | None
    bootstrap_ci_low_pct: float | None
    bootstrap_ci_high_pct: float | None
    recent_n: int
    recent_mean_return_pct: float | None
    recent_median_return_pct: float | None
    recent_win_rate_pct: float | None
    stable_positive: bool | None
    best_return_pct: float | None
    worst_return_pct: float | None
    max_drawdown_one_contract_dollars: float | None
    max_losing_streak: int | None
    close_entry_hypothetical_count: int


def _flip(side: str) -> str:
    return "short" if side == "long" else "long"


def _clock_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _parse_clock(raw: str) -> time:
    hour, minute = (int(part) for part in raw.split(":", 1))
    return time(hour, minute)


def _market_dt(day: str, clock: time) -> datetime:
    return datetime.combine(date.fromisoformat(day), clock, tzinfo=NY)


def _result_id(parts: Sequence[str]) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _safe_mean(values: Sequence[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _safe_median(values: Sequence[float]) -> float | None:
    return float(np.median(values)) if values else None


def _trimmed_mean(values: Sequence[float], proportion: float = 0.10) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    trim = int(len(ordered) * proportion)
    if trim and len(ordered) > 2 * trim:
        ordered = ordered[trim:-trim]
    return float(np.mean(ordered))


class OptionArchive:
    def __init__(self, symbol: str, db_path: Path, *, cache_days: int = 3000) -> None:
        self.symbol = symbol
        self.db_path = db_path
        if not db_path.exists():
            raise FileNotFoundError(db_path)
        self.db = sqlite3.connect(db_path)
        self.db.execute("pragma query_only=on")
        self.cache_days = max(100, int(cache_days))
        self._bars_cache: OrderedDict[tuple[str, str], dict[int, tuple[float, float, float, float, float]]] = OrderedDict()
        self.selection_map: dict[tuple[str, str, str, str], list[SelectedContract]] = defaultdict(list)
        rows = self.db.execute(
            """select decision_date,bucket,checkpoint,target_style,option_type,
                      symbol,expiration_date,strike,dte
               from eod_contract_selections
               order by decision_date,bucket,checkpoint,target_style,option_type,strike"""
        ).fetchall()
        for decision_date, bucket, checkpoint, target_style, option_type, contract, expiry, strike, dte in rows:
            self.selection_map[(decision_date, bucket, checkpoint, option_type)].append(
                SelectedContract(
                    symbol=str(contract),
                    expiration_date=str(expiry),
                    strike=float(strike),
                    dte=int(dte),
                    option_type=str(option_type),
                    target_style=str(target_style),
                    checkpoint=str(checkpoint),
                )
            )
        for key, values in list(self.selection_map.items()):
            unique: dict[str, SelectedContract] = {}
            for row in values:
                unique[row.symbol] = row
            self.selection_map[key] = sorted(unique.values(), key=lambda row: (row.strike, row.symbol))

    def close(self) -> None:
        self.db.close()

    def contracts(
        self,
        decision_day: str,
        bucket: str,
        checkpoint: str,
        option_type: str,
    ) -> list[SelectedContract]:
        return self.selection_map.get((decision_day, bucket, checkpoint, option_type), [])

    def day_bars(self, contract: str, day: str) -> dict[int, tuple[float, float, float, float, float]]:
        key = (contract, day)
        cached = self._bars_cache.get(key)
        if cached is not None:
            self._bars_cache.move_to_end(key)
            return cached
        start = datetime.combine(date.fromisoformat(day), time(0, 0), tzinfo=NY).astimezone(UTC)
        end = (start.astimezone(NY) + timedelta(days=1)).astimezone(UTC)
        rows = self.db.execute(
            """select timestamp,open,high,low,close,volume
               from option_bars
               where symbol=? and timestamp>=? and timestamp<?
               order by timestamp""",
            (
                contract,
                start.isoformat().replace("+00:00", "Z"),
                end.isoformat().replace("+00:00", "Z"),
            ),
        ).fetchall()
        output: dict[int, tuple[float, float, float, float, float]] = {}
        for timestamp, op, hi, lo, cl, volume in rows:
            dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(NY)
            if dt.date().isoformat() != day or not (time(9, 30) <= dt.time() < time(16, 0)):
                continue
            minute = _clock_minutes(dt.time())
            output[minute] = (float(op), float(hi), float(lo), float(cl), float(volume or 0.0))
        self._bars_cache[key] = output
        self._bars_cache.move_to_end(key)
        while len(self._bars_cache) > self.cache_days:
            self._bars_cache.popitem(last=False)
        return output


@dataclass(frozen=True, slots=True)
class Timing:
    entry_day: str
    entry_clock: time
    exit_day: str
    exit_clock: time
    selection_checkpoint: str
    close_entry_hypothetical: bool


def _event_signal_index(spec: PatternSpec, row: dict[str, Any]) -> int | None:
    mapping = {
        "Close above pre-3 PM high": "breakout_long_index",
        "Close below pre-3 PM low": "breakdown_short_index",
        "Failed break above pre-3 PM high": "failed_breakout_index",
        "Failed break below pre-3 PM low": "failed_breakdown_index",
    }
    key = mapping.get(spec.name)
    if key:
        value = row.get(key)
        return int(value) if value is not None else None
    bars = row["bars"]
    afternoon_high = max(bar["high"] for bar in bars[IDX_1400 + 1 : IDX_1500 + 1])
    afternoon_low = min(bar["low"] for bar in bars[IDX_1400 + 1 : IDX_1500 + 1])
    if spec.name == "Close above 2-3 PM range high":
        for index in range(IDX_1500 + 1, IDX_CLOSE):
            if bars[index]["close"] > afternoon_high:
                return index
    if spec.name == "Close below 2-3 PM range low":
        for index in range(IDX_1500 + 1, IDX_CLOSE):
            if bars[index]["close"] < afternoon_low:
                return index
    return None


def resolve_timing(
    spec: PatternSpec,
    row: dict[str, Any],
    next_row: dict[str, Any] | None,
) -> Timing | None:
    day = row["day"]
    if spec.signal_time == "after 15:00":
        signal_index = _event_signal_index(spec, row)
        if signal_index is None or signal_index + 1 > IDX_CLOSE:
            return None
        entry_dt = row["bars"][signal_index + 1]["dt_et"]
        entry_clock = entry_dt.time().replace(tzinfo=None)
        if entry_clock <= time(15, 30):
            checkpoint = "price_1500"
        elif entry_clock <= time(15, 45):
            checkpoint = "price_1530"
        else:
            checkpoint = "price_1545"
        return Timing(day, entry_clock, day, time(15, 59), checkpoint, False)

    if spec.signal_time in {"15:00", "15:30", "15:45", "15:55"}:
        entry_clock = _parse_clock(spec.signal_time)
        checkpoint = {
            "15:00": "price_1500",
            "15:30": "price_1530",
            "15:45": "price_1545",
            "15:55": "price_1545",
        }[spec.signal_time]
        return Timing(day, entry_clock, day, time(15, 59), checkpoint, False)

    if spec.signal_time != "16:00" or next_row is None:
        return None
    next_day = next_row["day"]
    if spec.holding_period == "close-next open":
        return Timing(day, time(15, 59), next_day, time(9, 30), "close", True)
    if spec.holding_period == "next open-10:30":
        return Timing(next_day, time(9, 30), next_day, time(10, 29), "close", False)
    if spec.holding_period == "next open-close":
        return Timing(next_day, time(9, 30), next_day, time(15, 59), "close", False)
    if spec.holding_period == "close-next close":
        return Timing(day, time(15, 59), next_day, time(15, 59), "close", True)
    return None


def _underlying_spot(
    session_by_day: dict[str, dict[str, Any]],
    day: str,
    clock: time,
) -> float | None:
    row = session_by_day.get(day)
    if row is None:
        return None
    if clock == time(15, 59):
        return float(row["close"])
    if clock == time(9, 30):
        return float(row["open"])
    target = _clock_minutes(clock)
    candidates = [
        bar for bar in row["bars"] if _clock_minutes(bar["dt_et"].time()) == target
    ]
    if candidates:
        return float(candidates[0]["open"])
    return None


def _find_entry_bar(
    bars: dict[int, tuple[float, float, float, float, float]],
    target: time,
    max_delay: int = 3,
) -> tuple[tuple[float, float, float, float, float] | None, int | None]:
    start = _clock_minutes(target)
    for delay in range(max_delay + 1):
        row = bars.get(start + delay)
        if row is not None:
            return row, delay
    return None, None


def _find_exit_bar(
    bars: dict[int, tuple[float, float, float, float, float]],
    target: time,
    max_delay: int = 3,
) -> tuple[tuple[float, float, float, float, float] | None, int | None]:
    start = _clock_minutes(target)
    for delay in range(max_delay + 1):
        row = bars.get(start - delay)
        if row is not None:
            return row, delay
    return None, None


def _select_single(
    contracts: Sequence[SelectedContract],
    spot: float,
    option_type: str,
    target_style: str,
) -> SelectedContract | None:
    if not contracts:
        return None
    target = spot * (
        1.0
        if target_style == "atm"
        else (1.0075 if option_type == "call" else 0.9925)
    )
    preferred = [row for row in contracts if row.target_style == target_style]
    pool = preferred or list(contracts)
    return min(pool, key=lambda row: (abs(row.strike - target), row.strike, row.symbol))


def _select_spread(
    contracts: Sequence[SelectedContract],
    spot: float,
    option_type: str,
) -> tuple[SelectedContract, SelectedContract] | None:
    long_leg = _select_single(contracts, spot, option_type, "atm")
    if long_leg is None:
        return None
    if option_type == "call":
        eligible = [
            row for row in contracts
            if row.expiration_date == long_leg.expiration_date and row.strike > long_leg.strike
        ]
        target = spot * 1.0075
    else:
        eligible = [
            row for row in contracts
            if row.expiration_date == long_leg.expiration_date and row.strike < long_leg.strike
        ]
        target = spot * 0.9925
    if not eligible:
        return None
    short_leg = min(eligible, key=lambda row: (abs(row.strike - target), abs(row.strike - long_leg.strike)))
    return long_leg, short_leg


def _intrinsic(option_type: str, strike: float, spot: float) -> float:
    return max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)


def _exit_observation(
    archive: OptionArchive,
    contract: SelectedContract,
    timing: Timing,
    session_by_day: dict[str, dict[str, Any]],
) -> tuple[float | None, float | None, int | None]:
    bars = archive.day_bars(contract.symbol, timing.exit_day)
    bar, delay = _find_exit_bar(bars, timing.exit_clock)
    if bar is not None:
        _op, high, low, _close, _volume = bar
        return high, low, delay
    if contract.expiration_date == timing.exit_day and timing.exit_clock >= time(15, 55):
        underlying = session_by_day.get(timing.exit_day)
        if underlying is not None:
            value = _intrinsic(contract.option_type, contract.strike, float(underlying["close"]))
            return value, value, 0
    return None, None, None


def simulate_outcome(
    *,
    archive: OptionArchive,
    session_by_day: dict[str, dict[str, Any]],
    decision_day: str,
    timing: Timing,
    actual_side: str,
    bucket: str,
    structure: str,
    execution: ExecutionModel,
) -> OptionOutcome:
    option_type = "call" if actual_side == "long" else "put"
    key_parts = (
        archive.symbol,
        decision_day,
        actual_side,
        bucket,
        structure,
        execution.name,
        timing.entry_day,
        timing.entry_clock.isoformat(timespec="minutes"),
        timing.exit_day,
        timing.exit_clock.isoformat(timespec="minutes"),
        timing.selection_checkpoint,
    )
    cache_key = _result_id(key_parts)
    base = OptionOutcome(
        cache_key=cache_key,
        symbol=archive.symbol,
        day=decision_day,
        actual_side=actual_side,
        option_type=option_type,
        bucket=bucket,
        structure=structure,
        execution_model=execution.name,
        entry_day=timing.entry_day,
        entry_time_et=timing.entry_clock.isoformat(timespec="minutes"),
        exit_day=timing.exit_day,
        exit_time_et=timing.exit_clock.isoformat(timespec="minutes"),
        selection_checkpoint=timing.selection_checkpoint,
        close_entry_hypothetical=timing.close_entry_hypothetical,
        long_contract=None,
        long_expiration=None,
        long_strike=None,
        short_contract=None,
        short_strike=None,
        entry_debit=None,
        exit_value=None,
        fees_dollars=None,
        risk_capital_dollars=None,
        pnl_dollars=None,
        return_on_risk_pct=None,
        entry_delay_minutes=None,
        exit_delay_minutes=None,
        status="skipped",
        skip_reason=None,
    )
    if bucket == "0dte" and timing.exit_day != decision_day:
        base.skip_reason = "0DTE contract cannot span the next session"
        return base
    spot = _underlying_spot(session_by_day, timing.entry_day, timing.entry_clock)
    if spot is None or spot <= 0:
        base.skip_reason = "missing point-in-time underlying entry spot"
        return base
    contracts = archive.contracts(
        decision_day, bucket, timing.selection_checkpoint, option_type
    )
    if not contracts:
        base.skip_reason = "no selected contracts for bucket/checkpoint/type"
        return base

    if structure == "atm_debit":
        selected = _select_single(contracts, spot, option_type, "atm")
        pair = (selected, None) if selected else None
    elif structure == "otm075_debit":
        selected = _select_single(contracts, spot, option_type, "otm075")
        pair = (selected, None) if selected else None
    else:
        pair = _select_spread(contracts, spot, option_type)
    if not pair or pair[0] is None:
        base.skip_reason = "no valid contract or spread pair"
        return base
    long_leg, short_leg = pair
    base.long_contract = long_leg.symbol
    base.long_expiration = long_leg.expiration_date
    base.long_strike = long_leg.strike
    if short_leg:
        base.short_contract = short_leg.symbol
        base.short_strike = short_leg.strike

    long_entry_bars = archive.day_bars(long_leg.symbol, timing.entry_day)
    long_entry_bar, entry_delay = _find_entry_bar(long_entry_bars, timing.entry_clock)
    if long_entry_bar is None:
        base.skip_reason = "missing long-leg entry trade bar"
        return base
    _op, long_entry_high, _low, _close, _volume = long_entry_bar
    long_buy = execution.buy(long_entry_high)

    long_exit_high, long_exit_low, exit_delay = _exit_observation(
        archive, long_leg, timing, session_by_day
    )
    if long_exit_low is None:
        base.skip_reason = "missing long-leg exit trade bar"
        return base
    long_sell = execution.sell(long_exit_low)

    if short_leg is None:
        entry_debit = long_buy
        exit_value = long_sell
        leg_count = 1
        combined_entry_delay = entry_delay
        combined_exit_delay = exit_delay
    else:
        short_entry_bars = archive.day_bars(short_leg.symbol, timing.entry_day)
        short_entry_bar, short_entry_delay = _find_entry_bar(short_entry_bars, timing.entry_clock)
        if short_entry_bar is None:
            base.skip_reason = "missing short-leg entry trade bar"
            return base
        _op, _high, short_entry_low, _close, _volume = short_entry_bar
        short_credit = execution.sell(short_entry_low)
        short_exit_high, _short_exit_low, short_exit_delay = _exit_observation(
            archive, short_leg, timing, session_by_day
        )
        if short_exit_high is None:
            base.skip_reason = "missing short-leg exit trade bar"
            return base
        short_buyback = execution.buy(short_exit_high)
        entry_debit = long_buy - short_credit
        spread_width = abs(short_leg.strike - long_leg.strike)
        if entry_debit > spread_width:
            base.skip_reason = "reconstructed spread debit exceeds maximum value"
            return base
        # A same-expiration vertical cannot have negative liquidation value or
        # exceed its strike width, even when independent minute-bar extremes do.
        exit_value = min(spread_width, max(0.0, long_sell - short_buyback))
        leg_count = 2
        combined_entry_delay = max(entry_delay or 0, short_entry_delay or 0)
        combined_exit_delay = max(exit_delay or 0, short_exit_delay or 0)

    fees = execution.fee_per_contract_side * 2.0 * leg_count
    if entry_debit <= 0:
        base.skip_reason = "non-positive reconstructed net debit"
        return base
    risk_capital = entry_debit * 100.0 + execution.fee_per_contract_side * leg_count
    pnl = (exit_value - entry_debit) * 100.0 - fees
    result_pct = pnl / risk_capital * 100.0 if risk_capital > 0 else None
    base.entry_debit = entry_debit
    base.exit_value = exit_value
    base.fees_dollars = fees
    base.risk_capital_dollars = risk_capital
    base.pnl_dollars = pnl
    base.return_on_risk_pct = result_pct
    base.entry_delay_minutes = combined_entry_delay
    base.exit_delay_minutes = combined_exit_delay
    base.status = "executed"
    return base


def _max_drawdown_dollars(pnls: Sequence[float]) -> float | None:
    if not pnls:
        return None
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _max_losing_streak(values: Sequence[float]) -> int:
    best = current = 0
    for value in values:
        if value < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def evaluate_result(
    *,
    spec: PatternSpec,
    direction_mode: str,
    actual_side: str,
    bucket: str,
    structure: str,
    execution: ExecutionModel,
    outcomes: Sequence[OptionOutcome],
) -> OptionPatternResult:
    executed = [
        row for row in outcomes
        if row.status == "executed" and row.return_on_risk_pct is not None and row.pnl_dollars is not None
    ]
    values = [float(row.return_on_risk_pct) for row in executed]
    pnls = [float(row.pnl_dollars) for row in executed]
    risks = [float(row.risk_capital_dollars) for row in executed if row.risk_capital_dollars is not None]
    recent = [row for row in executed if row.day >= RECENT_START_DAY.isoformat()]
    recent_values = [float(row.return_on_risk_pct) for row in recent]
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    profit_factor = gains / losses if losses > 0 else (math.inf if gains > 0 else None)
    array = np.asarray(values, dtype=float)
    se = hac_mean_se(array) if len(array) >= 3 else None
    mean_value = _safe_mean(values)
    t_stat = mean_value / se if mean_value is not None and se and se > 0 else None
    p_value = float(2.0 * stats.t.sf(abs(t_stat), df=max(len(values) - 1, 1))) if t_stat is not None else None
    seed = int(
        hashlib.sha256(
            f"{spec.pattern_id}|{direction_mode}|{bucket}|{structure}|{execution.name}".encode()
        ).hexdigest()[:8],
        16,
    )
    ci_low, ci_high = block_bootstrap_ci(array, seed) if len(array) >= 5 else (None, None)
    recent_mean = _safe_mean(recent_values)
    ordered_values = list(values)
    exclude_best_3 = (
        _safe_mean(sorted(values)[:-3]) if len(values) > 3 else None
    )
    midpoint = len(ordered_values) // 2
    first_half_mean = _safe_mean(ordered_values[:midpoint]) if midpoint else None
    second_half_mean = _safe_mean(ordered_values[midpoint:]) if ordered_values[midpoint:] else None
    stable_positive = (
        mean_value is not None and recent_mean is not None and mean_value > 0 and recent_mean > 0
    )
    result_id = _result_id(
        (
            spec.pattern_id,
            direction_mode,
            bucket,
            structure,
            execution.name,
        )
    )
    return OptionPatternResult(
        result_id=result_id,
        pattern_id=spec.pattern_id,
        symbol=spec.symbol,
        family=spec.family,
        name=spec.name,
        signal_time=spec.signal_time,
        holding_period=spec.holding_period,
        tested_side=spec.side,
        direction_mode=direction_mode,
        actual_side=actual_side,
        bucket=bucket,
        structure=structure,
        execution_model=execution.name,
        signal_n=len(spec.observations),
        executed_n=len(executed),
        coverage_pct=(len(executed) / len(spec.observations) * 100.0) if spec.observations else None,
        mean_return_pct=mean_value,
        median_return_pct=_safe_median(values),
        trimmed_mean_return_pct=_trimmed_mean(values),
        exclude_best_3_mean_return_pct=exclude_best_3,
        first_half_mean_return_pct=first_half_mean,
        second_half_mean_return_pct=second_half_mean,
        win_rate_pct=(sum(value > 0 for value in values) / len(values) * 100.0) if values else None,
        profit_factor=profit_factor,
        mean_pnl_dollars=_safe_mean(pnls),
        total_pnl_dollars=sum(pnls) if pnls else None,
        median_risk_capital_dollars=_safe_median(risks),
        hac_t_stat=t_stat,
        hac_p_value=p_value,
        global_fdr_q=None,
        variant_fdr_q=None,
        positive_global_fdr_q=None,
        positive_variant_fdr_q=None,
        bootstrap_ci_low_pct=ci_low,
        bootstrap_ci_high_pct=ci_high,
        recent_n=len(recent),
        recent_mean_return_pct=recent_mean,
        recent_median_return_pct=_safe_median(recent_values),
        recent_win_rate_pct=(sum(value > 0 for value in recent_values) / len(recent_values) * 100.0) if recent_values else None,
        stable_positive=stable_positive,
        best_return_pct=max(values) if values else None,
        worst_return_pct=min(values) if values else None,
        max_drawdown_one_contract_dollars=_max_drawdown_dollars(pnls),
        max_losing_streak=_max_losing_streak(values) if values else None,
        close_entry_hypothetical_count=sum(row.close_entry_hypothetical for row in executed),
    )


def _apply_bh(rows: Sequence[OptionPatternResult], attribute: str) -> None:
    eligible = [
        row for row in rows
        if row.execution_model == "base"
        and row.executed_n >= MIN_REPORT_N
        and row.hac_p_value is not None
    ]
    if not eligible:
        return
    ordered = sorted(eligible, key=lambda row: float(row.hac_p_value))
    m = len(ordered)
    running = 1.0
    values: dict[str, float] = {}
    for index in range(m - 1, -1, -1):
        row = ordered[index]
        rank = index + 1
        running = min(running, float(row.hac_p_value) * m / rank)
        values[row.result_id] = min(1.0, running)
    for row in rows:
        if row.result_id in values:
            setattr(row, attribute, values[row.result_id])


def _apply_positive_bh(rows: Sequence[OptionPatternResult], attribute: str) -> None:
    eligible: list[tuple[OptionPatternResult, float]] = []
    for row in rows:
        if (
            row.execution_model != "base"
            or row.executed_n < MIN_REPORT_N
            or row.mean_return_pct is None
            or row.mean_return_pct <= 0
            or row.hac_t_stat is None
            or row.hac_t_stat <= 0
        ):
            continue
        one_sided = float(stats.t.sf(row.hac_t_stat, df=max(row.executed_n - 1, 1)))
        eligible.append((row, one_sided))
    if not eligible:
        return
    ordered = sorted(eligible, key=lambda item: item[1])
    m = len(ordered)
    running = 1.0
    q_by_id: dict[str, float] = {}
    for index in range(m - 1, -1, -1):
        row, p_value = ordered[index]
        rank = index + 1
        running = min(running, p_value * m / rank)
        q_by_id[row.result_id] = min(1.0, running)
    for row in rows:
        if row.result_id in q_by_id:
            setattr(row, attribute, q_by_id[row.result_id])


def apply_fdr(results: list[OptionPatternResult]) -> None:
    base = [row for row in results if row.execution_model == "base"]
    _apply_bh(base, "global_fdr_q")
    _apply_positive_bh(base, "positive_global_fdr_q")
    groups: dict[tuple[str, str, str, str], list[OptionPatternResult]] = defaultdict(list)
    for row in base:
        groups[(row.symbol, row.direction_mode, row.bucket, row.structure)].append(row)
    for rows in groups.values():
        _apply_bh(rows, "variant_fdr_q")
        _apply_positive_bh(rows, "positive_variant_fdr_q")


def _write_csv(path: Path, rows: Sequence[Any]) -> None:
    payload = [asdict(row) for row in rows]
    fields = list(payload[0].keys()) if payload else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(payload)


def _aggregate_variant(results: Sequence[OptionPatternResult]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[OptionPatternResult]] = defaultdict(list)
    for row in results:
        if row.execution_model != "base":
            continue
        groups[(row.bucket, row.structure, row.direction_mode)].append(row)
    output: list[dict[str, Any]] = []
    for (bucket, structure, direction_mode), rows in sorted(groups.items()):
        viable = [
            row for row in rows
            if row.executed_n >= MIN_REPORT_N
            and row.coverage_pct is not None
            and row.coverage_pct >= 60
            and row.mean_return_pct is not None
        ]
        output.append(
            {
                "bucket": bucket,
                "structure": structure,
                "direction_mode": direction_mode,
                "tested_patterns": len(rows),
                "viable_patterns": len(viable),
                "positive_mean_patterns": sum(float(row.mean_return_pct) > 0 for row in viable),
                "positive_median_patterns": sum((row.median_return_pct or 0) > 0 for row in viable),
                "stable_positive_patterns": sum(row.stable_positive is True for row in viable),
                "positive_variant_fdr_10pct_discoveries": sum((row.positive_variant_fdr_q or 1) <= 0.10 for row in viable),
                "any_effect_variant_fdr_10pct": sum((row.variant_fdr_q or 1) <= 0.10 for row in viable),
                "median_pattern_mean_return_pct": _safe_median([float(row.mean_return_pct) for row in viable]),
            }
        )
    return output


def _robust_rows(results: Sequence[OptionPatternResult]) -> list[OptionPatternResult]:
    worse_lookup = {
        (row.pattern_id, row.direction_mode, row.bucket, row.structure): row
        for row in results
        if row.execution_model == "worse"
    }
    output: list[OptionPatternResult] = []
    for row in results:
        if row.execution_model != "base":
            continue
        worse = worse_lookup.get((row.pattern_id, row.direction_mode, row.bucket, row.structure))
        if (
            row.executed_n >= 12
            and (row.coverage_pct or 0) >= 60
            and row.close_entry_hypothetical_count == 0
            and row.mean_return_pct is not None
            and row.median_return_pct is not None
            and row.recent_mean_return_pct is not None
            and row.mean_return_pct > 0
            and row.median_return_pct > 0
            and row.recent_mean_return_pct > 0
            and row.exclude_best_3_mean_return_pct is not None
            and row.exclude_best_3_mean_return_pct > 0
            and row.first_half_mean_return_pct is not None
            and row.first_half_mean_return_pct > 0
            and row.second_half_mean_return_pct is not None
            and row.second_half_mean_return_pct > 0
            and worse is not None
            and (worse.mean_return_pct or -math.inf) > 0
            and (worse.recent_mean_return_pct or -math.inf) > 0
        ):
            output.append(row)
    output.sort(
        key=lambda row: (
            row.positive_variant_fdr_q if row.positive_variant_fdr_q is not None else 1.0,
            -(row.trimmed_mean_return_pct or -math.inf),
            -row.executed_n,
        )
    )
    return output


def build_signal_overlap(
    rows: Sequence[OptionPatternResult], specs: Sequence[PatternSpec]
) -> list[dict[str, Any]]:
    spec_by_id = {row.pattern_id: row for row in specs}
    unique: dict[str, OptionPatternResult] = {}
    for row in rows:
        unique[row.pattern_id] = row
    ordered = list(unique.values())
    output: list[dict[str, Any]] = []
    for left_index, left in enumerate(ordered):
        left_spec = spec_by_id.get(left.pattern_id)
        if left_spec is None:
            continue
        left_days = {row.day for row in left_spec.observations}
        for right in ordered[left_index + 1 :]:
            if left.symbol != right.symbol:
                continue
            right_spec = spec_by_id.get(right.pattern_id)
            if right_spec is None:
                continue
            right_days = {row.day for row in right_spec.observations}
            intersection = left_days & right_days
            union = left_days | right_days
            output.append(
                {
                    "symbol": left.symbol,
                    "left_pattern": left.name,
                    "right_pattern": right.name,
                    "left_n": len(left_days),
                    "right_n": len(right_days),
                    "overlap_n": len(intersection),
                    "overlap_of_smaller_pct": (
                        len(intersection) / min(len(left_days), len(right_days)) * 100.0
                        if left_days and right_days
                        else None
                    ),
                    "jaccard_pct": len(intersection) / len(union) * 100.0 if union else None,
                }
            )
    return output


def build_report(
    *,
    sessions: dict[str, list[dict[str, Any]]],
    results: Sequence[OptionPatternResult],
    outcomes: Sequence[OptionOutcome],
    variant_summary: Sequence[dict[str, Any]],
    positive_fdr_overlap: Sequence[dict[str, Any]],
    output_dir: Path,
) -> str:
    robust = _robust_rows(results)
    fdr_rows = [
        row for row in results
        if row.execution_model == "base"
        and row.executed_n >= MIN_REPORT_N
        and (row.positive_variant_fdr_q or 1) <= 0.10
    ]
    fdr_rows.sort(key=lambda row: (row.positive_variant_fdr_q or 1, -(row.mean_return_pct or -math.inf)))
    lines = [
        "# SPY / QQQ / IWM EOD Options Pattern Lab",
        "",
        f"**Generated:** {datetime.now(UTC).isoformat().replace('+00:00','Z')}",
        "",
        "## Scope",
        "",
        f"All {len({row.pattern_id for row in results})} underlying EOD patterns were evaluated in both directions, across four DTE buckets, three defined-risk structures, and three conservative execution models.",
        "",
        "| ETF | Complete sessions | Option selection mappings |",
        "|---|---:|---:|",
    ]
    for symbol in SYMBOLS:
        mapping_count = sum(
            1 for row in outcomes if row.symbol == symbol
        )
        lines.append(f"| {symbol} | {len(sessions[symbol])} | {mapping_count:,} cached outcome evaluations |")
    lines += [
        "",
        "Historical option NBBO is absent. Every result is a one-minute OPRA trade-bar execution approximation, not an executable quote backtest.",
        "",
        "## Contract variants",
        "",
        "- `0dte`: same-day expiration; intraday patterns only.",
        "- `front`: approximately 1–3 calendar DTE.",
        "- `weekly`: established Friday expiration approximately 3–10 calendar DTE.",
        "- `swing`: established Friday expiration approximately 10–23 calendar DTE.",
        "- Structures: ATM call/put debit, 0.75% OTM debit, and ATM-to-0.75%-OTM debit spread.",
        "",
        "## Variant-wide diagnostic",
        "",
        "| Bucket | Structure | Direction | Viable patterns | Positive mean | Positive median | Stable positive | Positive-FDR discoveries | Any-effect FDR | Median pattern mean |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|", 
    ]
    for row in variant_summary:
        lines.append(
            f"| {row['bucket']} | {row['structure']} | {row['direction_mode']} | {row['viable_patterns']} | "
            f"{row['positive_mean_patterns']} | {row['positive_median_patterns']} | {row['stable_positive_patterns']} | "
            f"{row['positive_variant_fdr_10pct_discoveries']} | {row['any_effect_variant_fdr_10pct']} | "
            f"{row['median_pattern_mean_return_pct'] if row['median_pattern_mean_return_pct'] is not None else 'n/a'} |"
        )
    lines += [
        "",
        "## Positive variant-level FDR discoveries",
        "",
        "| ETF | Pattern | Direction | Bucket | Structure | N | Mean | Median | Ex-best-3 | Wins | Recent mean | Variant q |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|", 
    ]
    for row in fdr_rows[:100]:
        lines.append(
            f"| {row.symbol} | {row.name} | {row.actual_side} ({row.direction_mode}) | {row.bucket} | {row.structure} | "
            f"{row.executed_n} | {row.mean_return_pct:.2f}% | {row.median_return_pct:.2f}% | "
            f"{row.exclude_best_3_mean_return_pct if row.exclude_best_3_mean_return_pct is not None else float('nan'):.2f}% | "
            f"{row.win_rate_pct:.1f}% | {row.recent_mean_return_pct if row.recent_mean_return_pct is not None else float('nan'):.2f}% | {row.positive_variant_fdr_q:.3f} |"
        )
    if not fdr_rows:
        lines.append("| — | No options pattern survived 10% within-variant positive FDR | — | — | — | — | — | — | — | — | — | — |")
    lines += [
        "",
        "## Positive-discovery overlap",
        "",
        "| ETF | Pattern A | Pattern B | A N | B N | Shared days | Smaller-set overlap | Jaccard |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in positive_fdr_overlap:
        lines.append(
            f"| {row['symbol']} | {row['left_pattern']} | {row['right_pattern']} | {row['left_n']} | "
            f"{row['right_n']} | {row['overlap_n']} | {row['overlap_of_smaller_pct']:.1f}% | {row['jaccard_pct']:.1f}% |"
        )
    if not positive_fdr_overlap:
        lines.append("| — | Fewer than two distinct positive discoveries | — | — | — | — | — | — |")
    lines += [
        "",
        "## Robust positive candidates",
        "",
        "These require at least 12 executed trades, at least 60% data coverage, positive mean/median/recent results, positive first- and second-half means, a positive mean after removing the three best trades, positive base and recent performance under the worse execution model, and no close-entry lookahead assumption.",
        "",
        "| ETF | Pattern | Side | Bucket | Structure | N | Mean | Median | Ex-best-3 | Win rate | Recent mean | Variant q |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|", 
    ]
    for row in robust[:75]:
        lines.append(
            f"| {row.symbol} | {row.name} | {row.actual_side} | {row.bucket} | {row.structure} | {row.executed_n} | "
            f"{row.mean_return_pct:.2f}% | {row.median_return_pct:.2f}% | "
            f"{row.exclude_best_3_mean_return_pct if row.exclude_best_3_mean_return_pct is not None else float('nan'):.2f}% | "
            f"{row.win_rate_pct:.1f}% | {row.recent_mean_return_pct:.2f}% | {row.positive_variant_fdr_q if row.positive_variant_fdr_q is not None else float('nan'):.3f} |"
        )
    if not robust:
        lines.append("| — | No candidate met every robustness gate | — | — | — | — | — | — | — | — | — |")
    lines += [
        "",
        "## Interpretation constraints",
        "",
        "- The same 125-session regime was used for discovery; these are not independent out-of-sample results.",
        "- The options archive is survivorship-safe at the contract-symbol level but lacks historical NBBO, Greeks, IV, rates, and historical open interest.",
        "- Long entries deliberately use minute highs and exits minute lows plus explicit slippage; this is conservative but not a substitute for quotes.",
        "- Close-state patterns that require entering at the same close are retained but marked hypothetical and excluded from the robust list.",
        "- A large return on a low-premium option can be statistically unstable; median, trimmed mean, coverage, and worse-cost results matter more than the single best trade.",
        "",
        "## Output files",
        "",
        f"- `{output_dir / 'all_option_pattern_results.csv'}` — every pattern/side/bucket/structure/execution result.",
        f"- `{output_dir / 'daily_option_outcomes.csv'}` — de-duplicated contract-level simulations used by the pattern aggregations.",
        f"- `{output_dir / 'variant_summary.csv'}` — broad comparison of DTE and structure families.",
        f"- `{output_dir / 'report.json'}` — machine-readable summary and top rankings.",
    ]
    return "\n".join(lines) + "\n"


def run_lab(args: argparse.Namespace) -> dict[str, Any]:
    equity_db = Path(args.equity_db or DEFAULT_EQUITY_DB).resolve()
    archive_root = Path(args.archive_root or DEFAULT_ARCHIVE_ROOT).resolve()
    output_dir = Path(args.output_dir or DEFAULT_OUT).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    sessions = load_sessions(equity_db)
    sessions = {
        symbol: [
            row for row in rows
            if ANALYSIS_START_DAY <= date.fromisoformat(row["day"]) <= ANALYSIS_END_DAY
        ]
        for symbol, rows in sessions.items()
    }
    session_maps = {
        symbol: {row["day"]: row for row in rows}
        for symbol, rows in sessions.items()
    }
    specs = build_patterns(sessions)
    archives = {
        symbol: OptionArchive(
            symbol,
            archive_root / symbol.lower() / "historical_options.sqlite",
            cache_days=args.cache_days,
        )
        for symbol in SYMBOLS
    }
    outcome_cache: dict[str, OptionOutcome] = {}
    results: list[OptionPatternResult] = []
    execution_by_name = {row.name: row for row in EXECUTION_MODELS}
    try:
        for spec_index, spec in enumerate(specs, start=1):
            archive = archives[spec.symbol]
            session_by_day = session_maps[spec.symbol]
            ordered_days = [row["day"] for row in sessions[spec.symbol]]
            next_by_day = {
                day: session_by_day.get(ordered_days[index + 1]) if index + 1 < len(ordered_days) else None
                for index, day in enumerate(ordered_days)
            }
            observation_days = [row.day for row in spec.observations]
            for direction_mode in DIRECTION_MODES:
                actual_side = spec.side if direction_mode == "as_tested" else _flip(spec.side)
                for bucket in BUCKETS:
                    for structure in STRUCTURES:
                        outcomes_by_execution: dict[str, list[OptionOutcome]] = {
                            model.name: [] for model in EXECUTION_MODELS
                        }
                        for day in observation_days:
                            session = session_by_day.get(day)
                            if session is None:
                                continue
                            timing = resolve_timing(spec, session, next_by_day.get(day))
                            if timing is None:
                                continue
                            for execution in EXECUTION_MODELS:
                                key = _result_id(
                                    (
                                        spec.symbol,
                                        day,
                                        actual_side,
                                        bucket,
                                        structure,
                                        execution.name,
                                        timing.entry_day,
                                        timing.entry_clock.isoformat(timespec="minutes"),
                                        timing.exit_day,
                                        timing.exit_clock.isoformat(timespec="minutes"),
                                        timing.selection_checkpoint,
                                    )
                                )
                                outcome = outcome_cache.get(key)
                                if outcome is None:
                                    outcome = simulate_outcome(
                                        archive=archive,
                                        session_by_day=session_by_day,
                                        decision_day=day,
                                        timing=timing,
                                        actual_side=actual_side,
                                        bucket=bucket,
                                        structure=structure,
                                        execution=execution,
                                    )
                                    outcome_cache[key] = outcome
                                outcomes_by_execution[execution.name].append(outcome)
                        for execution_name, outcomes in outcomes_by_execution.items():
                            results.append(
                                evaluate_result(
                                    spec=spec,
                                    direction_mode=direction_mode,
                                    actual_side=actual_side,
                                    bucket=bucket,
                                    structure=structure,
                                    execution=execution_by_name[execution_name],
                                    outcomes=outcomes,
                                )
                            )
            if args.progress and (spec_index == 1 or spec_index % 25 == 0 or spec_index == len(specs)):
                print(
                    json.dumps(
                        {
                            "progress": f"{spec_index}/{len(specs)}",
                            "pattern": spec.name,
                            "cached_outcomes": len(outcome_cache),
                            "results": len(results),
                        },
                        sort_keys=True,
                    )
                )
        apply_fdr(results)
        outcomes = sorted(
            outcome_cache.values(),
            key=lambda row: (
                row.symbol,
                row.day,
                row.actual_side,
                row.bucket,
                row.structure,
                row.execution_model,
                row.entry_time_et,
            ),
        )
        variant_summary = _aggregate_variant(results)
        robust = _robust_rows(results)
        fdr_rows = [
            row for row in results
            if row.execution_model == "base"
            and row.executed_n >= MIN_REPORT_N
            and (row.positive_variant_fdr_q or 1) <= 0.10
        ]
        fdr_rows.sort(key=lambda row: (row.positive_variant_fdr_q or 1, -(row.mean_return_pct or -math.inf)))

        _write_csv(output_dir / "all_option_pattern_results.csv", results)
        _write_csv(output_dir / "daily_option_outcomes.csv", outcomes)
        fields = list(variant_summary[0].keys()) if variant_summary else []
        with (output_dir / "variant_summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if fields:
                writer.writeheader()
                writer.writerows(variant_summary)
        positive_fdr_overlap = build_signal_overlap(fdr_rows, specs)
        report_md = build_report(
            sessions=sessions,
            results=results,
            outcomes=outcomes,
            variant_summary=variant_summary,
            positive_fdr_overlap=positive_fdr_overlap,
            output_dir=output_dir,
        )
        (output_dir / "report.md").write_text(report_md, encoding="utf-8")
        summary = {
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "equity_db": str(equity_db),
            "archive_root": str(archive_root),
            "output_dir": str(output_dir),
            "patterns": len(specs),
            "result_rows": len(results),
            "cached_outcomes": len(outcomes),
            "executed_outcomes": sum(row.status == "executed" for row in outcomes),
            "skipped_outcomes": sum(row.status != "executed" for row in outcomes),
            "positive_variant_fdr_10pct_discoveries": len(fdr_rows),
            "positive_global_fdr_10pct_discoveries": sum(
                row.execution_model == "base"
                and row.executed_n >= MIN_REPORT_N
                and (row.positive_global_fdr_q or 1) <= 0.10
                for row in results
            ),
            "any_effect_variant_fdr_10pct_discoveries": sum(
                row.execution_model == "base"
                and row.executed_n >= MIN_REPORT_N
                and (row.variant_fdr_q or 1) <= 0.10
                for row in results
            ),
            "any_effect_global_fdr_10pct_discoveries": sum(
                row.execution_model == "base"
                and row.executed_n >= MIN_REPORT_N
                and (row.global_fdr_q or 1) <= 0.10
                for row in results
            ),
            "robust_positive_candidates": len(robust),
            "sessions": {symbol: len(rows) for symbol, rows in sessions.items()},
            "variant_summary": variant_summary,
            "top_fdr": [asdict(row) for row in fdr_rows[:100]],
            "positive_fdr_overlap": positive_fdr_overlap,
            "top_robust": [asdict(row) for row in robust[:100]],
            "research_grade": False,
            "research_grade_reason": "Historical option NBBO, IV, and Greeks are absent.",
            "files": {
                "results_csv": str(output_dir / "all_option_pattern_results.csv"),
                "outcomes_csv": str(output_dir / "daily_option_outcomes.csv"),
                "variant_summary_csv": str(output_dir / "variant_summary.csv"),
                "report_markdown": str(output_dir / "report.md"),
                "report_json": str(output_dir / "report.json"),
            },
        }
        (output_dir / "report.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        return summary
    finally:
        for archive in archives.values():
            archive.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Translate every SPY/QQQ/IWM EOD pattern into conservative historical option outcomes."
    )
    parser.add_argument("--equity-db")
    parser.add_argument("--archive-root")
    parser.add_argument("--output-dir")
    parser.add_argument("--cache-days", type=int, default=3000)
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_lab(args)
    compact_keys = (
        "generated_at",
        "patterns",
        "result_rows",
        "cached_outcomes",
        "executed_outcomes",
        "skipped_outcomes",
        "positive_variant_fdr_10pct_discoveries",
        "positive_global_fdr_10pct_discoveries",
        "any_effect_variant_fdr_10pct_discoveries",
        "robust_positive_candidates",
        "files",
    )
    print(json.dumps({key: summary.get(key) for key in compact_keys}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
