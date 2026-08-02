"""Recent-regime SPY put strategy expansion using historical trade bars.

This module extends the expiration-only cash-secured-put study with a bounded,
pre-registered family of managed and defined-risk put structures.  It is a
research tool only.  Historical bid/ask and quote size are unavailable, so
all fills are deliberately conservative trade-bar approximations.

Primary ranking windows:

* latest six monthly decisions: January through June 2026;
* latest three monthly decisions: April through June 2026.

Older observations remain a secondary stress reference.  No result produced by
this module is suitable for live deployment or an edge claim without observed
historical quotes and a larger out-of-sample sample.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
import csv
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Iterable, Mapping, Sequence

try:
    from .historical_option_strategy_lab import (
        DEFAULT_DB,
        EXECUTION_ASSUMPTIONS,
        ContractObservation,
        DecisionSnapshot,
        ExecutionAssumption,
        HistoricalOptionResearchDataset,
        SignalFeatures,
        signal_passes,
    )
except ImportError:  # Direct script/test import.
    from historical_option_strategy_lab import (
        DEFAULT_DB,
        EXECUTION_ASSUMPTIONS,
        ContractObservation,
        DecisionSnapshot,
        ExecutionAssumption,
        HistoricalOptionResearchDataset,
        SignalFeatures,
        signal_passes,
    )


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "cipher-system" / "data" / "historical_options" / "recent_strategy_expansion"
NY = __import__("zoneinfo").ZoneInfo("America/New_York")
UTC = timezone.utc


class RecentStrategyError(RuntimeError):
    """Raised when a recent-regime simulation cannot be completed safely."""


def _finite(value: Any, *, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _mean(values: Sequence[float]) -> float | None:
    return statistics.mean(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


@dataclass(frozen=True, slots=True)
class ManagementRule:
    name: str
    profit_target_fraction: float | None = None
    stop_multiple: float | None = None
    exit_dte: int | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("management-rule name is required")
        if self.profit_target_fraction is not None:
            target = _finite(self.profit_target_fraction, name="profit_target_fraction")
            if not 0 < target < 1:
                raise ValueError("profit_target_fraction must be in (0, 1)")
            object.__setattr__(self, "profit_target_fraction", target)
        if self.stop_multiple is not None:
            stop = _finite(self.stop_multiple, name="stop_multiple")
            if stop <= 1:
                raise ValueError("stop_multiple must exceed 1")
            object.__setattr__(self, "stop_multiple", stop)
        if self.exit_dte is not None:
            if not isinstance(self.exit_dte, int) or isinstance(self.exit_dte, bool):
                raise ValueError("exit_dte must be an integer")
            if self.exit_dte < 0:
                raise ValueError("exit_dte cannot be negative")

    @property
    def managed(self) -> bool:
        return any(
            value is not None
            for value in (
                self.profit_target_fraction,
                self.stop_multiple,
                self.exit_dte,
            )
        )


EXPIRY = ManagementRule("expiry")
PT25 = ManagementRule("pt25", profit_target_fraction=0.25)
PT50 = ManagementRule("pt50", profit_target_fraction=0.50)
PT75 = ManagementRule("pt75", profit_target_fraction=0.75)
PT50_STOP2 = ManagementRule("pt50_stop2", profit_target_fraction=0.50, stop_multiple=2.0)
PT50_STOP3 = ManagementRule("pt50_stop3", profit_target_fraction=0.50, stop_multiple=3.0)
DTE7 = ManagementRule("dte7", exit_dte=7)
PT50_DTE7 = ManagementRule("pt50_dte7", profit_target_fraction=0.50, exit_dte=7)
PT50_STOP2_DTE7 = ManagementRule(
    "pt50_stop2_dte7",
    profit_target_fraction=0.50,
    stop_multiple=2.0,
    exit_dte=7,
)


@dataclass(frozen=True, slots=True)
class LegTarget:
    quantity: int
    target_moneyness: float

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise ValueError("leg quantity must be an integer")
        if self.quantity == 0:
            raise ValueError("leg quantity cannot be zero")
        target = _finite(self.target_moneyness, name="target_moneyness")
        if not 0 < target < 1.25:
            raise ValueError("invalid leg target moneyness")
        object.__setattr__(self, "target_moneyness", target)


@dataclass(frozen=True, slots=True)
class ExpandedStrategySpec:
    name: str
    family: str
    signal: str
    legs: tuple[LegTarget, ...]
    management: ManagementRule = EXPIRY
    target_dte: int = 35

    def __post_init__(self) -> None:
        families = {
            "csp",
            "bull_put_spread",
            "bear_put_spread",
            "put_butterfly",
            "put_backspread",
            "csp_ladder",
            "long_put",
        }
        if self.family not in families:
            raise ValueError(f"unsupported family {self.family!r}")
        if self.signal not in {
            "always",
            "momentum20_positive",
            "trend200",
            "momentum20_and_trend200",
            "mild_pullback_uptrend",
            "elevated_rv_momentum",
        }:
            raise ValueError(f"unsupported signal {self.signal!r}")
        if not self.name.strip() or not self.legs:
            raise ValueError("strategy name and legs are required")
        if not isinstance(self.target_dte, int) or isinstance(self.target_dte, bool):
            raise ValueError("target_dte must be an integer")
        if self.target_dte <= 0:
            raise ValueError("target_dte must be positive")
        object.__setattr__(self, "legs", tuple(self.legs))
        if self.management.managed and self.family not in {"csp", "bull_put_spread"}:
            raise ValueError("managed exits are supported only for CSPs and bull put spreads")

    @property
    def contracts_per_position(self) -> int:
        return sum(abs(leg.quantity) for leg in self.legs)


@dataclass(frozen=True, slots=True)
class SelectedLeg:
    quantity: int
    contract: ContractObservation
    entry_price: float


@dataclass(frozen=True, slots=True)
class PathBar:
    timestamp: datetime
    low: float
    high: float
    volume: float = 1.0


@dataclass(frozen=True, slots=True)
class ExpandedTrade:
    strategy: str
    family: str
    execution_assumption: str
    management_rule: str
    signal: str
    decision_date: date
    expiration_date: date
    exit_date: date
    exit_reason: str
    days_held: int
    contracts_per_position: int
    leg_symbols: tuple[str, ...]
    leg_quantities: tuple[int, ...]
    leg_strikes: tuple[float, ...]
    entry_cash_per_share: float
    exit_cash_per_share: float
    expiration_payoff_per_share: float
    fees: float
    pnl: float
    risk_capital: float
    return_on_risk: float
    feature_return_20d: float | None
    feature_above_sma_200: bool | None
    feature_rv_percentile: float | None
    exit_timestamp: str | None = None
    minimum_exit_bar_volume: float | None = None


@dataclass(frozen=True, slots=True)
class ExpandedSkip:
    strategy: str
    execution_assumption: str
    decision_date: date
    reason: str


@dataclass(frozen=True, slots=True)
class ExpandedRun:
    spec: ExpandedStrategySpec
    execution: ExecutionAssumption
    trades: tuple[ExpandedTrade, ...]
    skips: tuple[ExpandedSkip, ...]


class RecentPathStore:
    """Cached option paths from the immutable SQLite archive."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self._cache: dict[str, tuple[PathBar, ...]] = {}

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def bars(self, symbol: str) -> tuple[PathBar, ...]:
        symbol = str(symbol).upper().strip()
        if symbol in self._cache:
            return self._cache[symbol]
        with self.connect() as db:
            rows = db.execute(
                """select timestamp,low,high,volume from option_bars
                   where symbol=? and low is not null and high is not null
                   order by timestamp""",
                (symbol,),
            ).fetchall()
        result: list[PathBar] = []
        for row in rows:
            low = float(row["low"])
            high = float(row["high"])
            if low <= 0 or high < low:
                continue
            timestamp = datetime.fromisoformat(
                str(row["timestamp"]).replace("Z", "+00:00")
            ).astimezone(UTC)
            local = timestamp.astimezone(NY)
            if local.weekday() >= 5 or not time(9, 30) <= local.time() <= time(16, 0):
                continue
            result.append(
                PathBar(
                    timestamp,
                    low,
                    high,
                    max(0.0, float(row["volume"] or 0.0)),
                )
            )
        self._cache[symbol] = tuple(result)
        return self._cache[symbol]

    def synchronized_bars(
        self,
        symbols: Sequence[str],
        *,
        after: datetime,
        through: date,
    ) -> tuple[tuple[datetime, tuple[PathBar, ...]], ...]:
        if not symbols:
            return ()
        maps: list[dict[datetime, PathBar]] = []
        for symbol in symbols:
            maps.append(
                {
                    row.timestamp: row
                    for row in self.bars(symbol)
                    if row.timestamp > after and row.timestamp.date() <= through
                }
            )
        common = set(maps[0])
        for mapping in maps[1:]:
            common.intersection_update(mapping)
        return tuple(
            (timestamp, tuple(mapping[timestamp] for mapping in maps))
            for timestamp in sorted(common)
        )


def _contract_key(
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


def select_legs(
    snapshot: DecisionSnapshot,
    spec: ExpandedStrategySpec,
) -> tuple[tuple[int, ContractObservation], ...] | None:
    liquid = [contract for contract in snapshot.contracts if contract.liquid_before_entry]
    if not liquid:
        return None
    anchor = min(
        liquid,
        key=lambda contract: _contract_key(
            contract,
            target_moneyness=spec.legs[0].target_moneyness,
            target_dte=spec.target_dte,
        ),
    )
    expiry = anchor.expiration_date
    eligible = [contract for contract in liquid if contract.expiration_date == expiry]
    selected: list[tuple[int, ContractObservation]] = []
    used: set[str] = set()
    for index, target in enumerate(spec.legs):
        if index == 0:
            contract = anchor
        else:
            candidates = [contract for contract in eligible if contract.symbol not in used]
            if not candidates:
                return None
            contract = min(
                candidates,
                key=lambda row: _contract_key(
                    row,
                    target_moneyness=target.target_moneyness,
                    target_dte=spec.target_dte,
                ),
            )
        used.add(contract.symbol)
        selected.append((target.quantity, contract))

    strikes = [contract.strike for _, contract in selected]
    if spec.family == "bull_put_spread":
        if not (selected[0][0] < 0 and selected[1][0] > 0 and strikes[0] > strikes[1]):
            return None
    elif spec.family == "bear_put_spread":
        if not (selected[0][0] > 0 and selected[1][0] < 0 and strikes[0] > strikes[1]):
            return None
    elif spec.family == "put_butterfly":
        if len(selected) != 3 or not (strikes[0] > strikes[1] > strikes[2]):
            return None
    elif spec.family == "put_backspread":
        if len(selected) != 2 or not (selected[0][0] < 0 and selected[1][0] > 1 and strikes[0] > strikes[1]):
            return None
    elif spec.family == "csp_ladder":
        if any(quantity >= 0 for quantity, _ in selected):
            return None
    elif spec.family == "long_put":
        if len(selected) != 1 or selected[0][0] <= 0:
            return None
    return tuple(selected)


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


def _entry_cash_per_share(legs: Sequence[SelectedLeg]) -> float:
    return sum(-leg.quantity * leg.entry_price for leg in legs)


def _expiration_payoff_per_share(legs: Sequence[SelectedLeg], spot: float) -> float:
    return sum(
        leg.quantity * max(leg.contract.strike - spot, 0.0)
        for leg in legs
    )


def _risk_capital(
    legs: Sequence[SelectedLeg],
    entry_cash_per_share: float,
) -> float:
    strikes = sorted({leg.contract.strike for leg in legs})
    critical_spots = [0.0, *strikes, max(strikes) * 1.25]
    values = [
        entry_cash_per_share + _expiration_payoff_per_share(legs, spot)
        for spot in critical_spots
    ]
    minimum = min(values)
    if minimum >= 0:
        # A true zero-risk credit is almost certainly a sparse-data artifact.
        return 0.0
    return -minimum * 100.0


def _close_cash_per_share(
    legs: Sequence[SelectedLeg],
    bars: Sequence[PathBar],
    execution: ExecutionAssumption,
) -> float | None:
    if len(legs) != len(bars):
        raise ValueError("legs and bars must have equal length")
    cash = 0.0
    for leg, bar in zip(legs, bars):
        if leg.quantity < 0:
            debit = execution.long_debit(bar.high)
            if debit is None:
                return None
            cash -= abs(leg.quantity) * debit
        else:
            credit = execution.short_credit(bar.low)
            if credit is None:
                return None
            cash += abs(leg.quantity) * credit
    return cash


def _managed_exit(
    path_store: RecentPathStore,
    legs: Sequence[SelectedLeg],
    execution: ExecutionAssumption,
    management: ManagementRule,
    *,
    decision_date: date,
    expiration_date: date,
    entry_cash_per_share: float,
    minimum_exit_volume: float = 0.0,
    target_confirmations: int = 1,
) -> tuple[datetime, str, float, float] | None:
    if not management.managed:
        return None
    if entry_cash_per_share <= 0:
        return None
    minimum_exit_volume = max(0.0, _finite(minimum_exit_volume, name="minimum_exit_volume"))
    if not isinstance(target_confirmations, int) or isinstance(target_confirmations, bool):
        raise ValueError("target_confirmations must be an integer")
    if target_confirmations <= 0:
        raise ValueError("target_confirmations must be positive")
    after = datetime.combine(decision_date, time(16, 0), tzinfo=NY).astimezone(UTC)
    synchronized = path_store.synchronized_bars(
        [leg.contract.symbol for leg in legs],
        after=after,
        through=expiration_date,
    )
    target_streak = 0
    for timestamp, bars in synchronized:
        local = timestamp.astimezone(NY)
        if local.date() <= decision_date:
            continue
        minimum_bar_volume = min((bar.volume for bar in bars), default=0.0)
        if minimum_bar_volume < minimum_exit_volume:
            target_streak = 0
            continue
        close_cash = _close_cash_per_share(legs, bars, execution)
        if close_cash is None:
            target_streak = 0
            continue
        close_debit = -close_cash
        stop_hit = (
            management.stop_multiple is not None
            and close_debit >= entry_cash_per_share * management.stop_multiple
        )
        target_hit = (
            management.profit_target_fraction is not None
            and close_debit
            <= entry_cash_per_share * (1.0 - management.profit_target_fraction)
        )
        # A stop wins any same-bar ambiguity and never waits for confirmation.
        if stop_hit:
            return timestamp, "stop", close_cash, minimum_bar_volume
        target_streak = target_streak + 1 if target_hit else 0
        if target_streak >= target_confirmations:
            return timestamp, "profit_target", close_cash, minimum_bar_volume
        if (
            management.exit_dte is not None
            and (expiration_date - local.date()).days <= management.exit_dte
            and local.time() >= time(15, 45)
        ):
            return timestamp, "time_exit", close_cash, minimum_bar_volume
    return None


def simulate_expanded_strategy(
    dataset: HistoricalOptionResearchDataset,
    path_store: RecentPathStore,
    spec: ExpandedStrategySpec,
    execution: ExecutionAssumption,
    *,
    minimum_entry_volume: float = 0.0,
    minimum_exit_volume: float = 0.0,
    target_confirmations: int = 1,
) -> ExpandedRun:
    trades: list[ExpandedTrade] = []
    skips: list[ExpandedSkip] = []
    for snapshot in dataset.snapshots:
        passed, signal_reason = signal_passes(spec.signal, snapshot.features)
        if not passed:
            skips.append(
                ExpandedSkip(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    signal_reason or "signal_failed",
                )
            )
            continue
        selected = select_legs(snapshot, spec)
        if selected is None:
            skips.append(
                ExpandedSkip(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "unable_to_select_all_pre_entry_legs",
                )
            )
            continue
        legs = _entry_legs(selected, execution)
        if legs is None:
            skips.append(
                ExpandedSkip(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "selected_leg_missing_post_entry_observation_or_price",
                )
            )
            continue
        required_entry_volume = max(
            0.0,
            _finite(minimum_entry_volume, name="minimum_entry_volume"),
        )
        if any(
            leg.contract.entry_volume < required_entry_volume for leg in legs
        ):
            skips.append(
                ExpandedSkip(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "selected_leg_entry_volume_below_minimum",
                )
            )
            continue
        entry_cash = _entry_cash_per_share(legs)
        if spec.family in {"csp", "bull_put_spread", "csp_ladder"} and entry_cash <= 0:
            skips.append(
                ExpandedSkip(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "credit_structure_not_a_credit_after_costs",
                )
            )
            continue
        risk_capital = _risk_capital(legs, entry_cash)
        if risk_capital <= 0:
            skips.append(
                ExpandedSkip(
                    spec.name,
                    execution.name,
                    snapshot.decision_date,
                    "risk_capital_nonpositive_or_arbitrage_like",
                )
            )
            continue
        expiration = legs[0].contract.expiration_date
        if any(leg.contract.expiration_date != expiration for leg in legs):
            raise RecentStrategyError("all legs must share one expiration")
        managed = _managed_exit(
            path_store,
            legs,
            execution,
            spec.management,
            decision_date=snapshot.decision_date,
            expiration_date=expiration,
            entry_cash_per_share=entry_cash,
            minimum_exit_volume=minimum_exit_volume,
            target_confirmations=target_confirmations,
        )
        if managed is None:
            settlement_date, settlement_spot = dataset.settlement(expiration)
            exit_date = settlement_date
            exit_reason = "expiration"
            exit_cash = 0.0
            expiration_payoff = _expiration_payoff_per_share(legs, settlement_spot)
            exit_timestamp = None
            minimum_exit_bar_volume = None
        else:
            managed_timestamp, exit_reason, exit_cash, minimum_exit_bar_volume = managed
            exit_date = managed_timestamp.astimezone(NY).date()
            exit_timestamp = managed_timestamp.isoformat()
            expiration_payoff = 0.0
        contract_count = sum(abs(leg.quantity) for leg in legs)
        fees = execution.lifecycle_fees(contract_count)
        pnl = (entry_cash + exit_cash + expiration_payoff) * 100.0 - fees
        trades.append(
            ExpandedTrade(
                strategy=spec.name,
                family=spec.family,
                execution_assumption=execution.name,
                management_rule=spec.management.name,
                signal=spec.signal,
                decision_date=snapshot.decision_date,
                expiration_date=expiration,
                exit_date=exit_date,
                exit_reason=exit_reason,
                days_held=max(0, (exit_date - snapshot.decision_date).days),
                contracts_per_position=contract_count,
                leg_symbols=tuple(leg.contract.symbol for leg in legs),
                leg_quantities=tuple(leg.quantity for leg in legs),
                leg_strikes=tuple(leg.contract.strike for leg in legs),
                entry_cash_per_share=entry_cash,
                exit_cash_per_share=exit_cash,
                expiration_payoff_per_share=expiration_payoff,
                fees=fees,
                pnl=pnl,
                risk_capital=risk_capital,
                return_on_risk=pnl / risk_capital,
                feature_return_20d=snapshot.features.return_20d,
                feature_above_sma_200=snapshot.features.above_sma_200,
                feature_rv_percentile=snapshot.features.realized_volatility_percentile,
                exit_timestamp=exit_timestamp,
                minimum_exit_bar_volume=minimum_exit_bar_volume,
            )
        )
    return ExpandedRun(spec, execution, tuple(trades), tuple(skips))


def _csp_spec(target: float, management: ManagementRule, signal: str = "always") -> ExpandedStrategySpec:
    suffix = f"m{int(target * 100):02d}_{signal}_{management.name}"
    return ExpandedStrategySpec(
        f"csp_{suffix}",
        "csp",
        signal,
        (LegTarget(-1, target),),
        management,
    )


def fixed_expanded_specs() -> tuple[ExpandedStrategySpec, ...]:
    specs: list[ExpandedStrategySpec] = []
    csp_rules = (
        EXPIRY,
        PT25,
        PT50,
        PT75,
        PT50_STOP2,
        PT50_STOP3,
        DTE7,
        PT50_DTE7,
        PT50_STOP2_DTE7,
    )
    for target in (0.90, 0.92, 0.94, 0.96, 0.98):
        for rule in csp_rules:
            specs.append(_csp_spec(target, rule))
    for target in (0.94, 0.96, 0.98):
        for rule in (EXPIRY, PT50, PT50_STOP2_DTE7):
            specs.append(_csp_spec(target, rule, "momentum20_positive"))

    vertical_pairs = (
        (0.94, 0.90),
        (0.96, 0.90),
        (0.96, 0.92),
        (0.98, 0.90),
        (0.98, 0.92),
        (0.98, 0.94),
    )
    for short_target, long_target in vertical_pairs:
        for rule in (EXPIRY, PT50, DTE7, PT50_STOP2_DTE7):
            specs.append(
                ExpandedStrategySpec(
                    f"bull_put_m{int(short_target*100):02d}_m{int(long_target*100):02d}_{rule.name}",
                    "bull_put_spread",
                    "always",
                    (LegTarget(-1, short_target), LegTarget(1, long_target)),
                    rule,
                )
            )

    for high, low in ((0.98, 0.94), (0.98, 0.90), (0.96, 0.92)):
        specs.append(
            ExpandedStrategySpec(
                f"bear_put_m{int(high*100):02d}_m{int(low*100):02d}_expiry",
                "bear_put_spread",
                "always",
                (LegTarget(1, high), LegTarget(-1, low)),
            )
        )
    for target in (0.94, 0.96, 0.98):
        specs.append(
            ExpandedStrategySpec(
                f"long_put_m{int(target*100):02d}_expiry",
                "long_put",
                "always",
                (LegTarget(1, target),),
            )
        )
    butterflies = (
        (0.98, 0.94, 0.90),
        (0.98, 0.96, 0.94),
        (0.96, 0.94, 0.92),
    )
    for upper, middle, lower in butterflies:
        specs.append(
            ExpandedStrategySpec(
                f"put_bfly_m{int(upper*100):02d}_m{int(middle*100):02d}_m{int(lower*100):02d}",
                "put_butterfly",
                "always",
                (
                    LegTarget(1, upper),
                    LegTarget(-2, middle),
                    LegTarget(1, lower),
                ),
            )
        )
    backspreads = ((0.98, 0.92), (0.96, 0.90), (0.98, 0.94))
    for short_target, long_target in backspreads:
        specs.append(
            ExpandedStrategySpec(
                f"put_backspread_short{int(short_target*100):02d}_long2x{int(long_target*100):02d}",
                "put_backspread",
                "always",
                (LegTarget(-1, short_target), LegTarget(2, long_target)),
            )
        )
    for targets in ((0.92, 0.94, 0.96), (0.94, 0.96, 0.98)):
        specs.append(
            ExpandedStrategySpec(
                "csp_ladder_" + "_".join(f"m{int(value*100):02d}" for value in targets),
                "csp_ladder",
                "always",
                tuple(LegTarget(-1, value) for value in targets),
            )
        )
    return tuple(specs)


def _window_trades(
    trades: Sequence[ExpandedTrade],
    start: date,
    end: date,
) -> tuple[ExpandedTrade, ...]:
    return tuple(
        trade for trade in trades if start <= trade.decision_date <= end
    )


def _window_skips(
    skips: Sequence[ExpandedSkip],
    start: date,
    end: date,
) -> tuple[ExpandedSkip, ...]:
    return tuple(skip for skip in skips if start <= skip.decision_date <= end)


def _peak_exposure(trades: Sequence[ExpandedTrade]) -> dict[str, Any]:
    events: list[tuple[date, int, ExpandedTrade]] = []
    for trade in trades:
        events.append((trade.decision_date, 1, trade))
        events.append((trade.exit_date, -1, trade))
    # Opens are processed before same-day exits to avoid understating overlap.
    events.sort(key=lambda row: (row[0], -row[1]))
    active: dict[tuple[str, date], ExpandedTrade] = {}
    peak_positions = 0
    peak_contracts = 0
    peak_capital = 0.0
    for event_date, direction, trade in events:
        key = (trade.strategy, trade.decision_date)
        if direction > 0:
            active[key] = trade
        else:
            active.pop(key, None)
        peak_positions = max(peak_positions, len(active))
        peak_contracts = max(
            peak_contracts,
            sum(item.contracts_per_position for item in active.values()),
        )
        peak_capital = max(
            peak_capital,
            sum(item.risk_capital for item in active.values()),
        )
    return {
        "max_concurrent_positions": peak_positions,
        "max_concurrent_contracts": peak_contracts,
        "peak_combined_risk_capital": peak_capital,
    }


def apply_position_cap(
    trades: Sequence[ExpandedTrade],
    maximum_positions: int,
) -> tuple[ExpandedTrade, ...]:
    if maximum_positions <= 0:
        raise ValueError("maximum_positions must be positive")
    accepted: list[ExpandedTrade] = []
    active: list[ExpandedTrade] = []
    for trade in sorted(trades, key=lambda row: row.decision_date):
        active = [row for row in active if row.exit_date >= trade.decision_date]
        if len(active) >= maximum_positions:
            continue
        accepted.append(trade)
        active.append(trade)
    return tuple(accepted)


def summarize_recent(
    trades: Sequence[ExpandedTrade],
    skips: Sequence[ExpandedSkip],
    *,
    start: date,
    end: date,
    decision_dates: int,
) -> dict[str, Any]:
    ordered = tuple(sorted(_window_trades(trades, start, end), key=lambda row: row.decision_date))
    window_skips = _window_skips(skips, start, end)
    pnls = [trade.pnl for trade in ordered]
    returns = [trade.return_on_risk for trade in ordered]
    exposure = _peak_exposure(ordered)
    cap_one = apply_position_cap(ordered, 1)
    cap_one_exposure = _peak_exposure(cap_one)
    entry_credits = [trade.entry_cash_per_share * 100.0 for trade in ordered if trade.entry_cash_per_share > 0]
    entry_debits = [-trade.entry_cash_per_share * 100.0 for trade in ordered if trade.entry_cash_per_share < 0]
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "decision_dates": decision_dates,
        "trades": len(ordered),
        "trade_frequency": len(ordered) / decision_dates if decision_dates else None,
        "total_pnl": sum(pnls),
        "mean_pnl": _mean(pnls),
        "median_pnl": _median(pnls),
        "mean_return_on_risk": _mean(returns),
        "win_rate": sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "worst_trade_pnl": min(pnls) if pnls else None,
        "best_trade_pnl": max(pnls) if pnls else None,
        "average_days_held": _mean([float(trade.days_held) for trade in ordered]),
        "average_risk_capital": _mean([trade.risk_capital for trade in ordered]),
        "maximum_single_position_risk_capital": max(
            (trade.risk_capital for trade in ordered), default=None
        ),
        "average_credit_received": _mean(entry_credits),
        "average_debit_paid": _mean(entry_debits),
        "average_fees": _mean([trade.fees for trade in ordered]),
        "exit_reasons": dict(Counter(trade.exit_reason for trade in ordered)),
        "skip_reasons": dict(Counter(skip.reason for skip in window_skips)),
        **exposure,
        "return_on_peak_risk_capital": (
            sum(pnls) / exposure["peak_combined_risk_capital"]
            if exposure["peak_combined_risk_capital"] > 0
            else None
        ),
        "one_position_cap": {
            "trades": len(cap_one),
            "total_pnl": sum(trade.pnl for trade in cap_one),
            "mean_return_on_risk": _mean(
                [trade.return_on_risk for trade in cap_one]
            ),
            **cap_one_exposure,
        },
    }


def _ranking_score(summary: Mapping[str, Any]) -> tuple[float, float, int]:
    return (
        float(summary.get("return_on_peak_risk_capital") or -math.inf),
        float(summary.get("total_pnl") or -math.inf),
        int(summary.get("trades") or 0),
    )


def _run_liquidity_sensitivity(
    dataset: HistoricalOptionResearchDataset,
    path_store: RecentPathStore,
    specs_by_name: Mapping[str, ExpandedStrategySpec],
    windows: Mapping[str, tuple[date, date, int]],
) -> dict[str, Any]:
    severe = next(
        assumption
        for assumption in EXECUTION_ASSUMPTIONS
        if assumption.name == "severe"
    )
    strategy_names = (
        "csp_m94_always_pt50",
        "csp_m94_always_dte7",
        "csp_m94_always_pt25",
        "csp_m96_always_pt50_dte7",
        "csp_m92_always_pt75",
        "bull_put_m96_m92_expiry",
    )
    scenarios = {
        "entry5_exit5": {
            "minimum_entry_volume": 5.0,
            "minimum_exit_volume": 5.0,
            "target_confirmations": 1,
        },
        "entry10_exit10": {
            "minimum_entry_volume": 10.0,
            "minimum_exit_volume": 10.0,
            "target_confirmations": 1,
        },
        "entry5_exit5_confirm2": {
            "minimum_entry_volume": 5.0,
            "minimum_exit_volume": 5.0,
            "target_confirmations": 2,
        },
    }
    result: dict[str, Any] = {}
    for strategy_name in strategy_names:
        spec = specs_by_name[strategy_name]
        strategy_result: dict[str, Any] = {}
        for scenario_name, parameters in scenarios.items():
            run = simulate_expanded_strategy(
                dataset,
                path_store,
                spec,
                severe,
                **parameters,
            )
            scenario_result: dict[str, Any] = {
                "parameters": dict(parameters),
            }
            for label, (start, end, count) in windows.items():
                scenario_result[label] = summarize_recent(
                    run.trades,
                    run.skips,
                    start=start,
                    end=end,
                    decision_dates=count,
                )
            strategy_result[scenario_name] = scenario_result
        result[strategy_name] = strategy_result
    return result


def run_recent_strategy_expansion(
    database_path: str | Path = DEFAULT_DB,
    *,
    output_directory: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    dataset = HistoricalOptionResearchDataset(database_path)
    path_store = RecentPathStore(database_path)
    specs = fixed_expanded_specs()
    windows = {
        "last_6_months": (date(2026, 1, 1), date(2026, 6, 1), 6),
        "last_3_months": (date(2026, 4, 1), date(2026, 6, 1), 3),
        "full_archive": (
            dataset.snapshots[0].decision_date,
            dataset.snapshots[-1].decision_date,
            len(dataset.snapshots),
        ),
    }
    rows: list[dict[str, Any]] = []
    runs: dict[tuple[str, str], ExpandedRun] = {}
    for spec in specs:
        for execution in EXECUTION_ASSUMPTIONS:
            run = simulate_expanded_strategy(dataset, path_store, spec, execution)
            runs[(spec.name, execution.name)] = run
            row = {
                "strategy": spec.name,
                "family": spec.family,
                "signal": spec.signal,
                "management_rule": spec.management.name,
                "contracts_per_position": spec.contracts_per_position,
                "execution_assumption": execution.name,
            }
            for label, (start, end, count) in windows.items():
                row[label] = summarize_recent(
                    run.trades,
                    run.skips,
                    start=start,
                    end=end,
                    decision_dates=count,
                )
            rows.append(row)

    severe_rows = [row for row in rows if row["execution_assumption"] == "severe"]
    rankings: dict[str, list[dict[str, Any]]] = {}
    for label in ("last_6_months", "last_3_months"):
        eligible = [
            row
            for row in severe_rows
            if int(row[label]["trades"] or 0) >= 2
            and float(row[label]["total_pnl"] or 0.0) > 0
        ]
        eligible.sort(key=lambda row: _ranking_score(row[label]), reverse=True)
        rankings[label] = eligible

    family_leaders: dict[str, dict[str, Any]] = {}
    for family in sorted({spec.family for spec in specs}):
        candidates = [
            row
            for row in severe_rows
            if row["family"] == family
            and int(row["last_6_months"]["trades"] or 0) >= 2
            and float(row["last_6_months"]["total_pnl"] or 0.0) > 0
        ]
        if candidates:
            candidates.sort(
                key=lambda row: _ranking_score(row["last_6_months"]),
                reverse=True,
            )
            family_leaders[family] = candidates[0]

    primary_candidates = [
        row
        for row in severe_rows
        if int(row["last_6_months"]["trades"] or 0) >= 4
        and float(row["last_6_months"]["total_pnl"] or 0.0) > 0
        and int(row["full_archive"]["trades"] or 0) >= 12
        and float(row["full_archive"]["total_pnl"] or 0.0) > 0
        and float(
            row["full_archive"]["one_position_cap"]["total_pnl"] or 0.0
        )
        > 0
        and float(row["full_archive"]["worst_trade_pnl"] or -math.inf)
        > -1_000.0
    ]
    primary_candidates.sort(
        key=lambda row: (
            _ranking_score(row["last_6_months"]),
            float(row["full_archive"]["total_pnl"] or -math.inf),
        ),
        reverse=True,
    )
    liquidity_sensitivity = _run_liquidity_sensitivity(
        dataset,
        path_store,
        {spec.name: spec for spec in specs},
        windows,
    )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RECENT_REGIME_EXPLORATORY_ONLY_NO_HISTORICAL_NBBO",
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
            "fixed_strategy_count": len(specs),
            "primary_windows": {
                "last_6_months": "2026-01-01 through 2026-06-01 decision dates",
                "last_3_months": "2026-04-01 through 2026-06-01 decision dates",
            },
            "entry": "15:45 ET using the same conservative entry-window proxy as the base strategy lab",
            "managed_close": "synchronized minute bars; buy short legs at bar high plus haircut and sell long legs at bar low minus haircut",
            "same_bar_collision": "stop before profit target",
            "position_cap_sensitivity": "uncapped monthly entries and a separate maximum-one-open-position replay",
            "liquidity_sensitivity": "selected candidates rerun with 5- and 10-contract entry/exit volume floors and two-bar target confirmation",
            "execution_assumptions": [asdict(item) for item in EXECUTION_ASSUMPTIONS],
        },
        "rankings": rankings,
        "family_leaders": family_leaders,
        "primary_candidates": primary_candidates,
        "liquidity_sensitivity": liquidity_sensitivity,
        "all_results": rows,
        "caveats": [
            "Only six monthly decisions exist in the primary window and three in the shortest window.",
            "Historical bid/ask, quote size, and queue position are absent.",
            "Managed exits require synchronized trade bars and may skip otherwise tradable positions.",
            "The expanded family contains many fixed variants, so rankings are exploratory and multiple-testing-sensitive.",
            "Cash-secured structures retain large crash losses despite calm recent outcomes.",
            "Call-based strategies, covered calls, collars, and iron condors are not tested because this archive contains put selections only.",
        ],
    }
    write_recent_strategy_outputs(payload, runs, output_directory)
    return payload


def _csv_value(value: Any) -> Any:
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, date):
        return value.isoformat()
    return value


def write_recent_strategy_outputs(
    payload: Mapping[str, Any],
    runs: Mapping[tuple[str, str], ExpandedRun],
    output_directory: str | Path,
) -> None:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / "recent_option_strategy_expansion.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    with (output / "recent_option_strategy_rankings.csv").open(
        "w", newline="", encoding="utf-8"
    ) as fh:
        fields = [
            "strategy",
            "family",
            "management_rule",
            "contracts_per_position",
            "execution_assumption",
            "six_month_trades",
            "six_month_pnl",
            "six_month_return_on_peak_capital",
            "six_month_peak_capital",
            "six_month_max_contracts",
            "three_month_trades",
            "three_month_pnl",
            "three_month_return_on_peak_capital",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in payload["all_results"]:
            six = row["last_6_months"]
            three = row["last_3_months"]
            writer.writerow(
                {
                    "strategy": row["strategy"],
                    "family": row["family"],
                    "management_rule": row["management_rule"],
                    "contracts_per_position": row["contracts_per_position"],
                    "execution_assumption": row["execution_assumption"],
                    "six_month_trades": six["trades"],
                    "six_month_pnl": six["total_pnl"],
                    "six_month_return_on_peak_capital": six["return_on_peak_risk_capital"],
                    "six_month_peak_capital": six["peak_combined_risk_capital"],
                    "six_month_max_contracts": six["max_concurrent_contracts"],
                    "three_month_trades": three["trades"],
                    "three_month_pnl": three["total_pnl"],
                    "three_month_return_on_peak_capital": three["return_on_peak_risk_capital"],
                }
            )
    with (output / "recent_option_strategy_trades.csv").open(
        "w", newline="", encoding="utf-8"
    ) as fh:
        fields = list(ExpandedTrade.__dataclass_fields__)
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for key in sorted(runs):
            for trade in runs[key].trades:
                writer.writerow(
                    {
                        field_name: _csv_value(value)
                        for field_name, value in asdict(trade).items()
                    }
                )

    leaders = payload["family_leaders"]
    lines = [
        "# Recent SPY Put Strategy Expansion",
        "",
        f"**Status:** `{payload['status']}`",
        "",
        "Primary ranking uses January-June 2026. All fills are conservative trade-bar approximations because historical NBBO is unavailable.",
        "",
        "## Six-month family leaders under severe costs",
        "",
        "| Family | Strategy | Trades | P&L | Return on peak capital | Peak capital | Max contracts |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for family, row in sorted(leaders.items()):
        summary = row["last_6_months"]
        lines.append(
            "| {family} | {strategy} | {trades} | ${pnl:,.2f} | {return_pct:.2%} | ${capital:,.2f} | {contracts} |".format(
                family=family,
                strategy=row["strategy"],
                trades=summary["trades"],
                pnl=summary["total_pnl"],
                return_pct=summary["return_on_peak_risk_capital"] or 0.0,
                capital=summary["peak_combined_risk_capital"],
                contracts=summary["max_concurrent_contracts"],
            )
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            *[f"- {item}" for item in payload["caveats"]],
            "",
        ]
    )
    (output / "recent_option_strategy_expansion.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
