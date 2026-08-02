"""Defined-risk earnings options backtest for MSFT, META, AAPL, and AMZN.

The lab evaluates only non-butterfly structures over a frozen list of the last
six completed earnings reports per symbol:

* post-earnings first-hour acceptance debit spreads;
* post-earnings gap-hold debit spreads;
* pre-earnings five-session momentum debit spreads;
* immediate-expiry implied-move iron condors.

Historical NBBO is unavailable. Each leg is therefore reconstructed from
one-minute OPRA trade bars with conservative, leg-by-leg execution assumptions.
No orders or broker trading endpoints are present.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo


CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
DEFAULT_EVENTS = ROOT / "data" / "earnings_defined_risk_lab" / "events.csv"
DEFAULT_NEXT_WEEK_DB = (
    ROOT / "data" / "historical_options" / "earnings_defined_risk_next_week" / "historical_options.sqlite"
)
DEFAULT_AAPL_CONDOR_DB = (
    ROOT / "data" / "historical_options" / "earnings_defined_risk_aapl_condor" / "historical_options.sqlite"
)
DEFAULT_OTHER_CONDOR_DB = (
    ROOT / "data" / "historical_options" / "earnings_defined_risk_other_condors" / "historical_options.sqlite"
)
DEFAULT_OUTPUT = ROOT / "data" / "earnings_defined_risk_lab" / "results"


@dataclass(frozen=True, slots=True)
class EarningsEvent:
    symbol: str
    report_date: str
    next_trading_day: str


@dataclass(frozen=True, slots=True)
class Contract:
    symbol: str
    underlying: str
    expiration_date: str
    strike: float
    option_type: str


@dataclass(frozen=True, slots=True)
class ExecutionModel:
    name: str
    pct: float
    floor: float
    fee_per_leg: float

    def buy(self, observed_high: float) -> float:
        return max(0.0, observed_high + max(self.floor, observed_high * self.pct))

    def sell(self, observed_low: float) -> float:
        return max(0.0, observed_low - max(self.floor, observed_low * self.pct))


EXECUTION_MODELS = (
    ExecutionModel("base", 0.03, 0.03, 0.75),
    ExecutionModel("worse", 0.05, 0.05, 0.75),
    ExecutionModel("severe", 0.10, 0.10, 1.00),
)


@dataclass(slots=True)
class TradeRow:
    strategy: str
    symbol: str
    report_date: str
    post_day: str
    execution_model: str
    direction: str
    status: str
    skip_reason: str | None = None
    signal_time_et: str | None = None
    entry_day: str | None = None
    entry_time_et: str | None = None
    exit_day: str | None = None
    exit_time_et: str | None = None
    exit_reason: str | None = None
    expiration_date: str | None = None
    underlying_entry_spot: float | None = None
    prior_close: float | None = None
    gap_pct: float | None = None
    momentum_5d_pct: float | None = None
    long_contract: str | None = None
    long_strike: float | None = None
    short_contract: str | None = None
    short_strike: float | None = None
    long_put_contract: str | None = None
    long_put_strike: float | None = None
    short_put_contract: str | None = None
    short_put_strike: float | None = None
    short_call_contract: str | None = None
    short_call_strike: float | None = None
    long_call_contract: str | None = None
    long_call_strike: float | None = None
    implied_move_proxy: float | None = None
    entry_value: float | None = None
    exit_value: float | None = None
    max_risk_dollars: float | None = None
    fees_dollars: float | None = None
    pnl_dollars: float | None = None
    return_on_risk_pct: float | None = None


@dataclass(slots=True)
class VariantResult:
    strategy: str
    execution_model: str
    scope: str
    event_n: int
    executed_n: int
    coverage_pct: float
    mean_return_pct: float | None
    median_return_pct: float | None
    win_rate_pct: float | None
    total_pnl_dollars: float | None
    mean_pnl_dollars: float | None
    profit_factor: float | None
    exclude_best_1_mean_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None
    max_drawdown_dollars: float | None
    robust_positive: bool


def clock_minute(value: time) -> int:
    return value.hour * 60 + value.minute


def minute_clock(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def parse_events(path: Path) -> list[EarningsEvent]:
    rows: list[EarningsEvent] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                EarningsEvent(
                    symbol=row["symbol"].strip().upper(),
                    report_date=row["report_date"].strip(),
                    next_trading_day=row["next_trading_day"].strip(),
                )
            )
    return rows


class Archive:
    def __init__(self, db_paths: Sequence[Path]) -> None:
        self.dbs: list[sqlite3.Connection] = []
        self.contract_db: dict[str, int] = {}
        self.selection_map: dict[tuple[str, str, str], list[Contract]] = defaultdict(list)
        self._option_cache: dict[tuple[str, str], dict[int, tuple[float, float, float, float, float]]] = {}
        self._underlying_cache: dict[tuple[str, str], dict[int, tuple[float, float, float, float, float]]] = {}
        self._daily_cache: dict[str, list[tuple[str, float]]] = {}
        self._session_cache: dict[str, list[str]] = {}
        for path in db_paths:
            if not path.exists():
                raise FileNotFoundError(path)
            db = sqlite3.connect(path)
            db.execute("pragma query_only=on")
            index = len(self.dbs)
            self.dbs.append(db)
            for symbol, underlying, expiry, strike, option_type in db.execute(
                "select symbol,underlying,expiration_date,strike,option_type from contracts"
            ):
                self.contract_db.setdefault(str(symbol), index)
            rows = db.execute(
                """select s.decision_date,c.symbol,c.underlying,c.expiration_date,
                          c.strike,c.option_type
                   from decision_selections s join contracts c on c.symbol=s.symbol
                   order by s.decision_date,c.underlying,c.option_type,c.expiration_date,c.strike"""
            ).fetchall()
            for decision_day, symbol, underlying, expiry, strike, option_type in rows:
                key = (str(decision_day), str(underlying), str(option_type))
                self.selection_map[key].append(
                    Contract(str(symbol), str(underlying), str(expiry), float(strike), str(option_type))
                )
        for key, values in list(self.selection_map.items()):
            unique = {row.symbol: row for row in values}
            self.selection_map[key] = sorted(
                unique.values(), key=lambda row: (row.expiration_date, row.strike, row.symbol)
            )

    def close(self) -> None:
        for db in self.dbs:
            db.close()

    def contracts(self, decision_day: str, underlying: str, option_type: str) -> list[Contract]:
        return list(self.selection_map.get((decision_day, underlying, option_type), []))

    @staticmethod
    def _window(day: str) -> tuple[str, str]:
        start = datetime.combine(date.fromisoformat(day), time(0, 0), tzinfo=NY).astimezone(UTC)
        end = (start.astimezone(NY) + timedelta(days=1)).astimezone(UTC)
        return (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )

    def option_bars(self, contract: str, day: str) -> dict[int, tuple[float, float, float, float, float]]:
        key = (contract, day)
        if key in self._option_cache:
            return self._option_cache[key]
        index = self.contract_db.get(contract)
        if index is None:
            self._option_cache[key] = {}
            return {}
        start, end = self._window(day)
        rows = self.dbs[index].execute(
            """select timestamp,open,high,low,close,volume from option_bars
               where symbol=? and timestamp>=? and timestamp<? order by timestamp""",
            (contract, start, end),
        ).fetchall()
        output: dict[int, tuple[float, float, float, float, float]] = {}
        for timestamp, op, hi, lo, cl, volume in rows:
            dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(NY)
            if dt.date().isoformat() != day or not (time(9, 30) <= dt.time() < time(16, 0)):
                continue
            output[clock_minute(dt.time())] = (
                float(op), float(hi), float(lo), float(cl), float(volume or 0.0)
            )
        self._option_cache[key] = output
        return output

    def underlying_bars(self, symbol: str, day: str) -> dict[int, tuple[float, float, float, float, float]]:
        key = (symbol, day)
        if key in self._underlying_cache:
            return self._underlying_cache[key]
        start, end = self._window(day)
        output: dict[int, tuple[float, float, float, float, float]] = {}
        for db in self.dbs:
            rows = db.execute(
                """select timestamp,open,high,low,close,volume from underlying_bars
                   where symbol=? and timeframe='1Min' and timestamp>=? and timestamp<?
                   order by timestamp""",
                (symbol, start, end),
            ).fetchall()
            if not rows:
                continue
            for timestamp, op, hi, lo, cl, volume in rows:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(NY)
                if dt.date().isoformat() != day or not (time(9, 30) <= dt.time() < time(16, 0)):
                    continue
                output[clock_minute(dt.time())] = (
                    float(op), float(hi), float(lo), float(cl), float(volume or 0.0)
                )
            if output:
                break
        self._underlying_cache[key] = output
        return output

    def daily_closes(self, symbol: str) -> list[tuple[str, float]]:
        if symbol in self._daily_cache:
            return self._daily_cache[symbol]
        values: dict[str, float] = {}
        for db in self.dbs:
            rows = db.execute(
                """select timestamp,close from underlying_bars
                   where symbol=? and timeframe='1Day' order by timestamp""",
                (symbol,),
            ).fetchall()
            for timestamp, close in rows:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(NY)
                values[dt.date().isoformat()] = float(close)
        result = sorted(values.items())
        self._daily_cache[symbol] = result
        return result

    def session_days(self, symbol: str) -> list[str]:
        if symbol in self._session_cache:
            return self._session_cache[symbol]
        days: set[str] = set()
        for db in self.dbs:
            for (timestamp,) in db.execute(
                "select distinct timestamp from underlying_bars where symbol=? and timeframe='1Min'",
                (symbol,),
            ):
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(NY)
                days.add(dt.date().isoformat())
        result = sorted(days)
        self._session_cache[symbol] = result
        return result


def find_bar(
    bars: dict[int, tuple[float, float, float, float, float]],
    target: int,
    *,
    forward: bool,
    max_delay: int = 3,
) -> tuple[tuple[float, float, float, float, float] | None, int | None]:
    for delay in range(max_delay + 1):
        minute = target + delay if forward else target - delay
        if minute in bars:
            return bars[minute], minute
    return None, None


def prior_close(archive: Archive, symbol: str, day: str) -> float | None:
    values = [value for value_day, value in archive.daily_closes(symbol) if value_day == day]
    return values[-1] if values else None


def momentum_5d(archive: Archive, symbol: str, report_day: str) -> float | None:
    values = [value for day, value in archive.daily_closes(symbol) if day < report_day]
    if len(values) < 5 or values[-5] <= 0:
        return None
    return (values[-1] / values[-5] - 1.0) * 100.0


def next_session(archive: Archive, symbol: str, day: str) -> str | None:
    return next((value for value in archive.session_days(symbol) if value > day), None)


def underlying_spot(archive: Archive, symbol: str, day: str, minute: int) -> float | None:
    bar, _actual = find_bar(archive.underlying_bars(symbol, day), minute, forward=True)
    return float(bar[0]) if bar else None


def choose_expiration(contracts: Sequence[Contract], report_day: str, target_dte: int) -> str | None:
    expiries = sorted({row.expiration_date for row in contracts})
    if not expiries:
        return None
    report = date.fromisoformat(report_day)
    return min(expiries, key=lambda expiry: (abs((date.fromisoformat(expiry) - report).days - target_dte), expiry))


def select_debit_pair(
    contracts: Sequence[Contract],
    spot: float,
    direction: str,
    report_day: str,
) -> tuple[Contract, Contract] | None:
    option_type = "call" if direction == "bullish" else "put"
    pool = [row for row in contracts if row.option_type == option_type]
    expiry = choose_expiration(pool, report_day, 8)
    pool = [row for row in pool if row.expiration_date == expiry]
    if not pool:
        return None
    long_leg = min(pool, key=lambda row: (abs(row.strike - spot), row.strike))
    if direction == "bullish":
        eligible = [row for row in pool if row.strike > long_leg.strike]
        target = spot * 1.025
    else:
        eligible = [row for row in pool if row.strike < long_leg.strike]
        target = spot * 0.975
    if not eligible:
        return None
    short_leg = min(eligible, key=lambda row: (abs(row.strike - target), abs(row.strike - long_leg.strike)))
    return long_leg, short_leg


def option_entry_prices(
    archive: Archive,
    long_leg: Contract,
    short_leg: Contract,
    day: str,
    minute: int,
    execution: ExecutionModel,
) -> tuple[float, float, int] | None:
    long_bar, long_minute = find_bar(archive.option_bars(long_leg.symbol, day), minute, forward=True)
    short_bar, short_minute = find_bar(archive.option_bars(short_leg.symbol, day), minute, forward=True)
    if long_bar is None or short_bar is None or long_minute is None or short_minute is None:
        return None
    actual_minute = max(long_minute, short_minute)
    long_bar = archive.option_bars(long_leg.symbol, day).get(actual_minute, long_bar)
    short_bar = archive.option_bars(short_leg.symbol, day).get(actual_minute, short_bar)
    long_buy = execution.buy(long_bar[1])
    short_sell = execution.sell(short_bar[2])
    return long_buy, short_sell, actual_minute


def debit_liquidation_value(
    archive: Archive,
    long_leg: Contract,
    short_leg: Contract,
    day: str,
    minute: int,
    execution: ExecutionModel,
    *,
    backward: bool,
) -> tuple[float, int] | None:
    long_bar, long_minute = find_bar(
        archive.option_bars(long_leg.symbol, day), minute, forward=not backward
    )
    short_bar, short_minute = find_bar(
        archive.option_bars(short_leg.symbol, day), minute, forward=not backward
    )
    if long_bar is None or short_bar is None or long_minute is None or short_minute is None:
        return None
    actual_minute = min(long_minute, short_minute) if backward else max(long_minute, short_minute)
    long_bar = archive.option_bars(long_leg.symbol, day).get(actual_minute, long_bar)
    short_bar = archive.option_bars(short_leg.symbol, day).get(actual_minute, short_bar)
    width = abs(short_leg.strike - long_leg.strike)
    value = execution.sell(long_bar[2]) - execution.buy(short_bar[1])
    return max(0.0, min(width, value)), actual_minute


def spread_intrinsic(direction: str, long_strike: float, short_strike: float, spot: float) -> float:
    if direction == "bullish":
        return max(0.0, spot - long_strike) - max(0.0, spot - short_strike)
    return max(0.0, long_strike - spot) - max(0.0, short_strike - spot)


def debit_trade(
    *,
    strategy: str,
    event: EarningsEvent,
    archive: Archive,
    execution: ExecutionModel,
    direction: str,
    signal_time: int,
    entry_day: str,
    entry_minute: int,
    exit_policy: str,
    momentum: float | None = None,
    gap_pct: float | None = None,
    fixed_exit_day: str | None = None,
    fixed_exit_minute: int | None = None,
    fixed_exit_backward: bool = False,
) -> TradeRow:
    base = TradeRow(
        strategy, event.symbol, event.report_date, event.next_trading_day,
        execution.name, direction, "skipped", None, minute_clock(signal_time),
        entry_day, minute_clock(entry_minute), None, None, None, None, None,
        prior_close(archive, event.symbol, event.report_date), gap_pct, momentum,
        None, None, None, None, None, None, None, None, None, None, None, None,
        None, None, None, None, None, None,
    )
    spot = underlying_spot(archive, event.symbol, entry_day, entry_minute)
    base.underlying_entry_spot = spot
    if spot is None:
        base.skip_reason = "missing underlying entry bar"
        return base
    option_type = "call" if direction == "bullish" else "put"
    contracts = archive.contracts(event.report_date, event.symbol, option_type)
    pair = select_debit_pair(contracts, spot, direction, event.report_date)
    if pair is None:
        base.skip_reason = "no valid next-week debit-spread pair"
        return base
    long_leg, short_leg = pair
    base.expiration_date = long_leg.expiration_date
    base.long_contract = long_leg.symbol
    base.long_strike = long_leg.strike
    base.short_contract = short_leg.symbol
    base.short_strike = short_leg.strike
    entry = option_entry_prices(archive, long_leg, short_leg, entry_day, entry_minute, execution)
    if entry is None:
        base.skip_reason = "missing option entry bar"
        return base
    long_buy, short_sell, actual_entry = entry
    debit = long_buy - short_sell
    width = abs(short_leg.strike - long_leg.strike)
    if debit <= 0 or debit >= width:
        base.skip_reason = "non-economic reconstructed debit"
        return base
    base.entry_time_et = minute_clock(actual_entry)
    base.entry_value = debit
    entry_fees = 2.0 * execution.fee_per_leg
    total_fees = 4.0 * execution.fee_per_leg
    risk = debit * 100.0 + entry_fees

    exit_value: float | None = None
    exit_day: str | None = None
    exit_minute: int | None = None
    exit_reason: str | None = None
    if fixed_exit_day is not None and fixed_exit_minute is not None:
        exit_day = fixed_exit_day
        observation = debit_liquidation_value(
            archive,
            long_leg,
            short_leg,
            exit_day,
            fixed_exit_minute,
            execution,
            backward=fixed_exit_backward,
        )
        if observation:
            exit_value, exit_minute = observation
            exit_reason = "fixed_exit"
    elif exit_policy == "same_day_close":
        exit_day = entry_day
        observation = debit_liquidation_value(
            archive, long_leg, short_leg, exit_day, clock_minute(time(15, 55)), execution, backward=True
        )
        if observation:
            exit_value, exit_minute = observation
            exit_reason = "same_day_close"
    elif exit_policy == "next_session_close":
        exit_day = next_session(archive, event.symbol, entry_day)
        if exit_day:
            observation = debit_liquidation_value(
                archive, long_leg, short_leg, exit_day, clock_minute(time(15, 55)), execution, backward=True
            )
            if observation:
                exit_value, exit_minute = observation
                exit_reason = "next_session_close"
    else:
        target_value = debit + 0.50 * (width - debit)
        stop_value = 0.50 * debit
        sessions = [
            day for day in archive.session_days(event.symbol)
            if entry_day <= day <= long_leg.expiration_date
        ]
        for day in sessions:
            start_minute = actual_entry + 1 if day == entry_day else clock_minute(time(9, 30))
            for minute in range(start_minute, clock_minute(time(15, 56))):
                observation = debit_liquidation_value(
                    archive, long_leg, short_leg, day, minute, execution, backward=False
                )
                if observation is None:
                    continue
                value, actual = observation
                if value <= stop_value:
                    exit_value, exit_day, exit_minute, exit_reason = value, day, actual, "50pct_debit_stop"
                    break
                if value >= target_value:
                    exit_value, exit_day, exit_minute, exit_reason = value, day, actual, "50pct_max_profit_target"
                    break
            if exit_value is not None:
                break
        if exit_value is None:
            expiry_close = prior_close(archive, event.symbol, long_leg.expiration_date)
            if expiry_close is not None:
                exit_value = max(
                    0.0,
                    min(width, spread_intrinsic(direction, long_leg.strike, short_leg.strike, expiry_close)),
                )
                exit_day = long_leg.expiration_date
                exit_minute = clock_minute(time(16, 0))
                exit_reason = "expiration_settlement"

    if exit_value is None or exit_day is None or exit_minute is None:
        base.skip_reason = "missing option exit observation"
        return base
    pnl = (exit_value - debit) * 100.0 - total_fees
    base.status = "ok"
    base.exit_day = exit_day
    base.exit_time_et = minute_clock(exit_minute)
    base.exit_reason = exit_reason
    base.exit_value = exit_value
    base.max_risk_dollars = risk
    base.fees_dollars = total_fees
    base.pnl_dollars = pnl
    base.return_on_risk_pct = pnl / risk * 100.0
    return base


def opening_range_signal(archive: Archive, event: EarningsEvent) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    opening = [bars[minute] for minute in range(570, 630) if minute in bars]
    if len(opening) < 50:
        return None
    range_high = max(row[1] for row in opening)
    range_low = min(row[2] for row in opening)
    prior_direction: str | None = None
    for close_minute in range(634, 720, 5):
        bar = bars.get(close_minute)
        if bar is None:
            prior_direction = None
            continue
        direction = "bullish" if bar[3] > range_high else "bearish" if bar[3] < range_low else None
        if direction and direction == prior_direction:
            return direction, close_minute + 1
        prior_direction = direction
    return None


def gap_hold_signal(archive: Archive, event: EarningsEvent) -> tuple[str, int, float] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    if 570 not in bars or 629 not in bars:
        return None
    reference = prior_close(archive, event.symbol, event.report_date)
    if reference is None or reference <= 0:
        return None
    session_open = bars[570][0]
    confirmation = bars[629][3]
    gap = (session_open / reference - 1.0) * 100.0
    if gap >= 1.0 and confirmation > session_open and confirmation > reference:
        return "bullish", 630, gap
    if gap <= -1.0 and confirmation < session_open and confirmation < reference:
        return "bearish", 630, gap
    return None


def raw_entry_close(archive: Archive, contract: Contract, day: str, minute: int) -> float | None:
    bar, _actual = find_bar(archive.option_bars(contract.symbol, day), minute, forward=True)
    return float(bar[3]) if bar else None


def same_expiry_pool(
    archive: Archive, event: EarningsEvent
) -> tuple[str, list[Contract], list[Contract]] | None:
    calls = archive.contracts(event.report_date, event.symbol, "call")
    puts = archive.contracts(event.report_date, event.symbol, "put")
    common = sorted({row.expiration_date for row in calls} & {row.expiration_date for row in puts})
    if not common:
        return None
    report = date.fromisoformat(event.report_date)
    expiry = min(common, key=lambda value: ((date.fromisoformat(value) - report).days, value))
    return (
        expiry,
        [row for row in calls if row.expiration_date == expiry],
        [row for row in puts if row.expiration_date == expiry],
    )


def condor_debit_to_close(
    archive: Archive,
    legs: tuple[Contract, Contract, Contract, Contract],
    day: str,
    minute: int,
    execution: ExecutionModel,
    *,
    backward: bool,
) -> tuple[float, int] | None:
    long_put, short_put, short_call, long_call = legs
    observations = []
    for contract in legs:
        bar, actual = find_bar(
            archive.option_bars(contract.symbol, day), minute, forward=not backward
        )
        if bar is None or actual is None:
            return None
        observations.append((bar, actual))
    actual_minute = (
        min(actual for _bar, actual in observations)
        if backward
        else max(actual for _bar, actual in observations)
    )
    bars = [archive.option_bars(contract.symbol, day).get(actual_minute, observation[0]) for contract, observation in zip(legs, observations)]
    long_put_sell = execution.sell(bars[0][2])
    short_put_buy = execution.buy(bars[1][1])
    short_call_buy = execution.buy(bars[2][1])
    long_call_sell = execution.sell(bars[3][2])
    put_width = short_put.strike - long_put.strike
    call_width = long_call.strike - short_call.strike
    debit = short_put_buy - long_put_sell + short_call_buy - long_call_sell
    return max(0.0, min(max(put_width, call_width), debit)), actual_minute


def condor_trade(
    *,
    strategy: str,
    event: EarningsEvent,
    archive: Archive,
    execution: ExecutionModel,
    move_multiplier: float,
    exit_clock: time,
) -> TradeRow:
    base = TradeRow(
        strategy, event.symbol, event.report_date, event.next_trading_day,
        execution.name, "neutral", "skipped", None, "15:45", event.report_date,
        "15:45", event.next_trading_day, exit_clock.strftime("%H:%M"), None,
        None, None, prior_close(archive, event.symbol, event.report_date), None,
        momentum_5d(archive, event.symbol, event.report_date), None, None, None,
        None, None, None, None, None, None, None, None, None, None, None, None,
        None, None, None,
    )
    spot = underlying_spot(archive, event.symbol, event.report_date, 945)
    base.underlying_entry_spot = spot
    if spot is None:
        base.skip_reason = "missing underlying 15:45 bar"
        return base
    pools = same_expiry_pool(archive, event)
    if pools is None:
        base.skip_reason = "no common immediate expiration"
        return base
    expiry, calls, puts = pools
    base.expiration_date = expiry
    atm_call = min(calls, key=lambda row: abs(row.strike - spot))
    atm_put = min(puts, key=lambda row: abs(row.strike - spot))
    call_close = raw_entry_close(archive, atm_call, event.report_date, 945)
    put_close = raw_entry_close(archive, atm_put, event.report_date, 945)
    if call_close is None or put_close is None:
        base.skip_reason = "missing ATM straddle entry proxy"
        return base
    implied_move = call_close + put_close
    base.implied_move_proxy = implied_move
    upper = spot + move_multiplier * implied_move
    lower = spot - move_multiplier * implied_move
    short_calls = [row for row in calls if row.strike >= upper]
    short_puts = [row for row in puts if row.strike <= lower]
    if not short_calls or not short_puts:
        base.skip_reason = "no short strikes beyond implied-move boundary"
        return base
    short_call = min(short_calls, key=lambda row: row.strike)
    short_put = max(short_puts, key=lambda row: row.strike)
    wing_target = max(5.0, round((spot * 0.02) / 5.0) * 5.0)
    long_calls = [row for row in calls if row.strike >= short_call.strike + wing_target]
    long_puts = [row for row in puts if row.strike <= short_put.strike - wing_target]
    if not long_calls or not long_puts:
        base.skip_reason = "no protective wings at target width"
        return base
    long_call = min(long_calls, key=lambda row: row.strike)
    long_put = max(long_puts, key=lambda row: row.strike)
    legs = (long_put, short_put, short_call, long_call)
    base.long_put_contract, base.long_put_strike = long_put.symbol, long_put.strike
    base.short_put_contract, base.short_put_strike = short_put.symbol, short_put.strike
    base.short_call_contract, base.short_call_strike = short_call.symbol, short_call.strike
    base.long_call_contract, base.long_call_strike = long_call.symbol, long_call.strike

    observations = []
    for contract in legs:
        bar, actual = find_bar(archive.option_bars(contract.symbol, event.report_date), 945, forward=True)
        if bar is None or actual is None:
            base.skip_reason = "missing condor entry leg bar"
            return base
        observations.append((bar, actual))
    actual_entry = max(actual for _bar, actual in observations)
    bars = [archive.option_bars(contract.symbol, event.report_date).get(actual_entry, observation[0]) for contract, observation in zip(legs, observations)]
    long_put_buy = execution.buy(bars[0][1])
    short_put_sell = execution.sell(bars[1][2])
    short_call_sell = execution.sell(bars[2][2])
    long_call_buy = execution.buy(bars[3][1])
    credit = short_put_sell + short_call_sell - long_put_buy - long_call_buy
    put_width = short_put.strike - long_put.strike
    call_width = long_call.strike - short_call.strike
    max_width = max(put_width, call_width)
    if credit <= 0 or credit >= max_width:
        base.skip_reason = "non-economic reconstructed condor credit"
        return base
    exit_observation = condor_debit_to_close(
        archive,
        legs,
        event.next_trading_day,
        clock_minute(exit_clock),
        execution,
        backward=exit_clock >= time(15, 0),
    )
    if exit_observation is None:
        base.skip_reason = "missing condor exit leg bar"
        return base
    exit_debit, actual_exit = exit_observation
    total_fees = 8.0 * execution.fee_per_leg
    risk = (max_width - credit) * 100.0 + 4.0 * execution.fee_per_leg
    pnl = (credit - exit_debit) * 100.0 - total_fees
    base.status = "ok"
    base.entry_time_et = minute_clock(actual_entry)
    base.exit_time_et = minute_clock(actual_exit)
    base.exit_reason = f"post_earnings_{exit_clock.strftime('%H%M')}"
    base.entry_value = credit
    base.exit_value = exit_debit
    base.max_risk_dollars = risk
    base.fees_dollars = total_fees
    base.pnl_dollars = pnl
    base.return_on_risk_pct = pnl / risk * 100.0
    return base


def simulate(events: Sequence[EarningsEvent], next_week: Archive, immediate: Archive) -> list[TradeRow]:
    rows: list[TradeRow] = []
    for event in events:
        opening_signal = opening_range_signal(next_week, event)
        gap_signal = gap_hold_signal(next_week, event)
        momentum = momentum_5d(next_week, event.symbol, event.report_date)
        for execution in EXECUTION_MODELS:
            for exit_policy in ("same_day_close", "next_session_close", "managed"):
                if opening_signal:
                    direction, minute = opening_signal
                    rows.append(
                        debit_trade(
                            strategy=f"post_or_acceptance_{exit_policy}", event=event,
                            archive=next_week, execution=execution, direction=direction,
                            signal_time=minute - 1, entry_day=event.next_trading_day,
                            entry_minute=minute, exit_policy=exit_policy,
                        )
                    )
                else:
                    rows.append(
                        TradeRow(
                            f"post_or_acceptance_{exit_policy}", event.symbol, event.report_date,
                            event.next_trading_day, execution.name, "none", "skipped",
                            "no two-bar first-hour range acceptance", None, None, None, None,
                            None, None, None, None,
                            prior_close(next_week, event.symbol, event.report_date), None, momentum,
                            None, None, None, None, None, None, None, None, None, None,
                            None, None, None, None, None, None, None, None,
                        )
                    )
                if gap_signal:
                    direction, minute, gap = gap_signal
                    rows.append(
                        debit_trade(
                            strategy=f"post_gap_hold_{exit_policy}", event=event,
                            archive=next_week, execution=execution, direction=direction,
                            signal_time=629, entry_day=event.next_trading_day,
                            entry_minute=minute, exit_policy=exit_policy, gap_pct=gap,
                        )
                    )
                else:
                    rows.append(
                        TradeRow(
                            f"post_gap_hold_{exit_policy}", event.symbol, event.report_date,
                            event.next_trading_day, execution.name, "none", "skipped",
                            "no 1pct same-direction gap hold at 10:30", None, None, None,
                            None, None, None, None, None,
                            prior_close(next_week, event.symbol, event.report_date), None, momentum,
                            None, None, None, None, None, None, None, None, None, None,
                            None, None, None, None, None, None, None, None,
                        )
                    )
            if momentum is None or abs(momentum) < 1.0:
                direction = "none"
            else:
                direction = "bullish" if momentum > 0 else "bearish"
            for label, exit_minute in (("open", 575), ("1030", 630), ("close", 955)):
                strategy = f"pretrend_exit_{label}"
                if direction == "none":
                    rows.append(
                        TradeRow(
                            strategy, event.symbol, event.report_date, event.next_trading_day,
                            execution.name, direction, "skipped", "five-session momentum below 1pct",
                            None, None, None, None, None, None, None, None,
                            prior_close(next_week, event.symbol, event.report_date), None, momentum,
                            None, None, None, None, None, None, None, None, None, None,
                            None, None, None, None, None, None, None, None,
                        )
                    )
                else:
                    trade = debit_trade(
                        strategy=strategy,
                        event=event,
                        archive=next_week,
                        execution=execution,
                        direction=direction,
                        signal_time=945,
                        entry_day=event.report_date,
                        entry_minute=945,
                        exit_policy="fixed",
                        momentum=momentum,
                        fixed_exit_day=event.next_trading_day,
                        fixed_exit_minute=exit_minute,
                        fixed_exit_backward=label == "close",
                    )
                    if trade.status == "ok":
                        trade.exit_reason = f"post_earnings_{label}"
                    rows.append(trade)
            for multiplier in (1.0, 1.25):
                for label, exit_clock in (("0935", time(9, 35)), ("1030", time(10, 30)), ("close", time(15, 55))):
                    rows.append(
                        condor_trade(
                            strategy=f"condor_{multiplier:.2f}x_exit_{label}", event=event,
                            archive=immediate, execution=execution,
                            move_multiplier=multiplier, exit_clock=exit_clock,
                        )
                    )
    return rows


def safe_mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def profit_factor(values: Sequence[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses > 0:
        return gains / losses
    return math.inf if gains > 0 else None


def max_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    equity = peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def summarize(rows: Sequence[TradeRow], events: Sequence[EarningsEvent]) -> list[VariantResult]:
    strategies = sorted({row.strategy for row in rows})
    models = sorted({row.execution_model for row in rows})
    scopes = ["ALL", "MSFT", "META", "AAPL", "AMZN"]
    event_counts = {"ALL": len(events)}
    event_counts.update({symbol: sum(event.symbol == symbol for event in events) for symbol in scopes[1:]})
    base_map: dict[tuple[str, str], VariantResult] = {}
    results: list[VariantResult] = []
    for strategy in strategies:
        for model in models:
            for scope in scopes:
                subset = [
                    row for row in rows
                    if row.strategy == strategy and row.execution_model == model
                    and (scope == "ALL" or row.symbol == scope)
                ]
                executed = [row for row in subset if row.status == "ok" and row.return_on_risk_pct is not None]
                returns = [float(row.return_on_risk_pct) for row in executed]
                pnls = [float(row.pnl_dollars or 0.0) for row in executed]
                result = VariantResult(
                    strategy=strategy,
                    execution_model=model,
                    scope=scope,
                    event_n=event_counts[scope],
                    executed_n=len(executed),
                    coverage_pct=len(executed) / event_counts[scope] * 100.0 if event_counts[scope] else 0.0,
                    mean_return_pct=safe_mean(returns),
                    median_return_pct=median(returns) if returns else None,
                    win_rate_pct=sum(value > 0 for value in returns) / len(returns) * 100.0 if returns else None,
                    total_pnl_dollars=sum(pnls) if pnls else None,
                    mean_pnl_dollars=safe_mean(pnls),
                    profit_factor=profit_factor(pnls),
                    exclude_best_1_mean_pct=mean(sorted(returns)[:-1]) if len(returns) > 1 else None,
                    best_return_pct=max(returns) if returns else None,
                    worst_return_pct=min(returns) if returns else None,
                    max_drawdown_dollars=max_drawdown(pnls),
                    robust_positive=False,
                )
                results.append(result)
                base_map[(strategy, f"{scope}:{model}")] = result
    for result in results:
        if result.execution_model != "base":
            continue
        worse = base_map.get((result.strategy, f"{result.scope}:worse"))
        result.robust_positive = bool(
            result.executed_n >= (8 if result.scope == "ALL" else 4)
            and result.mean_return_pct is not None and result.mean_return_pct > 0
            and result.median_return_pct is not None and result.median_return_pct > 0
            and result.exclude_best_1_mean_pct is not None and result.exclude_best_1_mean_pct > 0
            and worse is not None and worse.mean_return_pct is not None and worse.mean_return_pct > 0
        )
    return results


def write_csv(path: Path, rows: Iterable[dict[str, object]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.2f}{suffix}"


def render_report(results: Sequence[VariantResult], rows: Sequence[TradeRow]) -> str:
    all_base = [row for row in results if row.scope == "ALL" and row.execution_model == "base" and row.executed_n]
    ranked = sorted(
        all_base,
        key=lambda row: (
            not row.robust_positive,
            -(row.mean_return_pct if row.mean_return_pct is not None else -1e9),
        ),
    )
    lines = [
        "# Earnings Defined-Risk Options Lab",
        "",
        "Research-only historical reconstruction. No butterflies, naked options, or order routing.",
        "",
        "## Frozen mechanics",
        "",
        "- 24 events: six completed earnings reports each for MSFT, META, AAPL, and AMZN.",
        "- Post-earnings opening-range signal: two consecutive five-minute closes outside the first-hour range; enter next minute.",
        "- Gap-hold signal: at least a 1% opening gap that remains beyond both the opening price and prior close at 10:30 ET.",
        "- Debit spreads: next-Friday expiration, ATM long leg, short leg near 2.5% OTM.",
        "- Pre-event trend: five-session momentum above +1% or below -1%; entry at 15:45 ET before earnings.",
        "- Condors: immediate Friday expiry; short strikes at 1.00x or 1.25x the ATM-straddle trade-price proxy; proportional protective wings.",
        "- Execution: each leg uses observed one-minute highs/lows plus fees; base, worse, and severe stress models.",
        "",
        "## Aggregate base-execution ranking",
        "",
        "| Rank | Strategy | Trades | Coverage | Mean ROR | Median | Win rate | Ex-best-1 | Total 1-lot P/L | Worse mean | Robust |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    lookup = {(row.strategy, row.scope, row.execution_model): row for row in results}
    for index, row in enumerate(ranked, start=1):
        worse = lookup.get((row.strategy, "ALL", "worse"))
        lines.append(
            f"| {index} | {row.strategy} | {row.executed_n} | {row.coverage_pct:.1f}% | "
            f"{fmt(row.mean_return_pct, '%')} | {fmt(row.median_return_pct, '%')} | "
            f"{fmt(row.win_rate_pct, '%')} | {fmt(row.exclude_best_1_mean_pct, '%')} | "
            f"{fmt(row.total_pnl_dollars)} | {fmt(worse.mean_return_pct if worse else None, '%')} | "
            f"{'YES' if row.robust_positive else 'NO'} |"
        )
    lines.extend(["", "## Best result by ticker", "", "| Ticker | Strategy | Trades | Mean ROR | Median | Win rate | Worse mean |", "|---|---|---:|---:|---:|---:|---:|"])
    for symbol in ("MSFT", "META", "AAPL", "AMZN"):
        candidates = [
            row for row in results
            if row.scope == symbol and row.execution_model == "base" and row.executed_n
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda row: row.mean_return_pct if row.mean_return_pct is not None else -1e9)
        worse = lookup.get((best.strategy, symbol, "worse"))
        lines.append(
            f"| {symbol} | {best.strategy} | {best.executed_n} | {fmt(best.mean_return_pct, '%')} | "
            f"{fmt(best.median_return_pct, '%')} | {fmt(best.win_rate_pct, '%')} | "
            f"{fmt(worse.mean_return_pct if worse else None, '%')} |"
        )
    skip_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.status != "ok":
            skip_counts[row.skip_reason or "unknown"] += 1
    lines.extend(["", "## Data and interpretation limits", ""])
    lines.extend(
        [
            "- Historical NBBO is unavailable; trade-bar extremes are conservative execution proxies, not executable quoted markets.",
            "- Six events per ticker is a very small sample. Ranking differences are descriptive, not statistically significant.",
            "- Earnings dates and rules were frozen before results were calculated.",
            "- One-contract P/L does not represent portfolio sizing or overlapping-position risk.",
            "- A result is marked robust only when base mean, median, ex-best-one mean, and worse-fill mean are positive with minimum coverage.",
            "",
            "Most common skipped observations:",
        ]
    )
    for reason, count in sorted(skip_counts.items(), key=lambda item: (-item[1], item[0]))[:10]:
        lines.append(f"- {reason}: {count}")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, object]:
    events = parse_events(Path(args.events))
    next_week = Archive((Path(args.next_week_db),))
    immediate = Archive(tuple(Path(value) for value in args.immediate_db))
    try:
        rows = simulate(events, next_week, immediate)
        results = summarize(rows, events)
    finally:
        next_week.close()
        immediate.close()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "trade_log.csv", [asdict(row) for row in rows])
    write_csv(output / "variant_results.csv", [asdict(row) for row in results])
    report_text = render_report(results, rows)
    (output / "report.md").write_text(report_text, encoding="utf-8")
    payload = {
        "events": [asdict(row) for row in events],
        "execution_models": [asdict(row) for row in EXECUTION_MODELS],
        "summary": {
            "events": len(events),
            "trade_rows": len(rows),
            "executed_rows": sum(row.status == "ok" for row in rows),
            "strategies": len({row.strategy for row in rows}),
            "robust_aggregate_variants": [
                row.strategy for row in results
                if row.scope == "ALL" and row.execution_model == "base" and row.robust_positive
            ],
        },
        "results": [asdict(row) for row in results],
        "files": {
            "trade_log": str(output / "trade_log.csv"),
            "variant_results": str(output / "variant_results.csv"),
            "report": str(output / "report.md"),
        },
    }
    (output / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest defined-risk earnings option structures.")
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--next-week-db", default=str(DEFAULT_NEXT_WEEK_DB))
    parser.add_argument(
        "--immediate-db", action="append",
        default=[str(DEFAULT_AAPL_CONDOR_DB), str(DEFAULT_OTHER_CONDOR_DB)],
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    run(build_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
