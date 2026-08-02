"""Capital-constrained multi-stock option strategy research.

This module completes the frozen study described in the options-research handoff:

* underlyings: AMZN, GOOGL, NVDA, and META;
* structures: long calls, bull-call debit spreads, call butterflies,
  bull-put credit spreads, long puts, and bear-put debit spreads;
* signals: unfiltered monthly control, momentum, trend, pullback, EMA-50
  reclaim/breakdown, and bearish momentum variants;
* exits: expiration, debit profit/loss rules, credit 50% profit targets, and
  seven-DTE exits;
* starting equity: $250, $500, $1,000, and $2,000;
* one contract and one open position at a time;
* maximum theoretical loss must fit inside current account equity;
* fills use the severe trade-bar execution approximation from Cipher.

The source archives contain historical trades and one-minute trade bars, not
historical NBBO quotes.  Results are therefore exploratory execution
approximations and must never be represented as executable-quote evidence.
This module is read-only and contains no brokerage or order-routing code.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

try:
    from .historical_option_strategy_lab import (
        EXECUTION_ASSUMPTIONS,
        ContractObservation,
        DecisionSnapshot,
        ExecutionAssumption,
        HistoricalOptionResearchDataset,
        StrategyLabError,
    )
    from .recent_option_strategy_expansion import PathBar, RecentPathStore
except ImportError:  # Direct script/test import.
    from historical_option_strategy_lab import (
        EXECUTION_ASSUMPTIONS,
        ContractObservation,
        DecisionSnapshot,
        ExecutionAssumption,
        HistoricalOptionResearchDataset,
        StrategyLabError,
    )
    from recent_option_strategy_expansion import PathBar, RecentPathStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE_ROOT = ROOT / "cipher-system" / "data" / "historical_options"
DEFAULT_OUTPUT = DEFAULT_ARCHIVE_ROOT / "capital_efficient_multi_stock_lab"
DEFAULT_TICKERS = ("AMZN", "GOOGL", "NVDA", "META")
DEFAULT_BUDGETS = (250.0, 500.0, 1_000.0, 2_000.0)
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
SEVERE = next(row for row in EXECUTION_ASSUMPTIONS if row.name == "severe")


class CapitalEfficientStudyError(RuntimeError):
    """Raised when the frozen study cannot be completed safely."""


def _finite(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _mean(values: Sequence[float]) -> float | None:
    return statistics.mean(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _ema(values: Sequence[float], periods: int) -> float | None:
    if periods <= 1 or len(values) < periods:
        return None
    multiplier = 2.0 / (periods + 1.0)
    result = statistics.mean(values[:periods])
    for value in values[periods:]:
        result = (float(value) - result) * multiplier + result
    return result


def _annualized_realized_vol(
    values: Sequence[float],
    periods: int,
) -> float | None:
    if periods <= 1 or len(values) < periods + 1:
        return None
    window = values[-(periods + 1) :]
    returns = [
        math.log(current / previous)
        for previous, current in zip(window, window[1:])
        if previous > 0 and current > 0
    ]
    if len(returns) < periods:
        return None
    return statistics.stdev(returns) * math.sqrt(252.0)


def _safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


@dataclass(frozen=True, slots=True)
class ArchivePaths:
    ticker: str
    put_database: Path
    call_database: Path


@dataclass(frozen=True, slots=True)
class ArchiveAudit:
    ticker: str
    option_type: str
    database: str
    exists: bool
    integrity: str | None
    database_bytes: int
    database_sha256: str | None
    decision_dates: int
    selected_contracts: int
    observed_contracts: int
    option_bars: int
    option_trades: int
    daily_underlying_bars: int
    complete_runs: int
    failed_runs: int
    running_runs: int
    failed_windows: int
    running_windows: int
    underlying_symbols: tuple[str, ...]
    option_types: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.blockers


@dataclass(frozen=True, slots=True)
class SplitEvent:
    effective_date: date
    backward_adjustment_factor: float
    raw_close_ratio: float


@dataclass(frozen=True, slots=True)
class StudySignalFeatures:
    ticker: str
    decision_date: date
    prior_close: float
    prior_return_5d: float | None
    prior_return_20d: float | None
    ema_50: float | None
    previous_ema_50: float | None
    sma_200: float | None
    above_sma_200: bool | None
    ema50_reclaim: bool
    ema50_breakdown: bool
    history_rows: int
    realized_vol_20d: float | None = None
    realized_vol_60d: float | None = None


@dataclass(frozen=True, slots=True)
class LegTarget:
    quantity: int
    target_moneyness: float

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise ValueError("quantity must be an integer")
        if self.quantity == 0:
            raise ValueError("quantity cannot be zero")
        target = _finite(self.target_moneyness, name="target_moneyness")
        if not 0.5 < target < 1.5:
            raise ValueError("target_moneyness is outside the supported range")
        object.__setattr__(self, "target_moneyness", target)


@dataclass(frozen=True, slots=True)
class ExitRule:
    name: str
    profit_fraction: float | None = None
    loss_fraction: float | None = None
    exit_dte: int | None = None
    credit_close_multiple: float | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("exit rule name is required")
        if self.profit_fraction is not None:
            profit = _finite(self.profit_fraction, name="profit_fraction")
            if profit <= 0:
                raise ValueError("profit_fraction must be positive")
            object.__setattr__(self, "profit_fraction", profit)
        if self.loss_fraction is not None:
            loss = _finite(self.loss_fraction, name="loss_fraction")
            if not 0 < loss < 1:
                raise ValueError("loss_fraction must be in (0, 1)")
            object.__setattr__(self, "loss_fraction", loss)
        if self.exit_dte is not None:
            if not isinstance(self.exit_dte, int) or isinstance(self.exit_dte, bool):
                raise ValueError("exit_dte must be an integer")
            if self.exit_dte < 0:
                raise ValueError("exit_dte cannot be negative")
        if self.credit_close_multiple is not None:
            close_multiple = _finite(
                self.credit_close_multiple,
                name="credit_close_multiple",
            )
            if close_multiple <= 1.0:
                raise ValueError("credit_close_multiple must exceed 1.0")
            object.__setattr__(self, "credit_close_multiple", close_multiple)


DEBIT_EXPIRY = ExitRule("expiry")
DEBIT_PT50_SL50 = ExitRule("pt50_sl50", profit_fraction=0.50, loss_fraction=0.50)
DEBIT_PT100_SL50 = ExitRule("pt100_sl50", profit_fraction=1.00, loss_fraction=0.50)
DEBIT_DTE7 = ExitRule("dte7", exit_dte=7)
DEBIT_DTE3 = ExitRule("dte3", exit_dte=3)
DEBIT_PT50_SL50_DTE3 = ExitRule(
    "pt50_sl50_dte3",
    profit_fraction=0.50,
    loss_fraction=0.50,
    exit_dte=3,
)
CREDIT_EXPIRY = ExitRule("expiry")
CREDIT_PT50 = ExitRule("pt50", profit_fraction=0.50)
CREDIT_DTE7 = ExitRule("dte7", exit_dte=7)
CREDIT_PT50_SL2X = ExitRule(
    "pt50_sl2x",
    profit_fraction=0.50,
    credit_close_multiple=2.0,
)
CREDIT_PT50_DTE3 = ExitRule("pt50_dte3", profit_fraction=0.50, exit_dte=3)
CREDIT_PT50_SL2X_DTE3 = ExitRule(
    "pt50_sl2x_dte3",
    profit_fraction=0.50,
    exit_dte=3,
    credit_close_multiple=2.0,
)


@dataclass(frozen=True, slots=True)
class CapitalStrategySpec:
    name: str
    family: str
    direction: str
    option_type: str
    signal: str
    legs: tuple[LegTarget, ...]
    exit_rule: ExitRule
    target_dte: int = 35
    strike_offsets: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        families = {
            "long_call",
            "bull_call_spread",
            "call_butterfly",
            "put_butterfly",
            "bull_put_spread",
            "bear_call_spread",
            "long_put",
            "bear_put_spread",
        }
        if self.family not in families:
            raise ValueError(f"unsupported family {self.family!r}")
        if self.direction not in {"bullish", "bearish"}:
            raise ValueError("direction must be bullish or bearish")
        if self.option_type not in {"call", "put"}:
            raise ValueError("option_type must be call or put")
        bullish_signals = {
            "always",
            "momentum20_positive",
            "momentum20_and_trend200",
            "mild_pullback_uptrend",
            "shallow_pullback_momentum",
            "calm_uptrend",
            "ema50_reclaim",
        }
        bearish_signals = {
            "always",
            "momentum20_negative",
            "momentum20_negative_below_sma200",
            "ema50_breakdown",
        }
        allowed = bullish_signals if self.direction == "bullish" else bearish_signals
        if self.signal not in allowed:
            raise ValueError(f"signal {self.signal!r} is invalid for {self.direction}")
        if not self.name.strip() or not self.legs:
            raise ValueError("strategy name and legs are required")
        if not isinstance(self.target_dte, int) or isinstance(self.target_dte, bool):
            raise ValueError("target_dte must be an integer")
        if self.target_dte <= 0:
            raise ValueError("target_dte must be positive")
        object.__setattr__(self, "legs", tuple(self.legs))
        if self.strike_offsets is not None:
            offsets = tuple(
                _finite(value, name="strike_offset")
                for value in self.strike_offsets
            )
            if len(offsets) != len(self.legs):
                raise ValueError("strike_offsets must match the leg count")
            if abs(offsets[0]) > 1e-9:
                raise ValueError("the anchor leg strike offset must be zero")
            object.__setattr__(self, "strike_offsets", offsets)

    @property
    def is_credit(self) -> bool:
        return self.family in {"bull_put_spread", "bear_call_spread"}

    @property
    def contracts_per_position(self) -> int:
        return sum(abs(leg.quantity) for leg in self.legs)


@dataclass(frozen=True, slots=True)
class SelectedLeg:
    quantity: int
    contract: ContractObservation
    entry_price: float


@dataclass(frozen=True, slots=True)
class CandidateTrade:
    strategy: str
    family: str
    direction: str
    signal: str
    exit_rule: str
    ticker: str
    decision_date: date
    expiration_date: date
    exit_date: date
    exit_reason: str
    days_held: int
    leg_symbols: tuple[str, ...]
    leg_quantities: tuple[int, ...]
    leg_strikes: tuple[float, ...]
    entry_cash_per_share: float
    exit_cash_per_share: float
    expiration_payoff_per_share: float
    fees: float
    pnl: float
    maximum_loss: float
    return_on_maximum_loss: float
    minimum_entry_volume: float
    signal_strength: float
    feature_return_5d: float | None
    feature_return_20d: float | None
    feature_above_sma_200: bool | None
    feature_ema50_reclaim: bool
    feature_ema50_breakdown: bool
    max_adverse_pnl: float
    max_favorable_pnl: float
    exit_timestamp: str | None


@dataclass(frozen=True, slots=True)
class CandidateSkip:
    strategy: str
    ticker: str
    decision_date: date
    reason: str


@dataclass(frozen=True, slots=True)
class CandidateRun:
    spec: CapitalStrategySpec
    trades: tuple[CandidateTrade, ...]
    skips: tuple[CandidateSkip, ...]


@dataclass(frozen=True, slots=True)
class PortfolioTrade:
    strategy: str
    ticker: str
    decision_date: date
    exit_date: date
    pnl: float
    maximum_loss: float
    max_adverse_pnl: float
    equity_before: float
    equity_after: float
    account_return: float
    source_trade: CandidateTrade


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    strategy: str
    family: str
    signal: str
    exit_rule: str
    starting_equity: float
    minimum_entry_volume: float
    maximum_trade_risk_fraction: float
    ending_equity: float
    total_pnl: float
    trade_count: int
    win_rate: float | None
    mean_pnl: float | None
    median_pnl: float | None
    worst_trade: float | None
    best_trade: float | None
    pnl_excluding_best_trade: float | None
    realized_max_drawdown_pct: float
    observed_mtm_max_drawdown_pct: float
    maximum_single_trade_risk_pct: float
    p_value_sign_test: float | None
    adjusted_p_value_holm: float | None
    yearly_pnl: Mapping[str, float]
    ticker_pnl: Mapping[str, float]
    ticker_trade_count: Mapping[str, int]
    positive_ticker_count: int
    positive_pnl_concentration: float | None
    skipped_while_position_open: int
    skipped_for_capital: int
    skipped_for_risk_cap: int
    trades: tuple[PortfolioTrade, ...]
    promotion_checks: Mapping[str, bool]
    promoted: bool


def detect_split_events(
    daily_closes: Sequence[tuple[date, float]],
) -> tuple[SplitEvent, ...]:
    """Detect only large, mechanically plausible split discontinuities.

    Alpaca's raw daily bars preserve stock splits.  For indicators we infer the
    nearest simple split factor from discontinuities larger than roughly 2:1.
    This is intentionally narrow and does not treat ordinary gaps as splits.
    Contract lives crossing a detected event are blocked separately because
    OCC deliverable adjustments are not present in the local archive.
    """
    events: list[SplitEvent] = []
    for (previous_day, previous_close), (current_day, current_close) in zip(
        daily_closes,
        daily_closes[1:],
    ):
        if previous_close <= 0 or current_close <= 0:
            continue
        raw_ratio = current_close / previous_close
        factor: float | None = None
        if raw_ratio < 0.55:
            denominator = round(1.0 / raw_ratio)
            if denominator >= 2:
                candidate = 1.0 / denominator
                if abs(raw_ratio - candidate) / candidate <= 0.15:
                    factor = candidate
        elif raw_ratio > 1.8:
            numerator = round(raw_ratio)
            if numerator >= 2:
                candidate = float(numerator)
                if abs(raw_ratio - candidate) / candidate <= 0.15:
                    factor = candidate
        if factor is not None:
            events.append(
                SplitEvent(
                    effective_date=current_day,
                    backward_adjustment_factor=factor,
                    raw_close_ratio=raw_ratio,
                )
            )
    return tuple(events)


def split_adjusted_daily_closes(
    daily_closes: Sequence[tuple[date, float]],
    events: Sequence[SplitEvent],
) -> tuple[tuple[date, float], ...]:
    adjusted: list[tuple[date, float]] = []
    for day, close in daily_closes:
        factor = math.prod(
            event.backward_adjustment_factor
            for event in events
            if day < event.effective_date
        )
        adjusted.append((day, close * factor))
    return tuple(adjusted)


class MultiStockDataset:
    """One ticker's point-in-time put/call archives plus cached paths."""

    def __init__(self, paths: ArchivePaths):
        self.ticker = paths.ticker.upper()
        self.paths = paths
        self.put_data = HistoricalOptionResearchDataset(
            paths.put_database,
            underlying_symbol=self.ticker,
        )
        self.call_data = HistoricalOptionResearchDataset(
            paths.call_database,
            underlying_symbol=self.ticker,
        )
        self.put_snapshots = {row.decision_date: row for row in self.put_data.snapshots}
        self.call_snapshots = {row.decision_date: row for row in self.call_data.snapshots}
        self.put_paths = RecentPathStore(paths.put_database)
        self.call_paths = RecentPathStore(paths.call_database)
        self.split_events = detect_split_events(self.put_data.daily_closes)
        self.adjusted_daily_closes = split_adjusted_daily_closes(
            self.put_data.daily_closes,
            self.split_events,
        )
        self._adjusted_daily_index = {
            day: index for index, (day, _close) in enumerate(self.adjusted_daily_closes)
        }
        self._synchronized_cache: dict[
            tuple[str, tuple[str, ...], str, str],
            tuple[tuple[datetime, tuple[PathBar, ...]], ...],
        ] = {}
        self._feature_cache: dict[date, StudySignalFeatures] = {}

    def data(self, option_type: str) -> HistoricalOptionResearchDataset:
        return self.call_data if option_type == "call" else self.put_data

    def snapshot(self, option_type: str, decision_date: date) -> DecisionSnapshot | None:
        snapshots = self.call_snapshots if option_type == "call" else self.put_snapshots
        return snapshots.get(decision_date)

    def decision_dates(self, option_type: str) -> tuple[date, ...]:
        snapshots = self.call_snapshots if option_type == "call" else self.put_snapshots
        return tuple(sorted(snapshots))

    def features(self, decision_date: date) -> StudySignalFeatures:
        if decision_date in self._feature_cache:
            return self._feature_cache[decision_date]
        index = self._adjusted_daily_index.get(decision_date)
        if index is None:
            raise CapitalEfficientStudyError(
                f"{self.ticker} is missing a daily bar for {decision_date}"
            )
        history = [value for _, value in self.adjusted_daily_closes[:index]]
        if len(history) < 21:
            raise CapitalEfficientStudyError(
                f"{self.ticker} has insufficient signal history before {decision_date}"
            )
        prior_close = history[-1]
        return_5d = history[-1] / history[-6] - 1.0 if len(history) >= 6 else None
        return_20d = history[-1] / history[-21] - 1.0 if len(history) >= 21 else None
        realized_vol_20d = _annualized_realized_vol(history, 20)
        realized_vol_60d = _annualized_realized_vol(history, 60)
        ema50 = _ema(history, 50)
        previous_ema50 = _ema(history[:-1], 50) if len(history) >= 51 else None
        sma200 = statistics.mean(history[-200:]) if len(history) >= 200 else None
        above200 = prior_close > sma200 if sma200 is not None else None
        reclaim = bool(
            ema50 is not None
            and previous_ema50 is not None
            and history[-1] > ema50
            and history[-2] <= previous_ema50
        )
        breakdown = bool(
            ema50 is not None
            and previous_ema50 is not None
            and history[-1] < ema50
            and history[-2] >= previous_ema50
        )
        result = StudySignalFeatures(
            ticker=self.ticker,
            decision_date=decision_date,
            prior_close=prior_close,
            prior_return_5d=return_5d,
            prior_return_20d=return_20d,
            ema_50=ema50,
            previous_ema_50=previous_ema50,
            sma_200=sma200,
            above_sma_200=above200,
            ema50_reclaim=reclaim,
            ema50_breakdown=breakdown,
            history_rows=len(history),
            realized_vol_20d=realized_vol_20d,
            realized_vol_60d=realized_vol_60d,
        )
        self._feature_cache[decision_date] = result
        return result

    def split_events_during(
        self,
        start: date,
        end: date,
    ) -> tuple[SplitEvent, ...]:
        return tuple(
            event
            for event in self.split_events
            if start < event.effective_date <= end
        )

    def synchronized_bars(
        self,
        option_type: str,
        symbols: Sequence[str],
        *,
        after: datetime,
        through: date,
    ) -> tuple[tuple[datetime, tuple[PathBar, ...]], ...]:
        key = (option_type, tuple(symbols), _iso(after), through.isoformat())
        if key not in self._synchronized_cache:
            store = self.call_paths if option_type == "call" else self.put_paths
            self._synchronized_cache[key] = store.synchronized_bars(
                symbols,
                after=after,
                through=through,
            )
        return self._synchronized_cache[key]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    row = db.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (table,),
    ).fetchone()
    return row is not None


def _scalar(db: sqlite3.Connection, query: str, params: Sequence[Any] = ()) -> int:
    row = db.execute(query, tuple(params)).fetchone()
    return int(row[0] or 0) if row else 0


def audit_archive(database: str | Path, ticker: str, option_type: str) -> ArchiveAudit:
    path = Path(database)
    ticker = ticker.upper()
    blockers: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return ArchiveAudit(
            ticker=ticker,
            option_type=option_type,
            database=str(path),
            exists=False,
            integrity=None,
            database_bytes=0,
            database_sha256=None,
            decision_dates=0,
            selected_contracts=0,
            observed_contracts=0,
            option_bars=0,
            option_trades=0,
            daily_underlying_bars=0,
            complete_runs=0,
            failed_runs=0,
            running_runs=0,
            failed_windows=0,
            running_windows=0,
            underlying_symbols=(),
            option_types=(),
            blockers=("database_missing",),
            warnings=(),
        )
    try:
        with sqlite3.connect(path) as db:
            integrity_row = db.execute("pragma integrity_check").fetchone()
            integrity = str(integrity_row[0]) if integrity_row else "unknown"
            if integrity.lower() != "ok":
                blockers.append("sqlite_integrity_failed")
            required = {
                "decision_selections",
                "selection_observation_audit",
                "underlying_bars",
                "option_bars",
            }
            missing_tables = sorted(table for table in required if not _table_exists(db, table))
            if missing_tables:
                blockers.append("missing_required_tables:" + ",".join(missing_tables))
            decision_dates = (
                _scalar(db, "select count(distinct decision_date) from decision_selections")
                if _table_exists(db, "decision_selections")
                else 0
            )
            selected = (
                _scalar(db, "select count(*) from decision_selections")
                if _table_exists(db, "decision_selections")
                else 0
            )
            observed = (
                _scalar(
                    db,
                    "select count(*) from selection_observation_audit where observed_on_decision=1",
                )
                if _table_exists(db, "selection_observation_audit")
                else 0
            )
            option_bars = (
                _scalar(db, "select count(*) from option_bars")
                if _table_exists(db, "option_bars")
                else 0
            )
            option_trades = (
                _scalar(db, "select count(*) from option_trades")
                if _table_exists(db, "option_trades")
                else 0
            )
            daily_bars = (
                _scalar(
                    db,
                    "select count(*) from underlying_bars where symbol=? and timeframe='1Day'",
                    (ticker,),
                )
                if _table_exists(db, "underlying_bars")
                else 0
            )
            underlying_symbols = (
                tuple(
                    str(row[0]).upper()
                    for row in db.execute(
                        "select distinct symbol from underlying_bars order by symbol"
                    ).fetchall()
                )
                if _table_exists(db, "underlying_bars")
                else ()
            )
            option_types = (
                tuple(
                    str(row[0]).lower()
                    for row in db.execute(
                        "select distinct option_type from decision_selections order by option_type"
                    ).fetchall()
                )
                if _table_exists(db, "decision_selections")
                else ()
            )
            if _table_exists(db, "download_runs"):
                complete_runs = _scalar(
                    db, "select count(*) from download_runs where status='complete'"
                )
                failed_runs = _scalar(
                    db, "select count(*) from download_runs where status='failed'"
                )
                running_runs = _scalar(
                    db, "select count(*) from download_runs where status='running'"
                )
            else:
                complete_runs = failed_runs = running_runs = 0
                warnings.append("download_runs_table_missing")
            if _table_exists(db, "download_windows"):
                failed_windows = _scalar(
                    db, "select count(*) from download_windows where status='failed'"
                )
                running_windows = _scalar(
                    db, "select count(*) from download_windows where status='running'"
                )
            else:
                failed_windows = running_windows = 0
                warnings.append("download_windows_table_missing")
    except sqlite3.DatabaseError as exc:
        blockers.append(f"sqlite_error:{exc}")
        integrity = "error"
        decision_dates = selected = observed = option_bars = option_trades = daily_bars = 0
        complete_runs = failed_runs = running_runs = failed_windows = running_windows = 0
        underlying_symbols = option_types = ()

    if decision_dates <= 0:
        blockers.append("no_decision_dates")
    if selected <= 0:
        blockers.append("no_selected_contracts")
    if selected != observed:
        blockers.append("not_all_selected_contracts_observed_on_decision")
    if option_bars <= 0:
        blockers.append("no_option_bars")
    if daily_bars < 250:
        blockers.append("insufficient_daily_underlying_history")
    if ticker not in underlying_symbols:
        blockers.append("expected_underlying_symbol_missing")
    if option_types and option_types != (option_type,):
        blockers.append("archive_option_type_mismatch")
    if running_windows:
        blockers.append("download_window_still_running")
    if running_runs:
        if complete_runs <= 0 or running_windows:
            blockers.append("download_run_still_running")
        else:
            warnings.append("stale_interrupted_download_run_present")
    if failed_runs:
        warnings.append("historical_failed_runs_present")
    if failed_windows:
        warnings.append("historical_failed_windows_present")
    if option_trades <= 0:
        warnings.append("option_trades_absent_bars_only")

    return ArchiveAudit(
        ticker=ticker,
        option_type=option_type,
        database=str(path),
        exists=True,
        integrity=integrity,
        database_bytes=path.stat().st_size,
        database_sha256=_sha256_file(path),
        decision_dates=decision_dates,
        selected_contracts=selected,
        observed_contracts=observed,
        option_bars=option_bars,
        option_trades=option_trades,
        daily_underlying_bars=daily_bars,
        complete_runs=complete_runs,
        failed_runs=failed_runs,
        running_runs=running_runs,
        failed_windows=failed_windows,
        running_windows=running_windows,
        underlying_symbols=underlying_symbols,
        option_types=option_types,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def _candidate_archive_names(ticker: str, option_type: str) -> tuple[str, ...]:
    slug = ticker.lower()
    if option_type == "put":
        return (
            f"alpaca_{slug}_monthly_backfill",
            f"alpaca_{slug}_put_monthly_backfill",
            f"alpaca_{slug}_puts_monthly_backfill",
            f"alpaca_{slug}_put",
        )
    return (
        f"alpaca_{slug}_call_monthly_backfill",
        f"alpaca_{slug}_calls_monthly_backfill",
        f"alpaca_{slug}_call_recent",
        f"alpaca_{slug}_call",
    )


def resolve_archive_paths(
    archive_root: str | Path = DEFAULT_ARCHIVE_ROOT,
    tickers: Sequence[str] = DEFAULT_TICKERS,
    explicit: Mapping[str, Mapping[str, str | Path]] | None = None,
) -> tuple[ArchivePaths, ...]:
    root = Path(archive_root)
    explicit = explicit or {}
    result: list[ArchivePaths] = []
    for raw_ticker in tickers:
        ticker = str(raw_ticker).strip().upper()
        if not ticker:
            continue
        configured = explicit.get(ticker, {})
        databases: dict[str, Path] = {}
        for option_type in ("put", "call"):
            configured_path = configured.get(option_type)
            if configured_path:
                databases[option_type] = Path(configured_path)
                continue
            candidates = [
                root / name / "historical_options.sqlite"
                for name in _candidate_archive_names(ticker, option_type)
            ]
            existing = next((candidate for candidate in candidates if candidate.exists()), None)
            databases[option_type] = existing or candidates[0]
        result.append(
            ArchivePaths(
                ticker=ticker,
                put_database=databases["put"],
                call_database=databases["call"],
            )
        )
    return tuple(result)


def signal_passes(
    signal: str,
    features: StudySignalFeatures,
) -> tuple[bool, str | None, float]:
    r5 = features.prior_return_5d
    r20 = features.prior_return_20d
    distance_ema = (
        features.prior_close / features.ema_50 - 1.0
        if features.ema_50
        else 0.0
    )
    distance_sma200 = (
        features.prior_close / features.sma_200 - 1.0
        if features.sma_200
        else 0.0
    )
    if signal == "always":
        return True, None, 0.0
    if signal == "momentum20_positive":
        passed = r20 is not None and r20 > 0
        return passed, None if passed else "return_20d_not_positive", max(r20 or 0.0, 0.0)
    if signal == "momentum20_and_trend200":
        passed = r20 is not None and r20 > 0 and features.above_sma_200 is True
        strength = max(r20 or 0.0, 0.0) + max(distance_sma200, 0.0)
        return passed, None if passed else "positive_momentum_and_uptrend_not_met", strength
    if signal == "mild_pullback_uptrend":
        passed = (
            r5 is not None
            and -0.05 <= r5 < 0
            and features.above_sma_200 is True
        )
        strength = max(0.0, 0.05 - abs((r5 or 0.0) + 0.02)) + max(distance_sma200, 0.0)
        return passed, None if passed else "mild_pullback_uptrend_not_met", strength
    if signal == "shallow_pullback_momentum":
        passed = (
            r5 is not None
            and -0.03 <= r5 < 0
            and r20 is not None
            and r20 > 0
            and features.above_sma_200 is True
        )
        strength = (
            max(0.0, 0.03 - abs((r5 or 0.0) + 0.01))
            + max(r20 or 0.0, 0.0)
            + max(distance_sma200, 0.0)
        )
        return (
            passed,
            None if passed else "shallow_pullback_positive_momentum_not_met",
            strength,
        )
    if signal == "calm_uptrend":
        rv20 = features.realized_vol_20d
        rv60 = features.realized_vol_60d
        passed = (
            r20 is not None
            and r20 > 0
            and features.above_sma_200 is True
            and rv20 is not None
            and rv60 is not None
            and rv20 <= rv60
        )
        volatility_discount = max((rv60 or 0.0) - (rv20 or 0.0), 0.0)
        strength = max(r20 or 0.0, 0.0) + max(distance_sma200, 0.0) + volatility_discount
        return passed, None if passed else "calm_uptrend_not_met", strength
    if signal == "ema50_reclaim":
        passed = features.ema50_reclaim
        return passed, None if passed else "ema50_reclaim_not_present", max(distance_ema, 0.0)
    if signal == "momentum20_negative":
        passed = r20 is not None and r20 < 0
        return passed, None if passed else "return_20d_not_negative", max(-(r20 or 0.0), 0.0)
    if signal == "momentum20_negative_below_sma200":
        passed = r20 is not None and r20 < 0 and features.above_sma_200 is False
        strength = max(-(r20 or 0.0), 0.0) + max(-distance_sma200, 0.0)
        return passed, None if passed else "negative_momentum_and_downtrend_not_met", strength
    if signal == "ema50_breakdown":
        passed = features.ema50_breakdown
        return passed, None if passed else "ema50_breakdown_not_present", max(-distance_ema, 0.0)
    raise ValueError(f"unsupported signal {signal!r}")


def _contract_key(
    contract: ContractObservation,
    target_moneyness: float,
    target_dte: int,
) -> tuple[float, int, float, int]:
    return (
        abs(contract.moneyness - target_moneyness),
        abs(contract.dte - target_dte),
        -contract.pre_entry_volume,
        contract.rank,
    )


def select_legs(
    snapshot: DecisionSnapshot,
    spec: CapitalStrategySpec,
) -> tuple[tuple[int, ContractObservation], ...] | None:
    eligible = [row for row in snapshot.contracts if row.liquid_before_entry]
    if not eligible:
        return None

    def shape_valid(
        selected: Sequence[tuple[int, ContractObservation]],
    ) -> bool:
        strikes = [row.strike for _, row in selected]
        quantities = [quantity for quantity, _ in selected]
        if spec.family == "long_call":
            return len(selected) == 1 and quantities == [1]
        if spec.family == "long_put":
            return len(selected) == 1 and quantities == [1]
        if spec.family == "bull_call_spread":
            return len(selected) == 2 and quantities == [1, -1] and strikes[0] < strikes[1]
        if spec.family == "bear_put_spread":
            return len(selected) == 2 and quantities == [1, -1] and strikes[0] > strikes[1]
        if spec.family == "bull_put_spread":
            return len(selected) == 2 and quantities == [-1, 1] and strikes[0] > strikes[1]
        if spec.family == "bear_call_spread":
            return len(selected) == 2 and quantities == [-1, 1] and strikes[0] < strikes[1]
        if spec.family == "call_butterfly":
            return (
                len(selected) == 3
                and quantities == [1, -2, 1]
                and strikes[0] < strikes[1] < strikes[2]
            )
        if spec.family == "put_butterfly":
            return (
                len(selected) == 3
                and quantities == [1, -2, 1]
                and strikes[0] > strikes[1] > strikes[2]
            )
        return False

    ordered_anchors = sorted(
        eligible,
        key=lambda row: _contract_key(
            row,
            spec.legs[0].target_moneyness,
            spec.target_dte,
        ),
    )

    if spec.strike_offsets is not None:
        # Rank complete, executable packages rather than committing to the
        # nearest short strike before checking whether its requested wing is
        # actually listed.  This preserves exact widths while allowing the
        # nearest feasible neighboring anchor.
        for anchor in ordered_anchors:
            same_expiry = [
                row for row in eligible if row.expiration_date == anchor.expiration_date
            ]
            selected: list[tuple[int, ContractObservation]] = []
            used: set[str] = set()
            package_ok = True
            for index, target in enumerate(spec.legs):
                target_strike = anchor.strike + spec.strike_offsets[index]
                candidates = [
                    row
                    for row in same_expiry
                    if row.symbol not in used and abs(row.strike - target_strike) <= 0.01
                ]
                if not candidates:
                    package_ok = False
                    break
                contract = min(
                    candidates,
                    key=lambda row: (
                        -row.pre_entry_volume,
                        abs(row.dte - spec.target_dte),
                        row.rank,
                    ),
                )
                selected.append((target.quantity, contract))
                used.add(contract.symbol)
            if package_ok and shape_valid(selected):
                return tuple(selected)
        return None

    anchor = ordered_anchors[0]
    same_expiry = [row for row in eligible if row.expiration_date == anchor.expiration_date]
    selected = []
    used: set[str] = set()
    for index, target in enumerate(spec.legs):
        if index == 0:
            contract = anchor
        else:
            candidates = [row for row in same_expiry if row.symbol not in used]
            if not candidates:
                return None
            contract = min(
                candidates,
                key=lambda row: _contract_key(
                    row,
                    target.target_moneyness,
                    spec.target_dte,
                ),
            )
        selected.append((target.quantity, contract))
        used.add(contract.symbol)
    return tuple(selected) if shape_valid(selected) else None


def _entry_legs(
    selected: Sequence[tuple[int, ContractObservation]],
    execution: ExecutionAssumption,
) -> tuple[SelectedLeg, ...] | None:
    result: list[SelectedLeg] = []
    for quantity, contract in selected:
        if not contract.has_entry_observation:
            return None
        if quantity < 0:
            price = execution.short_credit(float(contract.entry_low or 0.0))
        else:
            price = execution.long_debit(float(contract.entry_high or 0.0))
        if price is None or price <= 0:
            return None
        result.append(SelectedLeg(quantity, contract, float(price)))
    return tuple(result)


def _entry_cash(legs: Sequence[SelectedLeg]) -> float:
    return sum(-leg.quantity * leg.entry_price for leg in legs)


def _intrinsic(option_type: str, strike: float, spot: float) -> float:
    if option_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def _expiration_payoff(
    option_type: str,
    legs: Sequence[SelectedLeg],
    spot: float,
) -> float:
    return sum(
        leg.quantity * _intrinsic(option_type, leg.contract.strike, spot)
        for leg in legs
    )


def _maximum_loss(
    spec: CapitalStrategySpec,
    legs: Sequence[SelectedLeg],
    entry_cash: float,
    fees: float,
    entry_spot: float,
) -> float:
    strikes = sorted({leg.contract.strike for leg in legs})
    upper = max([entry_spot * 3.0, *(strike * 2.0 for strike in strikes)] or [entry_spot * 3.0])
    critical_spots = [0.0, *strikes, upper]
    net_values = [
        (entry_cash + _expiration_payoff(spec.option_type, legs, spot)) * 100.0 - fees
        for spot in critical_spots
    ]
    maximum_loss = max(0.0, -min(net_values))
    return maximum_loss


def _liquidation_cash(
    spec: CapitalStrategySpec,
    legs: Sequence[SelectedLeg],
    bars: Sequence[PathBar],
    execution: ExecutionAssumption,
) -> float | None:
    if len(legs) != len(bars):
        raise ValueError("legs and bars must have equal lengths")
    cash = 0.0
    for leg, bar in zip(legs, bars):
        if leg.quantity > 0:
            credit = execution.short_credit(bar.low)
            if credit is None:
                return None
            cash += leg.quantity * credit
        else:
            debit = execution.long_debit(bar.high)
            if debit is None:
                return None
            cash -= abs(leg.quantity) * debit

    # Independent leg high/low bars can imply package values that violate
    # elementary no-arbitrage bounds.  Preserve conservative spread crossing,
    # but never let a defined-risk package exceed its expiration payoff range.
    strikes = sorted({leg.contract.strike for leg in legs})
    if spec.family in {
        "bull_call_spread",
        "bear_put_spread",
        "bull_put_spread",
        "bear_call_spread",
        "call_butterfly",
        "put_butterfly",
    }:
        critical_spots = [0.0, *strikes, max(strikes) * 2.0]
        payoffs = [
            _expiration_payoff(spec.option_type, legs, spot)
            for spot in critical_spots
        ]
        return min(max(cash, min(payoffs)), max(payoffs))
    if spec.family == "long_put":
        return min(max(cash, 0.0), legs[0].contract.strike)
    if spec.family == "long_call":
        return max(cash, 0.0)
    return cash


def simulate_candidate(
    dataset: MultiStockDataset,
    spec: CapitalStrategySpec,
    decision_date: date,
    execution: ExecutionAssumption = SEVERE,
) -> tuple[CandidateTrade | None, CandidateSkip | None]:
    snapshot = dataset.snapshot(spec.option_type, decision_date)
    if snapshot is None:
        return None, CandidateSkip(spec.name, dataset.ticker, decision_date, "decision_snapshot_missing")
    features = dataset.features(decision_date)
    passed, signal_reason, signal_strength = signal_passes(spec.signal, features)
    if not passed:
        return None, CandidateSkip(
            spec.name,
            dataset.ticker,
            decision_date,
            signal_reason or "signal_failed",
        )
    selected = select_legs(snapshot, spec)
    if selected is None:
        return None, CandidateSkip(
            spec.name,
            dataset.ticker,
            decision_date,
            "unable_to_select_all_pre_entry_legs",
        )
    legs = _entry_legs(selected, execution)
    if legs is None:
        return None, CandidateSkip(
            spec.name,
            dataset.ticker,
            decision_date,
            "selected_leg_missing_entry_observation_or_price",
        )
    expiration = legs[0].contract.expiration_date
    if any(leg.contract.expiration_date != expiration for leg in legs):
        raise CapitalEfficientStudyError("all legs must share one expiration")
    if dataset.split_events_during(decision_date, expiration):
        return None, CandidateSkip(
            spec.name,
            dataset.ticker,
            decision_date,
            "corporate_action_crosses_contract_life",
        )
    entry_cash = _entry_cash(legs)
    if spec.is_credit and entry_cash <= 0:
        return None, CandidateSkip(
            spec.name,
            dataset.ticker,
            decision_date,
            "credit_structure_not_credit_after_costs",
        )
    if not spec.is_credit and entry_cash >= 0:
        return None, CandidateSkip(
            spec.name,
            dataset.ticker,
            decision_date,
            "debit_structure_not_debit_after_costs",
        )
    fees = execution.lifecycle_fees(spec.contracts_per_position)
    maximum_loss = _maximum_loss(
        spec,
        legs,
        entry_cash,
        fees,
        features.prior_close,
    )
    if maximum_loss <= 0:
        return None, CandidateSkip(
            spec.name,
            dataset.ticker,
            decision_date,
            "maximum_loss_nonpositive_or_arbitrage_like",
        )

    after = datetime.combine(decision_date, time(16, 0), tzinfo=NY).astimezone(UTC)
    synchronized = dataset.synchronized_bars(
        spec.option_type,
        [leg.contract.symbol for leg in legs],
        after=after,
        through=expiration,
    )
    exit_timestamp: datetime | None = None
    exit_reason = "expiration"
    exit_cash = 0.0
    max_adverse = 0.0
    max_favorable = 0.0
    for timestamp, bars in synchronized:
        local = timestamp.astimezone(NY)
        if local.date() <= decision_date:
            continue
        close_cash = _liquidation_cash(spec, legs, bars, execution)
        if close_cash is None:
            continue
        mark_pnl = (entry_cash + close_cash) * 100.0 - fees
        max_adverse = min(max_adverse, mark_pnl)
        max_favorable = max(max_favorable, mark_pnl)
        if spec.is_credit:
            close_debit = -close_cash
            stop_hit = (
                spec.exit_rule.credit_close_multiple is not None
                and close_debit
                >= entry_cash * spec.exit_rule.credit_close_multiple
            )
            target_hit = (
                spec.exit_rule.profit_fraction is not None
                and close_debit
                <= entry_cash * (1.0 - spec.exit_rule.profit_fraction)
            )
            # Conservative ordering: an adverse close wins any same-minute
            # ambiguity created by independent leg high/low bars.
            if stop_hit:
                exit_timestamp = timestamp
                exit_reason = "credit_loss_limit"
                exit_cash = close_cash
                break
            if target_hit:
                exit_timestamp = timestamp
                exit_reason = "profit_target"
                exit_cash = close_cash
                break
        else:
            entry_debit = -entry_cash
            liquidation_credit = close_cash
            stop_hit = (
                spec.exit_rule.loss_fraction is not None
                and liquidation_credit
                <= entry_debit * (1.0 - spec.exit_rule.loss_fraction)
            )
            target_hit = (
                spec.exit_rule.profit_fraction is not None
                and liquidation_credit
                >= entry_debit * (1.0 + spec.exit_rule.profit_fraction)
            )
            # Conservative ordering: a stop wins any same-minute ambiguity.
            if stop_hit:
                exit_timestamp = timestamp
                exit_reason = "loss_limit"
                exit_cash = close_cash
                break
            if target_hit:
                exit_timestamp = timestamp
                exit_reason = "profit_target"
                exit_cash = close_cash
                break
        if (
            spec.exit_rule.exit_dte is not None
            and (expiration - local.date()).days <= spec.exit_rule.exit_dte
            and local.time() >= time(15, 45)
        ):
            exit_timestamp = timestamp
            exit_reason = "time_exit"
            exit_cash = close_cash
            break

    if exit_timestamp is None:
        settlement_date, settlement_spot = dataset.data(spec.option_type).settlement(expiration)
        expiration_payoff = _expiration_payoff(spec.option_type, legs, settlement_spot)
        exit_date = settlement_date
        pnl = (entry_cash + expiration_payoff) * 100.0 - fees
    else:
        expiration_payoff = 0.0
        exit_date = exit_timestamp.astimezone(NY).date()
        pnl = (entry_cash + exit_cash) * 100.0 - fees
    max_adverse = min(max_adverse, pnl, -maximum_loss if not synchronized else max_adverse)
    max_favorable = max(max_favorable, pnl)
    minimum_entry_volume = min(leg.contract.entry_volume for leg in legs)
    return (
        CandidateTrade(
            strategy=spec.name,
            family=spec.family,
            direction=spec.direction,
            signal=spec.signal,
            exit_rule=spec.exit_rule.name,
            ticker=dataset.ticker,
            decision_date=decision_date,
            expiration_date=expiration,
            exit_date=exit_date,
            exit_reason=exit_reason,
            days_held=max(0, (exit_date - decision_date).days),
            leg_symbols=tuple(leg.contract.symbol for leg in legs),
            leg_quantities=tuple(leg.quantity for leg in legs),
            leg_strikes=tuple(leg.contract.strike for leg in legs),
            entry_cash_per_share=entry_cash,
            exit_cash_per_share=exit_cash,
            expiration_payoff_per_share=expiration_payoff,
            fees=fees,
            pnl=pnl,
            maximum_loss=maximum_loss,
            return_on_maximum_loss=pnl / maximum_loss,
            minimum_entry_volume=minimum_entry_volume,
            signal_strength=signal_strength,
            feature_return_5d=features.prior_return_5d,
            feature_return_20d=features.prior_return_20d,
            feature_above_sma_200=features.above_sma_200,
            feature_ema50_reclaim=features.ema50_reclaim,
            feature_ema50_breakdown=features.ema50_breakdown,
            max_adverse_pnl=max_adverse,
            max_favorable_pnl=max_favorable,
            exit_timestamp=_iso(exit_timestamp) if exit_timestamp else None,
        ),
        None,
    )


def run_candidates(
    datasets: Sequence[MultiStockDataset],
    spec: CapitalStrategySpec,
    execution: ExecutionAssumption = SEVERE,
) -> CandidateRun:
    trades: list[CandidateTrade] = []
    skips: list[CandidateSkip] = []
    for dataset in datasets:
        for decision_date in dataset.decision_dates(spec.option_type):
            trade, skip = simulate_candidate(dataset, spec, decision_date, execution)
            if trade is not None:
                trades.append(trade)
            elif skip is not None:
                skips.append(skip)
    trades.sort(key=lambda row: (row.decision_date, row.ticker, row.strategy))
    skips.sort(key=lambda row: (row.decision_date, row.ticker, row.strategy))
    return CandidateRun(spec, tuple(trades), tuple(skips))


def _candidate_priority(trade: CandidateTrade) -> tuple[float, float, float, str]:
    return (
        -trade.signal_strength,
        trade.maximum_loss,
        -trade.minimum_entry_volume,
        trade.ticker,
    )


def _sign_test_pvalue(values: Sequence[float]) -> float | None:
    nonzero = [value for value in values if abs(value) > 1e-12]
    if not nonzero:
        return None
    wins = sum(value > 0 for value in nonzero)
    count = len(nonzero)
    return min(
        1.0,
        sum(math.comb(count, index) for index in range(wins, count + 1)) / (2.0**count),
    )


def replay_portfolio(
    run: CandidateRun,
    starting_equity: float,
    *,
    minimum_entry_volume: float = 0.0,
    maximum_trade_risk_fraction: float = 1.0,
) -> PortfolioResult:
    starting_equity = _finite(starting_equity, name="starting_equity")
    minimum_entry_volume = max(
        0.0,
        _finite(minimum_entry_volume, name="minimum_entry_volume"),
    )
    maximum_trade_risk_fraction = _finite(
        maximum_trade_risk_fraction,
        name="maximum_trade_risk_fraction",
    )
    if starting_equity <= 0:
        raise ValueError("starting_equity must be positive")
    if not 0 < maximum_trade_risk_fraction <= 1.0:
        raise ValueError("maximum_trade_risk_fraction must be in (0, 1]")
    grouped: dict[date, list[CandidateTrade]] = defaultdict(list)
    for trade in run.trades:
        if trade.minimum_entry_volume >= minimum_entry_volume:
            grouped[trade.decision_date].append(trade)

    equity = starting_equity
    high_water = starting_equity
    realized_max_drawdown = 0.0
    observed_mtm_max_drawdown = 0.0
    maximum_single_trade_risk_pct = 0.0
    active: CandidateTrade | None = None
    active_equity_before = 0.0
    portfolio_trades: list[PortfolioTrade] = []
    skipped_open = 0
    skipped_capital = 0
    skipped_risk_cap = 0

    def realize(trade: CandidateTrade, equity_before: float) -> None:
        nonlocal equity, high_water, realized_max_drawdown
        equity_after = equity_before + trade.pnl
        portfolio_trades.append(
            PortfolioTrade(
                strategy=trade.strategy,
                ticker=trade.ticker,
                decision_date=trade.decision_date,
                exit_date=trade.exit_date,
                pnl=trade.pnl,
                maximum_loss=trade.maximum_loss,
                max_adverse_pnl=trade.max_adverse_pnl,
                equity_before=equity_before,
                equity_after=equity_after,
                account_return=trade.pnl / equity_before,
                source_trade=trade,
            )
        )
        equity = equity_after
        high_water = max(high_water, equity)
        if high_water > 0:
            realized_max_drawdown = max(
                realized_max_drawdown,
                (high_water - equity) / high_water,
            )

    for decision_day in sorted(grouped):
        if active is not None and active.exit_date < decision_day:
            realize(active, active_equity_before)
            active = None
        candidates = grouped[decision_day]
        if active is not None:
            skipped_open += len(candidates)
            continue
        capital_feasible = [
            trade for trade in candidates if trade.maximum_loss <= equity
        ]
        skipped_capital += len(candidates) - len(capital_feasible)
        risk_limit = equity * maximum_trade_risk_fraction
        feasible = [
            trade for trade in capital_feasible if trade.maximum_loss <= risk_limit
        ]
        skipped_risk_cap += len(capital_feasible) - len(feasible)
        if not feasible:
            continue
        selected = min(feasible, key=_candidate_priority)
        active = selected
        active_equity_before = equity
        maximum_single_trade_risk_pct = max(
            maximum_single_trade_risk_pct,
            selected.maximum_loss / equity,
        )
        observed_trough = equity + selected.max_adverse_pnl
        if high_water > 0:
            observed_mtm_max_drawdown = max(
                observed_mtm_max_drawdown,
                (high_water - observed_trough) / high_water,
            )
    if active is not None:
        realize(active, active_equity_before)

    pnls = [row.pnl for row in portfolio_trades]
    yearly: dict[str, float] = defaultdict(float)
    ticker_pnl: dict[str, float] = defaultdict(float)
    ticker_trades: dict[str, int] = defaultdict(int)
    for row in portfolio_trades:
        yearly[str(row.exit_date.year)] += row.pnl
        ticker_pnl[row.ticker] += row.pnl
        ticker_trades[row.ticker] += 1
    positive_contributions = [max(value, 0.0) for value in ticker_pnl.values()]
    total_positive = sum(positive_contributions)
    concentration = (
        max(positive_contributions) / total_positive if total_positive > 0 else None
    )
    best_trade = max(pnls) if pnls else None
    promotion_checks: dict[str, bool] = {}
    return PortfolioResult(
        strategy=run.spec.name,
        family=run.spec.family,
        signal=run.spec.signal,
        exit_rule=run.spec.exit_rule.name,
        starting_equity=starting_equity,
        minimum_entry_volume=minimum_entry_volume,
        maximum_trade_risk_fraction=maximum_trade_risk_fraction,
        ending_equity=equity,
        total_pnl=equity - starting_equity,
        trade_count=len(portfolio_trades),
        win_rate=(sum(value > 0 for value in pnls) / len(pnls)) if pnls else None,
        mean_pnl=_mean(pnls),
        median_pnl=_median(pnls),
        worst_trade=min(pnls) if pnls else None,
        best_trade=best_trade,
        pnl_excluding_best_trade=(sum(pnls) - best_trade) if best_trade is not None else None,
        realized_max_drawdown_pct=realized_max_drawdown,
        observed_mtm_max_drawdown_pct=observed_mtm_max_drawdown,
        maximum_single_trade_risk_pct=maximum_single_trade_risk_pct,
        p_value_sign_test=_sign_test_pvalue(pnls),
        adjusted_p_value_holm=None,
        yearly_pnl=dict(sorted(yearly.items())),
        ticker_pnl=dict(sorted(ticker_pnl.items())),
        ticker_trade_count=dict(sorted(ticker_trades.items())),
        positive_ticker_count=sum(value > 0 for value in ticker_pnl.values()),
        positive_pnl_concentration=concentration,
        skipped_while_position_open=skipped_open,
        skipped_for_capital=skipped_capital,
        skipped_for_risk_cap=skipped_risk_cap,
        trades=tuple(portfolio_trades),
        promotion_checks=promotion_checks,
        promoted=False,
    )


def _holm_adjust(results: Sequence[PortfolioResult]) -> tuple[PortfolioResult, ...]:
    indexed = [
        (index, row.p_value_sign_test)
        for index, row in enumerate(results)
        if row.p_value_sign_test is not None
    ]
    ordered = sorted(indexed, key=lambda item: float(item[1]))
    adjusted: dict[int, float] = {}
    running = 0.0
    total = len(ordered)
    for rank, (index, p_value) in enumerate(ordered):
        candidate = min(1.0, float(p_value) * (total - rank))
        running = max(running, candidate)
        adjusted[index] = running
    return tuple(
        replace(row, adjusted_p_value_holm=adjusted.get(index))
        for index, row in enumerate(results)
    )


def _promotion_checks(
    result: PortfolioResult,
    liquidity_result: PortfolioResult | None,
) -> Mapping[str, bool]:
    return {
        "minimum_12_trades": result.trade_count >= 12,
        "positive_total_pnl": result.total_pnl > 0,
        "positive_2025_validation": result.yearly_pnl.get("2025", 0.0) > 0,
        "positive_2026_holdout": result.yearly_pnl.get("2026", 0.0) > 0,
        "positive_after_best_trade_removed": (
            result.pnl_excluding_best_trade is not None
            and result.pnl_excluding_best_trade > 0
        ),
        "observed_mtm_drawdown_at_most_25pct": result.observed_mtm_max_drawdown_pct <= 0.25,
        "maximum_single_trade_risk_at_most_25pct": result.maximum_single_trade_risk_pct <= 0.25,
        "at_least_three_tickers_traded": len(result.ticker_trade_count) >= 3,
        "at_least_two_positive_tickers": result.positive_ticker_count >= 2,
        "positive_pnl_concentration_at_most_70pct": (
            result.positive_pnl_concentration is not None
            and result.positive_pnl_concentration <= 0.70
        ),
        "five_contract_volume_sensitivity_positive": (
            liquidity_result is not None
            and liquidity_result.total_pnl > 0
            and liquidity_result.trade_count >= max(6, math.ceil(result.trade_count * 0.60))
        ),
        "holm_adjusted_sign_test_at_most_10pct": (
            result.adjusted_p_value_holm is not None
            and result.adjusted_p_value_holm <= 0.10
        ),
    }


def apply_promotion_rules(
    base_results: Sequence[PortfolioResult],
    liquidity_results: Mapping[tuple[str, float], PortfolioResult],
) -> tuple[PortfolioResult, ...]:
    adjusted = _holm_adjust(base_results)
    completed: list[PortfolioResult] = []
    for row in adjusted:
        liquidity = liquidity_results.get((row.strategy, row.starting_equity))
        checks = _promotion_checks(row, liquidity)
        completed.append(
            replace(
                row,
                promotion_checks=checks,
                promoted=all(checks.values()),
            )
        )
    return tuple(completed)


def fixed_strategy_specs() -> tuple[CapitalStrategySpec, ...]:
    bullish_signals = (
        "always",
        "momentum20_positive",
        "momentum20_and_trend200",
        "mild_pullback_uptrend",
        "ema50_reclaim",
    )
    bearish_signals = (
        "always",
        "momentum20_negative",
        "momentum20_negative_below_sma200",
        "ema50_breakdown",
    )
    debit_rules = (
        DEBIT_EXPIRY,
        DEBIT_PT50_SL50,
        DEBIT_PT100_SL50,
        DEBIT_DTE7,
    )
    credit_rules = (CREDIT_EXPIRY, CREDIT_PT50, CREDIT_DTE7)
    specs: list[CapitalStrategySpec] = []

    for target in (1.00, 1.02):
        for signal in bullish_signals:
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"long_call_m{int(target*100):03d}_{signal}_{rule.name}",
                        "long_call",
                        "bullish",
                        "call",
                        signal,
                        (LegTarget(1, target),),
                        rule,
                    )
                )
    for lower, upper in ((1.00, 1.05), (1.02, 1.07)):
        for signal in bullish_signals:
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"bull_call_m{int(lower*100):03d}_m{int(upper*100):03d}_{signal}_{rule.name}",
                        "bull_call_spread",
                        "bullish",
                        "call",
                        signal,
                        (LegTarget(1, lower), LegTarget(-1, upper)),
                        rule,
                    )
                )
    for signal in bullish_signals:
        for rule in debit_rules:
            specs.append(
                CapitalStrategySpec(
                    f"call_bfly_m098_m102_m106_{signal}_{rule.name}",
                    "call_butterfly",
                    "bullish",
                    "call",
                    signal,
                    (LegTarget(1, 0.98), LegTarget(-2, 1.02), LegTarget(1, 1.06)),
                    rule,
                )
            )
    for short_target, long_target in ((0.96, 0.92), (0.98, 0.94)):
        for signal in bullish_signals:
            for rule in credit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"bull_put_m{int(short_target*100):03d}_m{int(long_target*100):03d}_{signal}_{rule.name}",
                        "bull_put_spread",
                        "bullish",
                        "put",
                        signal,
                        (LegTarget(-1, short_target), LegTarget(1, long_target)),
                        rule,
                    )
                )
    for target in (0.98, 0.96):
        for signal in bearish_signals:
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"long_put_m{int(target*100):03d}_{signal}_{rule.name}",
                        "long_put",
                        "bearish",
                        "put",
                        signal,
                        (LegTarget(1, target),),
                        rule,
                    )
                )
    for higher, lower in ((0.98, 0.94), (0.96, 0.92)):
        for signal in bearish_signals:
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"bear_put_m{int(higher*100):03d}_m{int(lower*100):03d}_{signal}_{rule.name}",
                        "bear_put_spread",
                        "bearish",
                        "put",
                        signal,
                        (LegTarget(1, higher), LegTarget(-1, lower)),
                        rule,
                    )
                )
    names = [row.name for row in specs]
    if len(names) != len(set(names)):
        raise AssertionError("fixed strategy names must be unique")
    return tuple(specs)


def fixed_width_strategy_specs() -> tuple[CapitalStrategySpec, ...]:
    """Pre-registered fixed-dollar structures for genuinely small accounts.

    Percentage-separated wings can accidentally create $1,000-$5,000 spreads
    on high-priced shares.  These variants require exact listed $1, $2.50, $5,
    or $10 wings and therefore preserve the intended capital constraint.
    """
    bullish_signals = (
        "always",
        "momentum20_positive",
        "momentum20_and_trend200",
        "mild_pullback_uptrend",
        "ema50_reclaim",
    )
    bearish_signals = (
        "always",
        "momentum20_negative",
        "momentum20_negative_below_sma200",
        "ema50_breakdown",
    )
    debit_rules = (
        DEBIT_EXPIRY,
        DEBIT_PT50_SL50,
        DEBIT_PT100_SL50,
        DEBIT_DTE7,
    )
    credit_rules = (CREDIT_EXPIRY, CREDIT_PT50, CREDIT_DTE7)
    widths = (1.0, 2.5, 5.0, 10.0)
    specs: list[CapitalStrategySpec] = []

    for width in widths:
        slug = str(width).replace(".", "p")
        for signal in bullish_signals:
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"fixed_bull_call_w{slug}_{signal}_{rule.name}",
                        "bull_call_spread",
                        "bullish",
                        "call",
                        signal,
                        (LegTarget(1, 1.00), LegTarget(-1, 1.00)),
                        rule,
                        strike_offsets=(0.0, width),
                    )
                )
            for rule in credit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"fixed_bull_put_w{slug}_{signal}_{rule.name}",
                        "bull_put_spread",
                        "bullish",
                        "put",
                        signal,
                        (LegTarget(-1, 0.98), LegTarget(1, 0.98)),
                        rule,
                        strike_offsets=(0.0, -width),
                    )
                )
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"fixed_call_bfly_w{slug}_{signal}_{rule.name}",
                        "call_butterfly",
                        "bullish",
                        "call",
                        signal,
                        (
                            LegTarget(1, 0.98),
                            LegTarget(-2, 0.98),
                            LegTarget(1, 0.98),
                        ),
                        rule,
                        strike_offsets=(0.0, width, 2.0 * width),
                    )
                )

        for signal in bearish_signals:
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"fixed_bear_put_w{slug}_{signal}_{rule.name}",
                        "bear_put_spread",
                        "bearish",
                        "put",
                        signal,
                        (LegTarget(1, 0.98), LegTarget(-1, 0.98)),
                        rule,
                        strike_offsets=(0.0, -width),
                    )
                )
            for rule in credit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"fixed_bear_call_w{slug}_{signal}_{rule.name}",
                        "bear_call_spread",
                        "bearish",
                        "call",
                        signal,
                        (LegTarget(-1, 1.02), LegTarget(1, 1.02)),
                        rule,
                        strike_offsets=(0.0, width),
                    )
                )
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        f"fixed_put_bfly_w{slug}_{signal}_{rule.name}",
                        "put_butterfly",
                        "bearish",
                        "put",
                        signal,
                        (
                            LegTarget(1, 1.02),
                            LegTarget(-2, 1.02),
                            LegTarget(1, 1.02),
                        ),
                        rule,
                        strike_offsets=(0.0, -width, -2.0 * width),
                    )
                )

    names = [row.name for row in specs]
    if len(names) != len(set(names)):
        raise AssertionError("fixed-width strategy names must be unique")
    return tuple(specs)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _ranking_key(result: PortfolioResult) -> tuple[bool, float, int, float]:
    return (
        result.promoted,
        result.total_pnl,
        result.trade_count,
        -result.observed_mtm_max_drawdown_pct,
    )


def _write_outputs(
    output_root: Path,
    report: Mapping[str, Any],
    results: Sequence[PortfolioResult],
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "capital_efficient_multi_stock_report.json"
    json_path.write_text(
        json.dumps(_jsonable(report), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    ranking_path = output_root / "capital_efficient_multi_stock_rankings.csv"
    with ranking_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "starting_equity",
                "strategy",
                "family",
                "signal",
                "exit_rule",
                "trade_count",
                "total_pnl",
                "ending_equity",
                "win_rate",
                "worst_trade",
                "pnl_excluding_best_trade",
                "observed_mtm_max_drawdown_pct",
                "maximum_single_trade_risk_pct",
                "p_value_sign_test",
                "adjusted_p_value_holm",
                "positive_ticker_count",
                "positive_pnl_concentration",
                "promoted",
            ],
        )
        writer.writeheader()
        for row in sorted(results, key=lambda item: (item.starting_equity, *_ranking_key(item)), reverse=True):
            writer.writerow(
                {
                    key: getattr(row, key)
                    for key in writer.fieldnames
                }
            )
    trade_path = output_root / "capital_efficient_multi_stock_trades.csv"
    with trade_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "starting_equity",
            "strategy",
            "ticker",
            "decision_date",
            "exit_date",
            "pnl",
            "maximum_loss",
            "max_adverse_pnl",
            "equity_before",
            "equity_after",
            "account_return",
            "family",
            "signal",
            "exit_rule",
            "leg_symbols",
            "leg_quantities",
            "leg_strikes",
            "minimum_entry_volume",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            for trade in result.trades:
                source = trade.source_trade
                writer.writerow(
                    {
                        "starting_equity": result.starting_equity,
                        "strategy": result.strategy,
                        "ticker": trade.ticker,
                        "decision_date": trade.decision_date.isoformat(),
                        "exit_date": trade.exit_date.isoformat(),
                        "pnl": trade.pnl,
                        "maximum_loss": trade.maximum_loss,
                        "max_adverse_pnl": trade.max_adverse_pnl,
                        "equity_before": trade.equity_before,
                        "equity_after": trade.equity_after,
                        "account_return": trade.account_return,
                        "family": source.family,
                        "signal": source.signal,
                        "exit_rule": source.exit_rule,
                        "leg_symbols": "|".join(source.leg_symbols),
                        "leg_quantities": "|".join(str(value) for value in source.leg_quantities),
                        "leg_strikes": "|".join(str(value) for value in source.leg_strikes),
                        "minimum_entry_volume": source.minimum_entry_volume,
                    }
                )

    lines = [
        "# Capital-Efficient Multi-Stock Options Study",
        "",
        f"**Status:** {report['status']}",
        "",
        "Historical fills are conservative trade-bar approximations, not historical NBBO executions.",
        "",
        "## Frozen protocol",
        "",
        f"- Tickers: {', '.join(report['protocol']['tickers'])}",
        f"- Fixed strategy variants: {report['protocol']['strategy_count']}",
        "- One strategy unit and one open position at a time; multi-leg ratios are preserved",
        "- Starting equity: $250, $500, $1,000, and $2,000",
        "- Severe multi-leg slippage and fees",
        "- Maximum theoretical loss must fit inside current equity",
        "- 2024 discovery, 2025 validation, and 2026 holdout are reported separately",
        "",
        "## Promotion result",
        "",
        f"Promoted variants: {len(report['promoted'])}",
        "",
    ]
    for budget_text, rows in report.get("top_by_budget", {}).items():
        lines.extend(
            [
                f"## Top results — ${float(budget_text):,.0f} starting equity",
                "",
                "| Strategy | Trades | P&L | Worst trade | Observed MTM DD | Holm p | Promoted |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in rows[:15]:
            holm = row.get("adjusted_p_value_holm")
            lines.append(
                "| {strategy} | {trade_count} | ${total_pnl:,.2f} | {worst} | {dd:.1%} | {holm} | {promoted} |".format(
                    strategy=row["strategy"],
                    trade_count=row["trade_count"],
                    total_pnl=row["total_pnl"],
                    worst=(
                        f"${row['worst_trade']:,.2f}"
                        if row.get("worst_trade") is not None
                        else "—"
                    ),
                    dd=row["observed_mtm_max_drawdown_pct"],
                    holm=(f"{holm:.4f}" if holm is not None else "—"),
                    promoted="YES" if row["promoted"] else "No",
                )
            )
        lines.append("")
    lines.extend(
        [
            "## Limitations",
            "",
            "- No historical bid/ask or quote-size series is available in these archives.",
            "- Option-bar high/low execution haircuts are conservative approximations, not guaranteed fills.",
            "- The sample begins in February 2024 and excludes the 2020 crash and 2022 bear market.",
            "- Holm correction is applied to an exact one-sided sign test across the fixed variants for each budget.",
            "- Assignment, exercise, and post-assignment stock inventory are represented only through expiration intrinsic value.",
            "- Observed mark-to-market drawdown uses synchronized option trade bars and can miss adverse periods with no print.",
        ]
    )
    (output_root / "capital_efficient_multi_stock_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_study(
    archive_paths: Sequence[ArchivePaths],
    *,
    output_root: str | Path = DEFAULT_OUTPUT,
    budgets: Sequence[float] = DEFAULT_BUDGETS,
    specs: Sequence[CapitalStrategySpec] | None = None,
    maximum_trade_risk_fraction: float = 1.0,
    execution: ExecutionAssumption = SEVERE,
) -> Mapping[str, Any]:
    audits: list[ArchiveAudit] = []
    for paths in archive_paths:
        audits.append(audit_archive(paths.put_database, paths.ticker, "put"))
        audits.append(audit_archive(paths.call_database, paths.ticker, "call"))
    blockers = [
        f"{row.ticker}:{row.option_type}:{blocker}"
        for row in audits
        for blocker in row.blockers
    ]
    output = Path(output_root)
    protocol_specs = tuple(specs or fixed_strategy_specs())
    protocol = {
        "tickers": [row.ticker for row in archive_paths],
        "budgets": [float(value) for value in budgets],
        "strategy_count": len(protocol_specs),
        "structures": sorted({row.family for row in protocol_specs}),
        "signals": sorted({row.signal for row in protocol_specs}),
        "exit_rules": sorted({row.exit_rule.name for row in protocol_specs}),
        "execution": asdict(execution),
        "position_rules": {
            "strategy_units_per_trade": 1,
            "leg_contract_count": "sum(abs(leg_quantity)) for the selected structure",
            "maximum_open_positions": 1,
            "fractional_options": False,
            "maximum_loss_must_fit_current_equity": True,
            "maximum_trade_risk_fraction": float(maximum_trade_risk_fraction),
        },
        "liquidity_sensitivity_minimum_contracts_per_leg": 5,
        "multiple_testing": "Holm-Bonferroni across fixed strategy variants using exact one-sided trade sign tests, separately by budget",
        "corporate_action_controls": {
            "signal_history": "large mechanically plausible split gaps are backward-adjusted for indicators",
            "contract_life": "any option position crossing a detected split is rejected because OCC deliverable adjustments are unavailable",
        },
    }
    if blockers:
        report = {
            "status": "BLOCKED_ARCHIVE_PREFLIGHT",
            "generated_at": datetime.now(UTC).isoformat(),
            "protocol": protocol,
            "archive_audits": [_jsonable(row) for row in audits],
            "blockers": blockers,
            "promoted": [],
            "top_by_budget": {},
            "limitations": [
                "The strategy simulation did not run because one or more required archives are missing or incomplete."
            ],
        }
        output.mkdir(parents=True, exist_ok=True)
        (output / "capital_efficient_multi_stock_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return report

    datasets = [MultiStockDataset(paths) for paths in archive_paths]
    candidate_runs: dict[str, CandidateRun] = {}
    for spec in protocol_specs:
        candidate_runs[spec.name] = run_candidates(datasets, spec, execution)

    base_results: list[PortfolioResult] = []
    liquidity_results: dict[tuple[str, float], PortfolioResult] = {}
    for budget in budgets:
        for spec in protocol_specs:
            run = candidate_runs[spec.name]
            base_results.append(
                replay_portfolio(
                    run,
                    float(budget),
                    minimum_entry_volume=0.0,
                    maximum_trade_risk_fraction=maximum_trade_risk_fraction,
                )
            )
            liquidity_results[(spec.name, float(budget))] = replay_portfolio(
                run,
                float(budget),
                minimum_entry_volume=5.0,
                maximum_trade_risk_fraction=maximum_trade_risk_fraction,
            )

    completed: list[PortfolioResult] = []
    for budget in budgets:
        budget_rows = [row for row in base_results if row.starting_equity == float(budget)]
        completed.extend(apply_promotion_rules(budget_rows, liquidity_results))

    promoted = [row for row in completed if row.promoted]
    top_by_budget: dict[str, list[Mapping[str, Any]]] = {}
    for budget in budgets:
        rows = sorted(
            [row for row in completed if row.starting_equity == float(budget)],
            key=_ranking_key,
            reverse=True,
        )
        top_by_budget[str(float(budget))] = [
            {
                key: _jsonable(getattr(row, key))
                for key in (
                    "strategy",
                    "family",
                    "signal",
                    "exit_rule",
                    "trade_count",
                    "total_pnl",
                    "ending_equity",
                    "win_rate",
                    "worst_trade",
                    "best_trade",
                    "pnl_excluding_best_trade",
                    "observed_mtm_max_drawdown_pct",
                    "maximum_single_trade_risk_pct",
                    "p_value_sign_test",
                    "adjusted_p_value_holm",
                    "yearly_pnl",
                    "ticker_pnl",
                    "ticker_trade_count",
                    "positive_ticker_count",
                    "positive_pnl_concentration",
                    "promotion_checks",
                    "promoted",
                )
            }
            for row in rows[:50]
        ]

    report = {
        "status": (
            "PROMOTED_CANDIDATE_REQUIRES_NBBO_REPLICATION"
            if promoted
            else "NO_STRATEGY_PROMOTED"
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": protocol,
        "archive_audits": [_jsonable(row) for row in audits],
        "candidate_counts": {
            name: {
                "trades": len(run.trades),
                "skips": len(run.skips),
            }
            for name, run in candidate_runs.items()
        },
        "promoted": [_jsonable(row) for row in promoted],
        "top_by_budget": top_by_budget,
        "limitations": [
            "Historical NBBO and quote size are unavailable; fills are severe trade-bar approximations.",
            "The sample begins in February 2024 and excludes the 2020 crash and 2022 bear market.",
            "A non-promoted result must not be described as a validated strategy edge.",
            "A promoted approximation would still require independent QuantConnect minute-NBBO replication.",
        ],
    }
    _write_outputs(output, report, completed)
    return report


def _load_explicit_config(path: str | Path | None) -> Mapping[str, Mapping[str, str]]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CapitalEfficientStudyError("archive config must be a JSON object")
    normalized: dict[str, dict[str, str]] = {}
    for ticker, value in payload.items():
        if not isinstance(value, dict):
            raise CapitalEfficientStudyError("each ticker config must be an object")
        normalized[str(ticker).upper()] = {
            option_type: str(value[option_type])
            for option_type in ("put", "call")
            if value.get(option_type)
        }
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen low-capital multi-stock historical option study."
    )
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument(
        "--archive-config",
        help="Optional JSON mapping: {TICKER: {put: /path/db, call: /path/db}}",
    )
    parser.add_argument(
        "--budgets",
        default=",".join(str(int(value)) for value in DEFAULT_BUDGETS),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tickers = tuple(
        value.strip().upper() for value in str(args.tickers).split(",") if value.strip()
    )
    budgets = tuple(
        float(value.strip()) for value in str(args.budgets).split(",") if value.strip()
    )
    explicit = _load_explicit_config(args.archive_config)
    paths = resolve_archive_paths(args.archive_root, tickers, explicit)
    report = run_study(paths, output_root=args.output_root, budgets=budgets)
    print(json.dumps(_jsonable(report), indent=2, sort_keys=True))
    return 0 if report["status"] != "BLOCKED_ARCHIVE_PREFLIGHT" else 3


if __name__ == "__main__":
    raise SystemExit(main())
