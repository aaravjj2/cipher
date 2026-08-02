"""Local research lab for Alpaca historical SPY option bars and trades.

The source archive contains observed trades and one-minute trade bars, but no
historical NBBO. This module therefore implements an explicitly conservative
*execution approximation* and never promotes its results to research-grade
quote-based evidence.

Research protocol
-----------------
* Signals use only daily SPY data completed before the decision date.
* Contracts are selected using metadata plus liquidity observed before 15:45 ET.
* A contract selected before 15:45 is not replaced if no later print appears.
* Short entries use the lowest observed trade-bar price from 15:45-16:00,
  reduced by an additional slippage haircut.
* Long entries use the highest observed trade-bar price from 15:45-16:00,
  increased by an additional slippage haircut.
* Positions settle at intrinsic value using the last SPY daily close on or
  before expiration.
* The fixed strategy family is evaluated on 2024 discovery, 2025 validation,
  and 2026 holdout partitions.

This is a read-only research utility. It contains no order-routing code.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")
UTC = timezone.utc
DEFAULT_DB = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "historical_options"
    / "alpaca_spy_monthly_backfill"
    / "historical_options.sqlite"
)
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "historical_options"
    / "strategy_lab"
)


class StrategyLabError(RuntimeError):
    """Raised when historical research inputs violate a hard invariant."""


def _finite(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StrategyLabError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise StrategyLabError(f"{name} must be finite")
    return number


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyLabError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _market_timestamp(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=NY).astimezone(UTC)


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    probability = min(max(float(probability), 0.0), 1.0)
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _safe_mean(values: Sequence[float]) -> float | None:
    return statistics.mean(values) if values else None


def _safe_median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _sample_stdev(values: Sequence[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def _annualized_volatility(closes: Sequence[float], periods: int = 20) -> float | None:
    if len(closes) < periods + 1:
        return None
    returns = [
        math.log(closes[index] / closes[index - 1])
        for index in range(len(closes) - periods, len(closes))
    ]
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(252.0)


def _block_bootstrap_mean_ci(
    values: Sequence[float],
    *,
    block_size: int = 3,
    samples: int = 2_000,
    seed: int = 0,
) -> tuple[float | None, float | None]:
    """Circular moving-block bootstrap CI for the sample mean."""
    normalized = [float(value) for value in values if math.isfinite(float(value))]
    if len(normalized) < 4 or samples <= 0:
        return None, None
    block_size = max(1, min(int(block_size), len(normalized)))
    rng = random.Random(int(seed))
    means: list[float] = []
    for _ in range(int(samples)):
        draw: list[float] = []
        while len(draw) < len(normalized):
            start = rng.randrange(len(normalized))
            for offset in range(block_size):
                draw.append(normalized[(start + offset) % len(normalized)])
                if len(draw) == len(normalized):
                    break
        means.append(statistics.mean(draw))
    return _percentile(means, 0.025), _percentile(means, 0.975)


@dataclass(frozen=True, slots=True)
class ExecutionAssumption:
    name: str
    slippage_fraction: float
    slippage_floor: float
    fee_per_leg_side: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("execution assumption name is required")
        for field_name in ("slippage_fraction", "slippage_floor", "fee_per_leg_side"):
            value = _finite(getattr(self, field_name), name=field_name)
            if value < 0:
                raise ValueError(f"{field_name} cannot be negative")
            object.__setattr__(self, field_name, value)

    def short_credit(self, observed_low: float) -> float | None:
        observed_low = _finite(observed_low, name="observed_low")
        if observed_low <= 0:
            return None
        haircut = max(self.slippage_floor, observed_low * self.slippage_fraction)
        credit = observed_low - haircut
        return credit if credit > 0 else None

    def long_debit(self, observed_high: float) -> float | None:
        observed_high = _finite(observed_high, name="observed_high")
        if observed_high <= 0:
            return None
        haircut = max(self.slippage_floor, observed_high * self.slippage_fraction)
        return observed_high + haircut

    def lifecycle_fees(self, leg_count: int) -> float:
        if not isinstance(leg_count, int) or isinstance(leg_count, bool) or leg_count <= 0:
            raise ValueError("leg_count must be a positive integer")
        return 2.0 * self.fee_per_leg_side * leg_count


EXECUTION_ASSUMPTIONS: tuple[ExecutionAssumption, ...] = (
    ExecutionAssumption("base", 0.05, 0.05, 0.75),
    ExecutionAssumption("worse", 0.10, 0.10, 1.00),
    ExecutionAssumption("severe", 0.20, 0.15, 1.25),
)


@dataclass(frozen=True, slots=True)
class SignalFeatures:
    decision_date: date
    prior_close: float
    return_5d: float | None
    return_20d: float | None
    realized_volatility_20d: float | None
    realized_volatility_percentile: float | None
    sma_200: float | None
    above_sma_200: bool | None
    drawdown_20d: float | None
    history_rows: int


@dataclass(frozen=True, slots=True)
class ContractObservation:
    symbol: str
    expiration_date: date
    strike: float
    moneyness: float
    dte: int
    rank: int
    pre_entry_bar_count: int
    pre_entry_volume: float
    entry_bar_count: int
    entry_volume: float
    entry_low: float | None
    entry_high: float | None
    entry_first_timestamp: str | None
    entry_last_timestamp: str | None

    @property
    def liquid_before_entry(self) -> bool:
        return self.pre_entry_bar_count > 0 and self.pre_entry_volume > 0

    @property
    def has_entry_observation(self) -> bool:
        return (
            self.entry_bar_count > 0
            and self.entry_low is not None
            and self.entry_high is not None
            and self.entry_low > 0
            and self.entry_high >= self.entry_low
        )


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    decision_date: date
    features: SignalFeatures
    contracts: tuple[ContractObservation, ...]


@dataclass(frozen=True, slots=True)
class StrategySpec:
    name: str
    structure: str
    signal: str
    short_target_moneyness: float
    long_target_moneyness: float | None = None
    target_dte: int = 35

    def __post_init__(self) -> None:
        if self.structure not in {"csp", "vertical"}:
            raise ValueError("structure must be csp or vertical")
        if self.signal not in {
            "always",
            "momentum20_positive",
            "trend200",
            "momentum20_and_trend200",
            "mild_pullback_uptrend",
            "elevated_rv_momentum",
        }:
            raise ValueError(f"unsupported signal {self.signal!r}")
        short = _finite(self.short_target_moneyness, name="short_target_moneyness")
        if not 0 < short < 1.25:
            raise ValueError("invalid short target moneyness")
        object.__setattr__(self, "short_target_moneyness", short)
        if self.structure == "vertical":
            if self.long_target_moneyness is None:
                raise ValueError("vertical requires long_target_moneyness")
            long = _finite(self.long_target_moneyness, name="long_target_moneyness")
            if not 0 < long < short:
                raise ValueError("long target moneyness must be below short target")
            object.__setattr__(self, "long_target_moneyness", long)
        elif self.long_target_moneyness is not None:
            raise ValueError("cash-secured put cannot have a long leg target")


@dataclass(frozen=True, slots=True)
class TradeResult:
    strategy: str
    execution_assumption: str
    decision_date: date
    expiration_date: date
    settlement_date: date
    signal: str
    structure: str
    short_symbol: str
    short_strike: float
    short_moneyness: float
    long_symbol: str | None
    long_strike: float | None
    raw_short_price: float
    short_credit: float
    raw_long_price: float | None
    long_debit: float | None
    net_credit: float
    settlement_spot: float
    intrinsic_loss: float
    fees: float
    pnl: float
    risk_capital: float
    return_on_risk: float
    pre_entry_short_bars: int
    entry_short_bars: int
    pre_entry_long_bars: int | None
    entry_long_bars: int | None
    entry_first_timestamp: str | None
    entry_last_timestamp: str | None
    feature_return_20d: float | None
    feature_above_sma_200: bool | None
    feature_rv_percentile: float | None


@dataclass(frozen=True, slots=True)
class SkipResult:
    strategy: str
    execution_assumption: str
    decision_date: date
    reason: str


@dataclass(frozen=True, slots=True)
class StrategyRun:
    spec: StrategySpec
    execution: ExecutionAssumption
    trades: tuple[TradeResult, ...]
    skips: tuple[SkipResult, ...]


class HistoricalOptionResearchDataset:
    """Read optimized decision snapshots from the immutable SQLite archive."""

    def __init__(
        self,
        database_path: str | Path = DEFAULT_DB,
        *,
        underlying_symbol: str = "SPY",
        entry_time: time = time(15, 45),
        entry_window_minutes: int = 15,
    ):
        self.database_path = Path(database_path)
        if not self.database_path.exists():
            raise StrategyLabError(f"database not found: {self.database_path}")
        normalized_symbol = str(underlying_symbol).strip().upper()
        if not normalized_symbol or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for character in normalized_symbol
        ):
            raise ValueError("underlying_symbol must be a valid market symbol")
        self.underlying_symbol = normalized_symbol
        self.entry_time = entry_time
        self.entry_window_minutes = int(entry_window_minutes)
        if self.entry_window_minutes <= 0 or self.entry_window_minutes > 60:
            raise ValueError("entry_window_minutes must be in 1..60")
        self.daily_closes = self._load_daily_closes()
        self._daily_index = {day: index for index, (day, _) in enumerate(self.daily_closes)}
        self.snapshots = self._load_snapshots()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _load_daily_closes(self) -> tuple[tuple[date, float], ...]:
        rows: dict[date, float] = {}
        with self.connect() as db:
            for row in db.execute(
                """select timestamp,close from underlying_bars
                   where symbol=? and timeframe='1Day' and close is not null
                   order by timestamp""",
                (self.underlying_symbol,),
            ):
                day = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")).date()
                close = _finite(row["close"], name="daily close")
                if close > 0:
                    rows[day] = close
        ordered = tuple(sorted(rows.items()))
        if len(ordered) < 250:
            raise StrategyLabError(
                f"at least 250 {self.underlying_symbol} daily closes are required"
            )
        return ordered

    def _features_for(self, decision_day: date, prior_close_hint: float) -> SignalFeatures:
        index = self._daily_index.get(decision_day)
        if index is None:
            raise StrategyLabError(
                f"missing {self.underlying_symbol} daily bar on decision date {decision_day}"
            )
        history = [value for _, value in self.daily_closes[:index]]
        if not history:
            raise StrategyLabError(f"missing prior close for decision date {decision_day}")
        prior_close = history[-1]
        if abs(prior_close - prior_close_hint) / prior_close > 0.02:
            raise StrategyLabError(
                "selection spot differs materially from prior "
                f"{self.underlying_symbol} close on {decision_day}"
            )
        return_5d = history[-1] / history[-6] - 1.0 if len(history) >= 6 else None
        return_20d = history[-1] / history[-21] - 1.0 if len(history) >= 21 else None
        rv20 = _annualized_volatility(history, 20)
        sma200 = statistics.mean(history[-200:]) if len(history) >= 200 else None
        above200 = prior_close > sma200 if sma200 is not None else None
        drawdown20 = (
            prior_close / max(history[-20:]) - 1.0 if len(history) >= 20 else None
        )

        rv_percentile = None
        if rv20 is not None and len(history) >= 80:
            starting = max(21, len(history) - 272)
            historical_rv: list[float] = []
            for end_index in range(starting, len(history) + 1):
                value = _annualized_volatility(history[:end_index], 20)
                if value is not None:
                    historical_rv.append(value)
            if historical_rv:
                rv_percentile = sum(value <= rv20 for value in historical_rv) / len(
                    historical_rv
                )

        return SignalFeatures(
            decision_date=decision_day,
            prior_close=prior_close,
            return_5d=return_5d,
            return_20d=return_20d,
            realized_volatility_20d=rv20,
            realized_volatility_percentile=rv_percentile,
            sma_200=sma200,
            above_sma_200=above200,
            drawdown_20d=drawdown20,
            history_rows=len(history),
        )

    def _load_snapshots(self) -> tuple[DecisionSnapshot, ...]:
        with self.connect() as db:
            selection_rows = db.execute(
                """select s.decision_date,s.symbol,s.expiration_date,s.strike,s.spot,
                          s.dte,s.moneyness,s.rank
                   from decision_selections s
                   join selection_observation_audit a
                     on a.decision_date=s.decision_date and a.symbol=s.symbol
                   where a.observed_on_decision=1
                   order by s.decision_date,s.rank"""
            ).fetchall()

            grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
            for row in selection_rows:
                grouped[str(row["decision_date"])].append(row)

            snapshots: list[DecisionSnapshot] = []
            for decision_text in sorted(grouped):
                decision_day = date.fromisoformat(decision_text)
                rows = grouped[decision_text]
                symbols = [str(row["symbol"]) for row in rows]
                market_open = _utc_text(_market_timestamp(decision_day, time(9, 30)))
                entry_start = _market_timestamp(decision_day, self.entry_time)
                entry_end = entry_start.timestamp() + self.entry_window_minutes * 60
                entry_end_dt = datetime.fromtimestamp(entry_end, tz=UTC)
                entry_start_text = _utc_text(entry_start)
                entry_end_text = _utc_text(entry_end_dt)

                placeholders = ",".join("?" for _ in symbols)
                bar_rows = db.execute(
                    f"""select symbol,timestamp,low,high,volume
                        from option_bars
                        where symbol in ({placeholders})
                          and timestamp>=? and timestamp<=?
                        order by timestamp""",
                    (*symbols, market_open, entry_end_text),
                ).fetchall()
                bars_by_symbol: dict[str, list[sqlite3.Row]] = defaultdict(list)
                for bar in bar_rows:
                    bars_by_symbol[str(bar["symbol"])].append(bar)

                observations: list[ContractObservation] = []
                for row in rows:
                    symbol = str(row["symbol"])
                    pre_bars = []
                    entry_bars = []
                    for bar in bars_by_symbol.get(symbol, []):
                        timestamp = str(bar["timestamp"])
                        if timestamp < entry_start_text:
                            pre_bars.append(bar)
                        else:
                            entry_bars.append(bar)
                    entry_lows = [
                        float(bar["low"])
                        for bar in entry_bars
                        if bar["low"] is not None and float(bar["low"]) > 0
                    ]
                    entry_highs = [
                        float(bar["high"])
                        for bar in entry_bars
                        if bar["high"] is not None and float(bar["high"]) > 0
                    ]
                    observations.append(
                        ContractObservation(
                            symbol=symbol,
                            expiration_date=date.fromisoformat(
                                str(row["expiration_date"])
                            ),
                            strike=_finite(row["strike"], name="strike"),
                            moneyness=_finite(row["moneyness"], name="moneyness"),
                            dte=int(row["dte"]),
                            rank=int(row["rank"]),
                            pre_entry_bar_count=len(pre_bars),
                            pre_entry_volume=sum(
                                float(bar["volume"] or 0.0) for bar in pre_bars
                            ),
                            entry_bar_count=len(entry_bars),
                            entry_volume=sum(
                                float(bar["volume"] or 0.0) for bar in entry_bars
                            ),
                            entry_low=min(entry_lows) if entry_lows else None,
                            entry_high=max(entry_highs) if entry_highs else None,
                            entry_first_timestamp=(
                                str(entry_bars[0]["timestamp"]) if entry_bars else None
                            ),
                            entry_last_timestamp=(
                                str(entry_bars[-1]["timestamp"]) if entry_bars else None
                            ),
                        )
                    )

                features = self._features_for(
                    decision_day,
                    prior_close_hint=float(rows[0]["spot"]),
                )
                snapshots.append(
                    DecisionSnapshot(
                        decision_date=decision_day,
                        features=features,
                        contracts=tuple(observations),
                    )
                )
        if not snapshots:
            raise StrategyLabError("no point-in-time decision snapshots were loaded")
        return tuple(snapshots)

    def settlement(self, expiration_date: date) -> tuple[date, float]:
        candidates = [row for row in self.daily_closes if row[0] <= expiration_date]
        if not candidates:
            raise StrategyLabError(
                f"no {self.underlying_symbol} settlement close for {expiration_date}"
            )
        settlement_date, close = candidates[-1]
        if (expiration_date - settlement_date).days > 4:
            raise StrategyLabError(
                f"settlement close is too stale for expiration {expiration_date}"
            )
        return settlement_date, close


SIGNAL_DESCRIPTIONS: Mapping[str, str] = {
    "always": "Trade every eligible monthly decision.",
    "momentum20_positive": "Trade only when the prior 20-session SPY return is positive.",
    "trend200": "Trade only when prior SPY close is above its trailing 200-session average.",
    "momentum20_and_trend200": "Require both positive 20-session momentum and the 200-session trend filter.",
    "mild_pullback_uptrend": "Require the 200-session uptrend and a prior 5-session return between -5% and 0%.",
    "elevated_rv_momentum": "Require positive 20-session momentum and trailing 20-session realized volatility at or above its 60th percentile.",
}


def signal_passes(signal: str, features: SignalFeatures) -> tuple[bool, str | None]:
    if signal == "always":
        return True, None
    if signal == "momentum20_positive":
        if features.return_20d is None:
            return False, "return_20d_unavailable"
        return (features.return_20d > 0), (
            None if features.return_20d > 0 else "return_20d_not_positive"
        )
    if signal == "trend200":
        if features.above_sma_200 is None:
            return False, "sma_200_unavailable"
        return features.above_sma_200, (
            None if features.above_sma_200 else "below_sma_200"
        )
    if signal == "momentum20_and_trend200":
        if features.return_20d is None or features.above_sma_200 is None:
            return False, "momentum_or_trend_unavailable"
        passed = features.return_20d > 0 and features.above_sma_200
        return passed, None if passed else "momentum_or_trend_failed"
    if signal == "mild_pullback_uptrend":
        if features.return_5d is None or features.above_sma_200 is None:
            return False, "pullback_or_trend_unavailable"
        passed = features.above_sma_200 and -0.05 <= features.return_5d < 0
        return passed, None if passed else "mild_pullback_uptrend_failed"
    if signal == "elevated_rv_momentum":
        if (
            features.return_20d is None
            or features.realized_volatility_percentile is None
        ):
            return False, "momentum_or_rv_unavailable"
        passed = (
            features.return_20d > 0
            and features.realized_volatility_percentile >= 0.60
        )
        return passed, None if passed else "elevated_rv_momentum_failed"
    raise StrategyLabError(f"unsupported signal {signal!r}")


def _contract_sort_key(
    contract: ContractObservation,
    *,
    target_moneyness: float,
    target_dte: int,
) -> tuple[float, int, float, int]:
    return (
        abs(contract.moneyness - target_moneyness),
        abs(contract.dte - target_dte),
        -contract.pre_entry_volume,
        contract.rank,
    )


def select_short_contract(
    snapshot: DecisionSnapshot,
    spec: StrategySpec,
) -> ContractObservation | None:
    candidates = [
        contract for contract in snapshot.contracts if contract.liquid_before_entry
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda contract: _contract_sort_key(
            contract,
            target_moneyness=spec.short_target_moneyness,
            target_dte=spec.target_dte,
        ),
    )


def select_long_contract(
    snapshot: DecisionSnapshot,
    spec: StrategySpec,
    short_contract: ContractObservation,
) -> ContractObservation | None:
    if spec.long_target_moneyness is None:
        return None
    candidates = [
        contract
        for contract in snapshot.contracts
        if contract.liquid_before_entry
        and contract.expiration_date == short_contract.expiration_date
        and contract.strike < short_contract.strike
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda contract: _contract_sort_key(
            contract,
            target_moneyness=spec.long_target_moneyness or 0.0,
            target_dte=spec.target_dte,
        ),
    )


def simulate_strategy(
    dataset: HistoricalOptionResearchDataset,
    spec: StrategySpec,
    execution: ExecutionAssumption,
) -> StrategyRun:
    trades: list[TradeResult] = []
    skips: list[SkipResult] = []
    for snapshot in dataset.snapshots:
        passed, signal_reason = signal_passes(spec.signal, snapshot.features)
        if not passed:
            skips.append(
                SkipResult(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    signal_reason or "signal_failed",
                )
            )
            continue

        short = select_short_contract(snapshot, spec)
        if short is None:
            skips.append(
                SkipResult(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "no_pre_entry_liquid_short_contract",
                )
            )
            continue
        # Deliberately do not choose a different contract after checking the
        # post-entry window. That would introduce fill-availability look-ahead.
        if not short.has_entry_observation:
            skips.append(
                SkipResult(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "selected_short_has_no_post_entry_observation",
                )
            )
            continue

        raw_short = float(short.entry_low or 0.0)
        short_credit = execution.short_credit(raw_short)
        if short_credit is None:
            skips.append(
                SkipResult(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "short_credit_nonpositive_after_costs",
                )
            )
            continue

        long_contract: ContractObservation | None = None
        raw_long: float | None = None
        long_debit: float | None = None
        if spec.structure == "vertical":
            long_contract = select_long_contract(snapshot, spec, short)
            if long_contract is None:
                skips.append(
                    SkipResult(
                        spec.name,
                        execution.name,
                        snapshot.decision_date,
                        "no_pre_entry_liquid_long_contract",
                    )
                )
                continue
            if not long_contract.has_entry_observation:
                skips.append(
                    SkipResult(
                        spec.name,
                        execution.name,
                        snapshot.decision_date,
                        "selected_long_has_no_post_entry_observation",
                    )
                )
                continue
            raw_long = float(long_contract.entry_high or 0.0)
            long_debit = execution.long_debit(raw_long)
            if long_debit is None:
                skips.append(
                    SkipResult(
                        spec.name,
                        execution.name,
                        snapshot.decision_date,
                        "long_debit_unavailable",
                    )
                )
                continue

        settlement_date, settlement_spot = dataset.settlement(short.expiration_date)
        short_intrinsic = max(short.strike - settlement_spot, 0.0)
        fees = execution.lifecycle_fees(1 if spec.structure == "csp" else 2)
        if spec.structure == "csp":
            net_credit = short_credit
            intrinsic_loss = short_intrinsic
            risk_capital = short.strike * 100.0 - net_credit * 100.0
        else:
            assert long_contract is not None and long_debit is not None
            net_credit = short_credit - long_debit
            if net_credit <= 0:
                skips.append(
                    SkipResult(
                        spec.name,
                        execution.name,
                        snapshot.decision_date,
                        "vertical_not_a_credit_after_costs",
                    )
                )
                continue
            long_intrinsic = max(long_contract.strike - settlement_spot, 0.0)
            intrinsic_loss = short_intrinsic - long_intrinsic
            width = short.strike - long_contract.strike
            risk_capital = width * 100.0 - net_credit * 100.0

        if risk_capital <= 0:
            skips.append(
                SkipResult(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "risk_capital_nonpositive",
                )
            )
            continue
        pnl = net_credit * 100.0 - intrinsic_loss * 100.0 - fees
        trades.append(
            TradeResult(
                strategy=spec.name,
                execution_assumption=execution.name,
                decision_date=snapshot.decision_date,
                expiration_date=short.expiration_date,
                settlement_date=settlement_date,
                signal=spec.signal,
                structure=spec.structure,
                short_symbol=short.symbol,
                short_strike=short.strike,
                short_moneyness=short.moneyness,
                long_symbol=long_contract.symbol if long_contract else None,
                long_strike=long_contract.strike if long_contract else None,
                raw_short_price=raw_short,
                short_credit=short_credit,
                raw_long_price=raw_long,
                long_debit=long_debit,
                net_credit=net_credit,
                settlement_spot=settlement_spot,
                intrinsic_loss=intrinsic_loss,
                fees=fees,
                pnl=pnl,
                risk_capital=risk_capital,
                return_on_risk=pnl / risk_capital,
                pre_entry_short_bars=short.pre_entry_bar_count,
                entry_short_bars=short.entry_bar_count,
                pre_entry_long_bars=(
                    long_contract.pre_entry_bar_count if long_contract else None
                ),
                entry_long_bars=(
                    long_contract.entry_bar_count if long_contract else None
                ),
                entry_first_timestamp=short.entry_first_timestamp,
                entry_last_timestamp=short.entry_last_timestamp,
                feature_return_20d=snapshot.features.return_20d,
                feature_above_sma_200=snapshot.features.above_sma_200,
                feature_rv_percentile=(
                    snapshot.features.realized_volatility_percentile
                ),
            )
        )
    return StrategyRun(spec, execution, tuple(trades), tuple(skips))


def _max_drawdown_from_pnl(trades: Sequence[TradeResult]) -> float:
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in sorted(trades, key=lambda row: row.decision_date):
        running += trade.pnl
        peak = max(peak, running)
        max_drawdown = min(max_drawdown, running - peak)
    return max_drawdown


def summarize_trades(
    trades: Sequence[TradeResult],
    *,
    bootstrap_seed: int = 0,
) -> dict[str, Any]:
    ordered = tuple(sorted(trades, key=lambda row: row.decision_date))
    pnls = [row.pnl for row in ordered]
    returns = [row.return_on_risk for row in ordered]
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    ci_low, ci_high = _block_bootstrap_mean_ci(
        returns,
        seed=bootstrap_seed,
    )
    by_year: dict[str, dict[str, Any]] = {}
    for year in sorted({row.decision_date.year for row in ordered}):
        subset = [row for row in ordered if row.decision_date.year == year]
        by_year[str(year)] = {
            "trades": len(subset),
            "total_pnl": sum(row.pnl for row in subset),
            "mean_return_on_risk": _safe_mean(
                [row.return_on_risk for row in subset]
            ),
            "win_rate": (
                sum(row.pnl > 0 for row in subset) / len(subset) if subset else None
            ),
        }
    result = {
        "trades": len(ordered),
        "first_decision_date": (
            ordered[0].decision_date.isoformat() if ordered else None
        ),
        "last_decision_date": (
            ordered[-1].decision_date.isoformat() if ordered else None
        ),
        "total_pnl": sum(pnls),
        "mean_pnl": _safe_mean(pnls),
        "median_pnl": _safe_median(pnls),
        "mean_return_on_risk": _safe_mean(returns),
        "median_return_on_risk": _safe_median(returns),
        "return_on_risk_stdev": _sample_stdev(returns),
        "block_bootstrap_mean_return_ci_95": [ci_low, ci_high],
        "win_rate": (
            sum(value > 0 for value in pnls) / len(pnls) if pnls else None
        ),
        "profit_factor": gains / losses if losses > 0 else ("Infinity" if gains > 0 else None),
        "best_trade_pnl": max(pnls) if pnls else None,
        "worst_trade_pnl": min(pnls) if pnls else None,
        "max_drawdown_pnl": _max_drawdown_from_pnl(ordered),
        "total_risk_capital": sum(row.risk_capital for row in ordered),
        "average_risk_capital": _safe_mean(
            [row.risk_capital for row in ordered]
        ),
        "by_year": by_year,
    }
    if len(pnls) >= 2:
        best_index = max(range(len(pnls)), key=pnls.__getitem__)
        worst_index = min(range(len(pnls)), key=pnls.__getitem__)
        result["total_pnl_excluding_best_trade"] = sum(
            value for index, value in enumerate(pnls) if index != best_index
        )
        result["total_pnl_excluding_worst_trade"] = sum(
            value for index, value in enumerate(pnls) if index != worst_index
        )
    else:
        result["total_pnl_excluding_best_trade"] = None
        result["total_pnl_excluding_worst_trade"] = None
    return result


def summarize_partition(
    run: StrategyRun,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    trades = [
        trade
        for trade in run.trades
        if start_year <= trade.decision_date.year <= end_year
    ]
    digest = hashlib.sha256(
        f"{run.spec.name}:{run.execution.name}:{start_year}:{end_year}".encode()
    ).hexdigest()
    return summarize_trades(trades, bootstrap_seed=int(digest[:8], 16))


def _month_window_start(latest: date, months: int) -> date:
    if months <= 0:
        raise ValueError("months must be positive")
    month_index = latest.year * 12 + latest.month - 1 - (months - 1)
    return date(month_index // 12, month_index % 12 + 1, 1)


def _exposure_summary(trades: Sequence[TradeResult]) -> dict[str, Any]:
    ordered = tuple(sorted(trades, key=lambda row: row.decision_date))
    if not ordered:
        return {
            "average_raw_entry_value": None,
            "average_net_credit_before_fees": None,
            "average_fees": None,
            "average_pnl": None,
            "average_risk_capital": None,
            "minimum_risk_capital": None,
            "maximum_single_position_risk_capital": None,
            "average_days_held": None,
            "max_concurrent_positions": 0,
            "max_concurrent_option_contracts": 0,
            "peak_combined_risk_capital": 0.0,
            "total_pnl_on_peak_risk_capital": None,
        }

    events: list[tuple[date, int, int, float]] = []
    for trade in ordered:
        leg_count = 2 if trade.structure == "vertical" else 1
        events.append((trade.decision_date, 0, leg_count, trade.risk_capital))
        events.append((trade.expiration_date, 1, -leg_count, -trade.risk_capital))

    active_positions = 0
    active_contracts = 0
    active_risk = 0.0
    max_positions = 0
    max_contracts = 0
    peak_risk = 0.0
    for _event_date, event_order, contract_delta, risk_delta in sorted(events):
        if event_order == 0:
            active_positions += 1
        else:
            active_positions -= 1
        active_contracts += contract_delta
        active_risk += risk_delta
        max_positions = max(max_positions, active_positions)
        max_contracts = max(max_contracts, active_contracts)
        peak_risk = max(peak_risk, active_risk)

    raw_entry_values = []
    for trade in ordered:
        raw_value = trade.raw_short_price * 100.0
        if trade.raw_long_price is not None:
            raw_value -= trade.raw_long_price * 100.0
        raw_entry_values.append(raw_value)

    total_pnl = sum(trade.pnl for trade in ordered)
    return {
        "average_raw_entry_value": _safe_mean(raw_entry_values),
        "average_net_credit_before_fees": _safe_mean(
            [trade.net_credit * 100.0 for trade in ordered]
        ),
        "average_fees": _safe_mean([trade.fees for trade in ordered]),
        "average_pnl": _safe_mean([trade.pnl for trade in ordered]),
        "average_risk_capital": _safe_mean(
            [trade.risk_capital for trade in ordered]
        ),
        "minimum_risk_capital": min(trade.risk_capital for trade in ordered),
        "maximum_single_position_risk_capital": max(
            trade.risk_capital for trade in ordered
        ),
        "average_days_held": _safe_mean(
            [(trade.expiration_date - trade.decision_date).days for trade in ordered]
        ),
        "max_concurrent_positions": max_positions,
        "max_concurrent_option_contracts": max_contracts,
        "peak_combined_risk_capital": peak_risk,
        "total_pnl_on_peak_risk_capital": (
            total_pnl / peak_risk if peak_risk > 0 else None
        ),
    }


def summarize_date_window(
    run: StrategyRun,
    start_date: date,
    end_date: date,
    *,
    decision_date_count: int,
) -> dict[str, Any]:
    trades = [
        trade
        for trade in run.trades
        if start_date <= trade.decision_date <= end_date
    ]
    skips = [
        skip
        for skip in run.skips
        if start_date <= skip.decision_date <= end_date
    ]
    digest = hashlib.sha256(
        f"{run.spec.name}:{run.execution.name}:{start_date}:{end_date}".encode()
    ).hexdigest()
    result = summarize_trades(trades, bootstrap_seed=int(digest[:8], 16))
    result["window_start"] = start_date.isoformat()
    result["window_end"] = end_date.isoformat()
    result["decision_dates"] = decision_date_count
    result["trade_frequency"] = (
        len(trades) / decision_date_count if decision_date_count else None
    )
    result["skip_reasons"] = dict(
        sorted(
            {
                reason: sum(skip.reason == reason for skip in skips)
                for reason in {skip.reason for skip in skips}
            }.items()
        )
    )
    result["exposure"] = _exposure_summary(trades)
    return result


def fixed_strategy_specs() -> tuple[StrategySpec, ...]:
    signals = (
        "always",
        "momentum20_positive",
        "trend200",
        "momentum20_and_trend200",
        "mild_pullback_uptrend",
        "elevated_rv_momentum",
    )
    specs: list[StrategySpec] = []
    for target in (0.90, 0.92, 0.94, 0.96, 0.98):
        for signal in signals:
            specs.append(
                StrategySpec(
                    name=f"csp_m{int(target * 100):02d}_{signal}",
                    structure="csp",
                    signal=signal,
                    short_target_moneyness=target,
                )
            )
    for short_target in (0.94, 0.96, 0.98):
        for signal in (
            "always",
            "momentum20_positive",
            "momentum20_and_trend200",
        ):
            specs.append(
                StrategySpec(
                    name=(
                        f"vertical_m{int(short_target * 100):02d}_m90_{signal}"
                    ),
                    structure="vertical",
                    signal=signal,
                    short_target_moneyness=short_target,
                    long_target_moneyness=0.90,
                )
            )
    return tuple(specs)


def _trade_path_hash(trades: Sequence[TradeResult], *, years: set[int] | None = None) -> str:
    rows = [
        (
            trade.decision_date.isoformat(),
            trade.short_symbol,
            trade.long_symbol or "",
            round(trade.short_moneyness, 8),
        )
        for trade in sorted(trades, key=lambda row: row.decision_date)
        if years is None or trade.decision_date.year in years
    ]
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _period_pass(summary: Mapping[str, Any], minimum_trades: int) -> bool:
    return (
        int(summary.get("trades") or 0) >= minimum_trades
        and float(summary.get("total_pnl") or 0.0) > 0
        and float(summary.get("mean_return_on_risk") or 0.0) > 0
    )


def run_strategy_lab(
    database_path: str | Path = DEFAULT_DB,
    *,
    output_directory: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    dataset = HistoricalOptionResearchDataset(database_path)
    specs = fixed_strategy_specs()
    runs: dict[tuple[str, str], StrategyRun] = {}
    rows: list[dict[str, Any]] = []
    for spec in specs:
        for execution in EXECUTION_ASSUMPTIONS:
            run = simulate_strategy(dataset, spec, execution)
            runs[(spec.name, execution.name)] = run
            full = summarize_partition(run, 2024, 2026)
            discovery = summarize_partition(run, 2024, 2024)
            validation = summarize_partition(run, 2025, 2025)
            holdout = summarize_partition(run, 2026, 2026)
            rows.append(
                {
                    "strategy": spec.name,
                    "structure": spec.structure,
                    "signal": spec.signal,
                    "short_target_moneyness": spec.short_target_moneyness,
                    "long_target_moneyness": spec.long_target_moneyness,
                    "execution_assumption": execution.name,
                    "full": full,
                    "discovery_2024": discovery,
                    "validation_2025": validation,
                    "holdout_2026": holdout,
                    "skip_reasons": dict(
                        sorted(
                            {
                                reason: sum(
                                    skip.reason == reason for skip in run.skips
                                )
                                for reason in {skip.reason for skip in run.skips}
                            }.items()
                        )
                    ),
                }
            )

    severe_rows = [row for row in rows if row["execution_assumption"] == "severe"]
    discovery_ranked = sorted(
        [
            row
            for row in severe_rows
            if int(row["discovery_2024"]["trades"] or 0) >= 5
            and float(row["discovery_2024"]["total_pnl"] or 0.0) > 0
        ],
        key=lambda row: (
            float(row["discovery_2024"]["mean_return_on_risk"] or -math.inf),
            float(row["discovery_2024"]["total_pnl"] or -math.inf),
        ),
        reverse=True,
    )
    selected_names = [row["strategy"] for row in discovery_ranked[:8]]
    discovery_path_owner: dict[str, str] = {}
    discovery_aliases: dict[str, list[str]] = {}
    for name in selected_names:
        run = runs[(name, "severe")]
        signature = _trade_path_hash(run.trades, years={2024})
        if signature in discovery_path_owner:
            discovery_aliases.setdefault(discovery_path_owner[signature], []).append(
                name
            )
        else:
            discovery_path_owner[signature] = name
    selected_evaluation: list[dict[str, Any]] = []
    for name in selected_names:
        severe = next(
            row
            for row in severe_rows
            if row["strategy"] == name
        )
        base = next(
            row
            for row in rows
            if row["strategy"] == name
            and row["execution_assumption"] == "base"
        )
        validation_pass = _period_pass(severe["validation_2025"], 3)
        holdout_pass = _period_pass(severe["holdout_2026"], 2)
        full_pass = _period_pass(severe["full"], 10)
        ci = severe["full"]["block_bootstrap_mean_return_ci_95"]
        ci_positive = bool(ci[0] is not None and float(ci[0]) > 0)
        status = (
            "PROMISING_NOT_RESEARCH_GRADE"
            if validation_pass and holdout_pass and full_pass
            else "FAILED_FORWARD_VALIDATION"
        )
        selected_evaluation.append(
            {
                "strategy": name,
                "trade_path_hash": _trade_path_hash(
                    runs[(name, "severe")].trades
                ),
                "status": status,
                "validation_2025_pass": validation_pass,
                "holdout_2026_pass": holdout_pass,
                "severe_full_pass": full_pass,
                "severe_block_bootstrap_ci_lower_positive": ci_positive,
                "base": base,
                "severe": severe,
            }
        )

    promoted_candidates = [
        row
        for row in selected_evaluation
        if row["status"] == "PROMISING_NOT_RESEARCH_GRADE"
    ]
    unique_promoted: list[dict[str, Any]] = []
    promoted_path_owner: dict[str, str] = {}
    promoted_aliases: dict[str, list[str]] = {}
    for row in promoted_candidates:
        signature = str(row["trade_path_hash"])
        if signature in promoted_path_owner:
            promoted_aliases.setdefault(promoted_path_owner[signature], []).append(
                row["strategy"]
            )
            continue
        promoted_path_owner[signature] = row["strategy"]
        unique_promoted.append(row)

    delayed_datasets = {
        "15:50": HistoricalOptionResearchDataset(
            database_path,
            entry_time=time(15, 50),
            entry_window_minutes=10,
        ),
        "15:55": HistoricalOptionResearchDataset(
            database_path,
            entry_time=time(15, 55),
            entry_window_minutes=5,
        ),
    }
    specs_by_name = {spec.name: spec for spec in specs}
    for row in unique_promoted:
        robustness: dict[str, Any] = {}
        for entry_label, delayed_dataset in delayed_datasets.items():
            delayed_run = simulate_strategy(
                delayed_dataset,
                specs_by_name[row["strategy"]],
                next(
                    assumption
                    for assumption in EXECUTION_ASSUMPTIONS
                    if assumption.name == "severe"
                ),
            )
            robustness[entry_label] = {
                "full": summarize_partition(delayed_run, 2024, 2026),
                "validation_2025": summarize_partition(delayed_run, 2025, 2025),
                "holdout_2026": summarize_partition(delayed_run, 2026, 2026),
            }
        row["entry_time_robustness"] = robustness
        row["entry_delay_positive"] = all(
            _period_pass(metrics["validation_2025"], 1)
            and _period_pass(metrics["holdout_2026"], 1)
            and _period_pass(metrics["full"], 5)
            for metrics in robustness.values()
        )
        row["entry_delay_sample_adequate"] = all(
            int(metrics["validation_2025"]["trades"] or 0) >= 3
            and int(metrics["holdout_2026"]["trades"] or 0) >= 2
            for metrics in robustness.values()
        )
        row["entry_delay_robust"] = bool(
            row["entry_delay_positive"]
            and row["entry_delay_sample_adequate"]
        )
        row["positive_excluding_best_trade"] = bool(
            row["severe"]["full"]["total_pnl_excluding_best_trade"] is not None
            and float(
                row["severe"]["full"]["total_pnl_excluding_best_trade"]
            )
            > 0
        )

    promoted = unique_promoted
    promoted.sort(
        key=lambda row: (
            float(row["severe"]["holdout_2026"]["mean_return_on_risk"] or -math.inf),
            float(row["severe"]["validation_2025"]["mean_return_on_risk"] or -math.inf),
        ),
        reverse=True,
    )

    benchmark_name = "csp_m94_always"
    benchmark = {
        assumption.name: next(
            row
            for row in rows
            if row["strategy"] == benchmark_name
            and row["execution_assumption"] == assumption.name
        )
        for assumption in EXECUTION_ASSUMPTIONS
    }

    latest_decision_date = dataset.snapshots[-1].decision_date
    recent_windows: dict[str, Any] = {}
    for label, months in (("last_3_months", 3), ("last_6_months", 6)):
        window_start = _month_window_start(latest_decision_date, months)
        decision_count = sum(
            window_start <= snapshot.decision_date <= latest_decision_date
            for snapshot in dataset.snapshots
        )
        recent_rows = []
        for spec in specs:
            run = runs[(spec.name, "severe")]
            recent_rows.append(
                {
                    "strategy": spec.name,
                    "structure": spec.structure,
                    "signal": spec.signal,
                    "short_target_moneyness": spec.short_target_moneyness,
                    "long_target_moneyness": spec.long_target_moneyness,
                    "summary": summarize_date_window(
                        run,
                        window_start,
                        latest_decision_date,
                        decision_date_count=decision_count,
                    ),
                }
            )
        by_total_pnl = sorted(
            recent_rows,
            key=lambda row: (
                float(row["summary"]["total_pnl"] or 0.0),
                int(row["summary"]["trades"] or 0),
                float(row["summary"]["mean_return_on_risk"] or -math.inf),
            ),
            reverse=True,
        )
        by_return_on_risk = sorted(
            [row for row in recent_rows if int(row["summary"]["trades"] or 0) >= 2],
            key=lambda row: (
                float(row["summary"]["mean_return_on_risk"] or -math.inf),
                float(row["summary"]["total_pnl"] or 0.0),
            ),
            reverse=True,
        )
        recent_windows[label] = {
            "window_start": window_start.isoformat(),
            "window_end": latest_decision_date.isoformat(),
            "decision_dates": decision_count,
            "execution_assumption": "severe",
            "top_by_total_pnl": by_total_pnl[:10],
            "top_by_mean_return_on_risk_min_2_trades": by_return_on_risk[:10],
            "all_results": recent_rows,
        }

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": (
            "EXPLORATORY_CANDIDATES_FOUND_NOT_RESEARCH_GRADE"
            if promoted
            else "NO_FORWARD_VALIDATED_EXPLORATORY_CANDIDATE"
        ),
        "research_claims_allowed": False,
        "dataset": {
            "database": str(Path(database_path)),
            "decision_dates": len(dataset.snapshots),
            "decision_date_min": dataset.snapshots[0].decision_date.isoformat(),
            "decision_date_max": dataset.snapshots[-1].decision_date.isoformat(),
            "historical_nbbo_available": False,
            "execution_classification": "HISTORICAL_TRADE_BAR_EXECUTION_APPROXIMATION",
        },
        "protocol": {
            "entry_time": "15:45 America/New_York",
            "entry_window_minutes": dataset.entry_window_minutes,
            "contract_selection_information_cutoff": "strictly before 15:45 ET",
            "missing_entry_fill_policy": "skip selected contract; never switch after observing fill availability",
            "short_fill_proxy": "minimum observed option trade-bar low in entry window minus haircut",
            "long_fill_proxy": "maximum observed option trade-bar high in entry window plus haircut",
            "settlement": "intrinsic value using last SPY daily close on or before expiration",
            "partitions": {
                "discovery": "2024",
                "validation": "2025",
                "holdout": "2026-01 through 2026-06",
            },
            "fixed_strategy_count": len(specs),
            "execution_assumptions": [asdict(row) for row in EXECUTION_ASSUMPTIONS],
            "signals": dict(SIGNAL_DESCRIPTIONS),
        },
        "benchmark": benchmark,
        "recent_regime_analysis": recent_windows,
        "discovery_selected_strategy_names": selected_names,
        "discovery_equivalent_aliases": discovery_aliases,
        "forward_evaluation": selected_evaluation,
        "promoted_exploratory_candidates": promoted,
        "promoted_equivalent_aliases": promoted_aliases,
        "strongest_candidates": [
            row
            for row in promoted
            if row.get("entry_delay_positive")
            and row.get("positive_excluding_best_trade")
        ],
        "all_results": rows,
        "caveats": [
            "Historical bid/ask and quote size are absent; every fill is an approximation.",
            "Only 29 monthly decision dates are available, with six dates in the final holdout.",
            "The sample begins after the 2020 crash and largely reflects a strong SPY regime.",
            "Multiple fixed variants were evaluated; promoted rows are exploratory, not proof of an edge.",
            "SPY ETF option expiration is modeled by intrinsic P&L without a post-assignment stock inventory path.",
            "Sparse trade bars can cause skipped orders and selection-dependent coverage.",
        ],
    }
    write_strategy_lab_outputs(payload, runs, output_directory)
    return payload


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    raise TypeError(f"cannot serialize {type(value)!r}")


def write_strategy_lab_outputs(
    payload: Mapping[str, Any],
    runs: Mapping[tuple[str, str], StrategyRun],
    output_directory: str | Path,
) -> None:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / "historical_option_strategy_report.json").write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            default=_json_default,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    with (output / "historical_option_strategy_rankings.csv").open(
        "w", newline="", encoding="utf-8"
    ) as fh:
        fields = [
            "strategy",
            "structure",
            "signal",
            "execution_assumption",
            "full_trades",
            "full_total_pnl",
            "full_mean_return_on_risk",
            "full_win_rate",
            "full_worst_trade_pnl",
            "discovery_trades",
            "discovery_total_pnl",
            "validation_trades",
            "validation_total_pnl",
            "holdout_trades",
            "holdout_total_pnl",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in payload["all_results"]:
            writer.writerow(
                {
                    "strategy": row["strategy"],
                    "structure": row["structure"],
                    "signal": row["signal"],
                    "execution_assumption": row["execution_assumption"],
                    "full_trades": row["full"]["trades"],
                    "full_total_pnl": row["full"]["total_pnl"],
                    "full_mean_return_on_risk": row["full"]["mean_return_on_risk"],
                    "full_win_rate": row["full"]["win_rate"],
                    "full_worst_trade_pnl": row["full"]["worst_trade_pnl"],
                    "discovery_trades": row["discovery_2024"]["trades"],
                    "discovery_total_pnl": row["discovery_2024"]["total_pnl"],
                    "validation_trades": row["validation_2025"]["trades"],
                    "validation_total_pnl": row["validation_2025"]["total_pnl"],
                    "holdout_trades": row["holdout_2026"]["trades"],
                    "holdout_total_pnl": row["holdout_2026"]["total_pnl"],
                }
            )

    with (output / "historical_option_strategy_trades.csv").open(
        "w", newline="", encoding="utf-8"
    ) as fh:
        fields = list(TradeResult.__dataclass_fields__)
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for key in sorted(runs):
            for trade in runs[key].trades:
                row = asdict(trade)
                for field_name in ("decision_date", "expiration_date", "settlement_date"):
                    row[field_name] = row[field_name].isoformat()
                writer.writerow(row)

    promoted = payload["promoted_exploratory_candidates"]
    lines = [
        "# Historical SPY Options Strategy Lab",
        "",
        f"**Status:** `{payload['status']}`",
        "",
        "This report uses observed option trade bars but no historical NBBO. Results are execution approximations, not live-trading evidence.",
        "",
        "## Dataset and protocol",
        "",
        f"- Decision dates: {payload['dataset']['decision_dates']} ({payload['dataset']['decision_date_min']} to {payload['dataset']['decision_date_max']})",
        "- Entry: 15:45 ET; contract selection uses only pre-entry liquidity",
        "- Short fill: entry-window low minus a cost haircut",
        "- Long fill: entry-window high plus a cost haircut",
        "- Discovery/validation/holdout: 2024 / 2025 / 2026 H1",
        "",
        "## Recent-regime screen",
        "",
        "The recent screen ranks the same fixed strategies under the severe execution model. It is a regime view, not a replacement for tail-history validation.",
        "",
    ]
    for label, heading in (("last_6_months", "Latest six decision months"), ("last_3_months", "Latest three decision months")):
        window = payload["recent_regime_analysis"][label]
        lines.extend(
            [
                f"### {heading}",
                "",
                f"Window: {window['window_start']} through {window['window_end']} ({window['decision_dates']} decision dates)",
                "",
                "| Strategy | Trades | P&L | Mean return/risk | Avg credit | Avg risk capital | Max contracts open | Peak risk capital |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in window["top_by_total_pnl"][:5]:
            summary = row["summary"]
            exposure = summary["exposure"]
            lines.append(
                "| {strategy} | {trades} | ${pnl:,.2f} | {ror:.3%} | ${credit:,.2f} | ${risk:,.2f} | {contracts} | ${peak:,.2f} |".format(
                    strategy=row["strategy"],
                    trades=summary["trades"],
                    pnl=summary["total_pnl"],
                    ror=summary["mean_return_on_risk"] or 0.0,
                    credit=exposure["average_net_credit_before_fees"] or 0.0,
                    risk=exposure["average_risk_capital"] or 0.0,
                    contracts=exposure["max_concurrent_option_contracts"],
                    peak=exposure["peak_combined_risk_capital"],
                )
            )
        lines.append("")
    lines.extend(
        [
            "## Promoted exploratory candidates",
            "",
        ]
    )
    if not promoted:
        lines.append("No strategy passed both forward partitions under the severe execution assumption.")
    else:
        lines.append("| Strategy | Structure | 2025 severe P&L | 2026 severe P&L | Full severe trades | Full severe P&L | Entry-delay robust |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for row in promoted:
            severe = row["severe"]
            lines.append(
                "| {strategy} | {structure} | ${validation:,.2f} | ${holdout:,.2f} | {trades} | ${full:,.2f} | {robust} |".format(
                    strategy=row["strategy"],
                    structure=severe["structure"],
                    validation=severe["validation_2025"]["total_pnl"],
                    holdout=severe["holdout_2026"]["total_pnl"],
                    trades=severe["full"]["trades"],
                    full=severe["full"]["total_pnl"],
                    robust=(
                        "yes"
                        if row.get("entry_delay_robust")
                        else "positive, sparse"
                        if row.get("entry_delay_positive")
                        else "no"
                    ),
                )
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A promoted row only means it was profitable in the 2025 and 2026 partitions after being selected on 2024 under the severe cost model. The sample is too short and lacks historical quotes, so no strategy is marked validated.",
            "",
            "## Hard limitations",
            "",
            *[f"- {item}" for item in payload["caveats"]],
            "",
        ]
    )
    (output / "historical_option_strategy_report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
