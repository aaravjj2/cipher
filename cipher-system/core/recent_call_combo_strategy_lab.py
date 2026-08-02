"""Recent SPY call/put combination strategy lab.

Uses separate point-in-time call and put archives for January-June 2026 to test
covered calls, protective puts, collars, covered strangles, bear call spreads,
and iron condors.  Historical NBBO is unavailable, so every option fill remains
a conservative trade-bar approximation.  The module is research-only.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
import csv
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

try:
    from .historical_option_strategy_lab import (
        EXECUTION_ASSUMPTIONS,
        ContractObservation,
        DecisionSnapshot,
        ExecutionAssumption,
        HistoricalOptionResearchDataset,
    )
except ImportError:
    from historical_option_strategy_lab import (
        EXECUTION_ASSUMPTIONS,
        ContractObservation,
        DecisionSnapshot,
        ExecutionAssumption,
        HistoricalOptionResearchDataset,
    )


ROOT = Path(__file__).resolve().parents[2]
PUT_DB = ROOT / "cipher-system" / "data" / "historical_options" / "alpaca_spy_monthly_backfill" / "historical_options.sqlite"
CALL_DB = ROOT / "cipher-system" / "data" / "historical_options" / "alpaca_spy_call_recent" / "historical_options.sqlite"
DEFAULT_OUTPUT = ROOT / "cipher-system" / "data" / "historical_options" / "recent_call_combo_lab"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc


class ComboStrategyError(RuntimeError):
    pass


def _mean(values: Sequence[float]) -> float | None:
    return statistics.mean(values) if values else None


@dataclass(frozen=True, slots=True)
class ComboOptionTarget:
    option_type: str
    quantity: int
    target_moneyness: float

    def __post_init__(self) -> None:
        if self.option_type not in {"put", "call"}:
            raise ValueError("option_type must be put or call")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity == 0:
            raise ValueError("quantity must be a nonzero integer")
        target = float(self.target_moneyness)
        if not math.isfinite(target) or not 0 < target < 1.5:
            raise ValueError("invalid target moneyness")
        object.__setattr__(self, "target_moneyness", target)


@dataclass(frozen=True, slots=True)
class ComboStrategySpec:
    name: str
    family: str
    stock_shares: int
    option_targets: tuple[ComboOptionTarget, ...]
    target_dte: int = 35

    def __post_init__(self) -> None:
        families = {
            "stock_only",
            "covered_call",
            "protective_put",
            "collar",
            "covered_strangle",
            "bear_call_spread",
            "iron_condor",
        }
        if self.family not in families:
            raise ValueError(f"unsupported family {self.family!r}")
        if self.stock_shares not in {0, 100}:
            raise ValueError("stock_shares must be 0 or 100")
        object.__setattr__(self, "option_targets", tuple(self.option_targets))

    @property
    def option_contracts(self) -> int:
        return sum(abs(target.quantity) for target in self.option_targets)


@dataclass(frozen=True, slots=True)
class ComboSelectedLeg:
    option_type: str
    quantity: int
    contract: ContractObservation
    entry_price: float


@dataclass(frozen=True, slots=True)
class ComboTrade:
    strategy: str
    family: str
    execution_assumption: str
    decision_date: date
    expiration_date: date
    settlement_date: date
    entry_spot: float
    settlement_spot: float
    stock_shares: int
    option_contracts: int
    leg_symbols: tuple[str, ...]
    leg_types: tuple[str, ...]
    leg_quantities: tuple[int, ...]
    leg_strikes: tuple[float, ...]
    option_entry_cash_per_share: float
    total_entry_cash_per_share: float
    expiration_value_per_share: float
    fees: float
    pnl: float
    risk_capital: float
    cash_capital_required: float
    return_on_risk: float
    return_on_cash_capital: float
    matched_stock_pnl: float | None
    incremental_pnl_vs_stock: float | None


@dataclass(frozen=True, slots=True)
class ComboSkip:
    strategy: str
    execution_assumption: str
    decision_date: date
    reason: str


@dataclass(frozen=True, slots=True)
class ComboRun:
    spec: ComboStrategySpec
    execution: ExecutionAssumption
    trades: tuple[ComboTrade, ...]
    skips: tuple[ComboSkip, ...]


class ComboDataset:
    def __init__(self, put_db: str | Path = PUT_DB, call_db: str | Path = CALL_DB):
        self.put_db = Path(put_db)
        self.call_db = Path(call_db)
        self.put_data = HistoricalOptionResearchDataset(self.put_db)
        self.call_data = HistoricalOptionResearchDataset(self.call_db)
        self.put_snapshots = {
            snapshot.decision_date: snapshot for snapshot in self.put_data.snapshots
        }
        self.call_snapshots = {
            snapshot.decision_date: snapshot for snapshot in self.call_data.snapshots
        }
        self.decision_dates = tuple(sorted(set(self.call_snapshots) & set(self.put_snapshots)))
        if not self.decision_dates:
            raise ComboStrategyError("put and call archives have no overlapping decisions")

    def connect_call(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.call_db)
        connection.row_factory = sqlite3.Row
        return connection

    def entry_spot(self, decision_date: date, execution: ExecutionAssumption) -> float | None:
        start = datetime.combine(decision_date, time(15, 45), tzinfo=NY).astimezone(UTC)
        end = datetime.combine(decision_date, time(16, 0), tzinfo=NY).astimezone(UTC)
        with self.connect_call() as db:
            row = db.execute(
                """select max(high) from underlying_bars
                   where symbol='SPY' and timeframe='1Min'
                     and timestamp>=? and timestamp<=? and high is not null""",
                (start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")),
            ).fetchone()
        if row is None or row[0] is None or float(row[0]) <= 0:
            return None
        stock_bps = {
            "zero": 0.0,
            "base": 5.0,
            "worse": 10.0,
            "severe": 20.0,
        }.get(execution.name, 20.0)
        return float(row[0]) * (1.0 + stock_bps / 10_000.0)

    def settlement(self, expiration_date: date) -> tuple[date, float]:
        return self.call_data.settlement(expiration_date)


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


def _select_contract(
    snapshot: DecisionSnapshot,
    target: ComboOptionTarget,
    *,
    target_dte: int,
    expiration_date: date | None = None,
    excluded: set[str] | None = None,
) -> ContractObservation | None:
    excluded = excluded or set()
    candidates = [
        contract
        for contract in snapshot.contracts
        if contract.liquid_before_entry
        and contract.symbol not in excluded
        and (expiration_date is None or contract.expiration_date == expiration_date)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda contract: _contract_key(
            contract,
            target.target_moneyness,
            target_dte,
        ),
    )


def _select_legs(
    dataset: ComboDataset,
    decision_date: date,
    spec: ComboStrategySpec,
    execution: ExecutionAssumption,
) -> tuple[ComboSelectedLeg, ...] | None:
    selected: list[ComboSelectedLeg] = []
    expiry: date | None = None
    used: set[str] = set()
    for target in spec.option_targets:
        snapshot = (
            dataset.put_snapshots[decision_date]
            if target.option_type == "put"
            else dataset.call_snapshots[decision_date]
        )
        contract = _select_contract(
            snapshot,
            target,
            target_dte=spec.target_dte,
            expiration_date=expiry,
            excluded=used,
        )
        if contract is None or not contract.has_entry_observation:
            return None
        if expiry is None:
            expiry = contract.expiration_date
        if target.quantity < 0:
            price = execution.short_credit(float(contract.entry_low or 0.0))
        else:
            price = execution.long_debit(float(contract.entry_high or 0.0))
        if price is None or price <= 0:
            return None
        used.add(contract.symbol)
        selected.append(
            ComboSelectedLeg(target.option_type, target.quantity, contract, price)
        )
    return tuple(selected)


def _option_entry_cash(legs: Sequence[ComboSelectedLeg]) -> float:
    return sum(-leg.quantity * leg.entry_price for leg in legs)


def _option_payoff(legs: Sequence[ComboSelectedLeg], spot: float) -> float:
    total = 0.0
    for leg in legs:
        if leg.option_type == "put":
            intrinsic = max(leg.contract.strike - spot, 0.0)
        else:
            intrinsic = max(spot - leg.contract.strike, 0.0)
        total += leg.quantity * intrinsic
    return total


def _risk_capital(
    spec: ComboStrategySpec,
    legs: Sequence[ComboSelectedLeg],
    entry_spot: float,
    option_entry_cash: float,
) -> float:
    strikes = sorted({leg.contract.strike for leg in legs})
    high = max([entry_spot * 2.0, *(strike * 1.5 for strike in strikes)] or [entry_spot * 2.0])
    critical = [0.0, *strikes, high]
    values = []
    for spot in critical:
        stock_value = spot if spec.stock_shares else 0.0
        entry_cash = -entry_spot if spec.stock_shares else 0.0
        values.append(entry_cash + option_entry_cash + stock_value + _option_payoff(legs, spot))
    minimum = min(values)
    return max(0.0, -minimum * 100.0)


def simulate_combo_strategy(
    dataset: ComboDataset,
    spec: ComboStrategySpec,
    execution: ExecutionAssumption,
) -> ComboRun:
    trades: list[ComboTrade] = []
    skips: list[ComboSkip] = []
    for decision_date in dataset.decision_dates:
        entry_spot = dataset.entry_spot(decision_date, execution)
        if entry_spot is None:
            skips.append(ComboSkip(spec.name, execution.name, decision_date, "entry_spot_unavailable"))
            continue
        legs = _select_legs(dataset, decision_date, spec, execution)
        if legs is None and spec.option_targets:
            skips.append(ComboSkip(spec.name, execution.name, decision_date, "option_leg_unavailable"))
            continue
        legs = legs or ()
        expiration = (
            legs[0].contract.expiration_date
            if legs
            else dataset.call_snapshots[decision_date].contracts[0].expiration_date
        )
        if any(leg.contract.expiration_date != expiration for leg in legs):
            raise ComboStrategyError("combo legs must share expiration")
        settlement_date, settlement_spot = dataset.settlement(expiration)
        option_cash = _option_entry_cash(legs)
        stock_entry_cash = -entry_spot if spec.stock_shares else 0.0
        total_entry_cash = stock_entry_cash + option_cash
        stock_exit_bps = {
            "zero": 0.0,
            "base": 5.0,
            "worse": 10.0,
            "severe": 20.0,
        }.get(execution.name, 20.0)
        liquidated_stock_value = settlement_spot * (
            1.0 - stock_exit_bps / 10_000.0
        )
        stock_value = liquidated_stock_value if spec.stock_shares else 0.0
        expiration_value = stock_value + _option_payoff(legs, settlement_spot)
        fees = execution.lifecycle_fees(max(1, spec.option_contracts)) if spec.option_contracts else 0.0
        pnl = (total_entry_cash + expiration_value) * 100.0 - fees
        risk = _risk_capital(spec, legs, entry_spot, option_cash)
        if risk <= 0:
            skips.append(ComboSkip(spec.name, execution.name, decision_date, "risk_capital_nonpositive"))
            continue
        if spec.stock_shares:
            net_cash_outlay = max(0.0, entry_spot * 100.0 - option_cash * 100.0)
            cash_capital = max(net_cash_outlay, risk)
            matched_stock_pnl = (liquidated_stock_value - entry_spot) * 100.0
            incremental_pnl = pnl - matched_stock_pnl
        else:
            cash_capital = risk
            matched_stock_pnl = None
            incremental_pnl = None
        if cash_capital <= 0:
            skips.append(ComboSkip(spec.name, execution.name, decision_date, "cash_capital_nonpositive"))
            continue
        trades.append(
            ComboTrade(
                strategy=spec.name,
                family=spec.family,
                execution_assumption=execution.name,
                decision_date=decision_date,
                expiration_date=expiration,
                settlement_date=settlement_date,
                entry_spot=entry_spot,
                settlement_spot=settlement_spot,
                stock_shares=spec.stock_shares,
                option_contracts=spec.option_contracts,
                leg_symbols=tuple(leg.contract.symbol for leg in legs),
                leg_types=tuple(leg.option_type for leg in legs),
                leg_quantities=tuple(leg.quantity for leg in legs),
                leg_strikes=tuple(leg.contract.strike for leg in legs),
                option_entry_cash_per_share=option_cash,
                total_entry_cash_per_share=total_entry_cash,
                expiration_value_per_share=expiration_value,
                fees=fees,
                pnl=pnl,
                risk_capital=risk,
                cash_capital_required=cash_capital,
                return_on_risk=pnl / risk,
                return_on_cash_capital=pnl / cash_capital,
                matched_stock_pnl=matched_stock_pnl,
                incremental_pnl_vs_stock=incremental_pnl,
            )
        )
    return ComboRun(spec, execution, tuple(trades), tuple(skips))


def fixed_combo_specs() -> tuple[ComboStrategySpec, ...]:
    specs = [ComboStrategySpec("stock_only", "stock_only", 100, ())]
    for call_target in (1.04, 1.06, 1.08):
        specs.append(
            ComboStrategySpec(
                f"covered_call_m{int(call_target*100)}",
                "covered_call",
                100,
                (ComboOptionTarget("call", -1, call_target),),
            )
        )
    for put_target in (0.94, 0.96):
        specs.append(
            ComboStrategySpec(
                f"protective_put_m{int(put_target*100)}",
                "protective_put",
                100,
                (ComboOptionTarget("put", 1, put_target),),
            )
        )
    for put_target, call_target in ((0.94, 1.06), (0.94, 1.08), (0.96, 1.06)):
        specs.append(
            ComboStrategySpec(
                f"collar_p{int(put_target*100)}_c{int(call_target*100)}",
                "collar",
                100,
                (
                    ComboOptionTarget("put", 1, put_target),
                    ComboOptionTarget("call", -1, call_target),
                ),
            )
        )
    for short_call, long_call in ((1.04, 1.08), (1.06, 1.08)):
        specs.append(
            ComboStrategySpec(
                f"bear_call_c{int(short_call*100)}_c{int(long_call*100)}",
                "bear_call_spread",
                0,
                (
                    ComboOptionTarget("call", -1, short_call),
                    ComboOptionTarget("call", 1, long_call),
                ),
            )
        )
    for put_target, call_target in ((0.94, 1.06), (0.92, 1.08)):
        specs.append(
            ComboStrategySpec(
                f"covered_strangle_p{int(put_target*100)}_c{int(call_target*100)}",
                "covered_strangle",
                100,
                (
                    ComboOptionTarget("put", -1, put_target),
                    ComboOptionTarget("call", -1, call_target),
                ),
            )
        )
    condors = (
        (0.94, 0.90, 1.06, 1.08),
        (0.96, 0.92, 1.06, 1.08),
    )
    for short_put, long_put, short_call, long_call in condors:
        specs.append(
            ComboStrategySpec(
                f"iron_condor_p{int(short_put*100)}_{int(long_put*100)}_c{int(short_call*100)}_{int(long_call*100)}",
                "iron_condor",
                0,
                (
                    ComboOptionTarget("put", -1, short_put),
                    ComboOptionTarget("put", 1, long_put),
                    ComboOptionTarget("call", -1, short_call),
                    ComboOptionTarget("call", 1, long_call),
                ),
            )
        )
    return tuple(specs)


def _peak_capital(trades: Sequence[ComboTrade]) -> tuple[int, int, float, float]:
    active: list[ComboTrade] = []
    max_positions = 0
    max_contracts = 0
    peak_risk = 0.0
    peak_cash = 0.0
    events = []
    for trade in trades:
        events.append((trade.decision_date, 1, trade))
        events.append((trade.settlement_date, -1, trade))
    events.sort(key=lambda row: (row[0], -row[1]))
    for _, direction, trade in events:
        if direction > 0:
            active.append(trade)
        else:
            if trade in active:
                active.remove(trade)
        max_positions = max(max_positions, len(active))
        max_contracts = max(max_contracts, sum(item.option_contracts for item in active))
        peak_risk = max(peak_risk, sum(item.risk_capital for item in active))
        peak_cash = max(
            peak_cash,
            sum(item.cash_capital_required for item in active),
        )
    return max_positions, max_contracts, peak_risk, peak_cash


def _one_position(trades: Sequence[ComboTrade]) -> tuple[ComboTrade, ...]:
    result = []
    active_until: date | None = None
    for trade in sorted(trades, key=lambda row: row.decision_date):
        if active_until is not None and active_until >= trade.decision_date:
            continue
        result.append(trade)
        active_until = trade.settlement_date
    return tuple(result)


def summarize_combo(run: ComboRun, start: date, end: date, decisions: int) -> dict[str, Any]:
    trades = tuple(trade for trade in run.trades if start <= trade.decision_date <= end)
    skips = tuple(skip for skip in run.skips if start <= skip.decision_date <= end)
    pnls = [trade.pnl for trade in trades]
    risk_returns = [trade.return_on_risk for trade in trades]
    cash_returns = [trade.return_on_cash_capital for trade in trades]
    incremental = [
        trade.incremental_pnl_vs_stock
        for trade in trades
        if trade.incremental_pnl_vs_stock is not None
    ]
    max_positions, max_contracts, peak_risk, peak_cash = _peak_capital(trades)
    capped = _one_position(trades)
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "decision_dates": decisions,
        "trades": len(trades),
        "trade_frequency": len(trades) / decisions if decisions else None,
        "total_pnl": sum(pnls),
        "mean_pnl": _mean(pnls),
        "mean_return_on_risk": _mean(risk_returns),
        "mean_return_on_cash_capital": _mean(cash_returns),
        "win_rate": sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "worst_trade_pnl": min(pnls) if pnls else None,
        "average_risk_capital": _mean([trade.risk_capital for trade in trades]),
        "average_cash_capital_required": _mean(
            [trade.cash_capital_required for trade in trades]
        ),
        "average_option_entry_cash": _mean([trade.option_entry_cash_per_share * 100.0 for trade in trades]),
        "matched_stock_total_pnl": sum(
            trade.matched_stock_pnl
            for trade in trades
            if trade.matched_stock_pnl is not None
        ) if incremental else None,
        "incremental_total_pnl_vs_stock": sum(incremental) if incremental else None,
        "incremental_mean_pnl_vs_stock": _mean(incremental),
        "max_concurrent_positions": max_positions,
        "max_concurrent_option_contracts": max_contracts,
        "peak_combined_risk_capital": peak_risk,
        "peak_combined_cash_capital": peak_cash,
        "return_on_peak_risk_capital": sum(pnls) / peak_risk if peak_risk > 0 else None,
        "return_on_peak_cash_capital": sum(pnls) / peak_cash if peak_cash > 0 else None,
        "skip_reasons": dict(Counter(skip.reason for skip in skips)),
        "one_position_cap": {
            "trades": len(capped),
            "total_pnl": sum(trade.pnl for trade in capped),
            "incremental_total_pnl_vs_stock": sum(
                trade.incremental_pnl_vs_stock
                for trade in capped
                if trade.incremental_pnl_vs_stock is not None
            ) if any(
                trade.incremental_pnl_vs_stock is not None for trade in capped
            ) else None,
        },
    }


def run_combo_lab(
    put_db: str | Path = PUT_DB,
    call_db: str | Path = CALL_DB,
    *,
    output_directory: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    dataset = ComboDataset(put_db, call_db)
    specs = fixed_combo_specs()
    rows = []
    runs = {}
    for spec in specs:
        for execution in EXECUTION_ASSUMPTIONS:
            run = simulate_combo_strategy(dataset, spec, execution)
            runs[(spec.name, execution.name)] = run
            rows.append(
                {
                    "strategy": spec.name,
                    "family": spec.family,
                    "stock_shares": spec.stock_shares,
                    "option_contracts": spec.option_contracts,
                    "execution_assumption": execution.name,
                    "last_6_months": summarize_combo(run, date(2026, 1, 1), date(2026, 6, 1), 6),
                    "last_3_months": summarize_combo(run, date(2026, 4, 1), date(2026, 6, 1), 3),
                }
            )
    severe = [row for row in rows if row["execution_assumption"] == "severe"]
    ranked = [
        row for row in severe
        if row["last_6_months"]["trades"] >= 2
        and row["last_6_months"]["total_pnl"] > 0
    ]
    ranked.sort(
        key=lambda row: (
            row["last_6_months"]["return_on_peak_cash_capital"] or -math.inf,
            row["last_6_months"]["total_pnl"],
        ),
        reverse=True,
    )
    incremental_ranked = [
        row for row in severe
        if row["stock_shares"]
        and row["last_6_months"]["trades"] >= 2
        and float(
            row["last_6_months"]["incremental_total_pnl_vs_stock"] or 0.0
        )
        > 0
    ]
    incremental_ranked.sort(
        key=lambda row: (
            row["last_6_months"]["incremental_total_pnl_vs_stock"],
            row["last_6_months"]["return_on_peak_cash_capital"] or -math.inf,
        ),
        reverse=True,
    )
    defined_risk_ranked = [
        row for row in severe
        if not row["stock_shares"]
        and row["last_6_months"]["trades"] >= 2
    ]
    defined_risk_ranked.sort(
        key=lambda row: (
            row["last_6_months"]["total_pnl"],
            row["last_6_months"]["return_on_peak_cash_capital"] or -math.inf,
        ),
        reverse=True,
    )
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RECENT_CALL_COMBO_EXPLORATORY_ONLY_NO_HISTORICAL_NBBO",
        "research_claims_allowed": False,
        "fixed_strategy_count": len(specs),
        "decision_dates": [day.isoformat() for day in dataset.decision_dates],
        "rankings": ranked,
        "incremental_rankings_vs_stock": incremental_ranked,
        "defined_risk_rankings": defined_risk_ranked,
        "all_results": rows,
        "caveats": [
            "Only six monthly decisions are available.",
            "Historical option bid/ask and quote size are absent.",
            "Stock entry uses the highest SPY minute-bar high in the entry window plus slippage.",
            "Expiration stock value uses the daily close less modeled exit slippage.",
            "Covered-strangle capital assumes the short put is cash secured in addition to the stock position.",
            "No dividend cash flows are included.",
        ],
    }
    write_combo_outputs(payload, runs, output_directory)
    return payload


def write_combo_outputs(payload: Mapping[str, Any], runs: Mapping[tuple[str, str], ComboRun], output_directory: str | Path) -> None:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / "recent_call_combo_report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    with (output / "recent_call_combo_rankings.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = [
            "strategy", "family", "execution_assumption", "six_month_trades",
            "six_month_pnl", "six_month_return_on_peak_cash_capital",
            "six_month_peak_cash_capital", "six_month_incremental_pnl_vs_stock",
            "three_month_trades", "three_month_pnl",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in payload["all_results"]:
            six = row["last_6_months"]
            three = row["last_3_months"]
            writer.writerow({
                "strategy": row["strategy"],
                "family": row["family"],
                "execution_assumption": row["execution_assumption"],
                "six_month_trades": six["trades"],
                "six_month_pnl": six["total_pnl"],
                "six_month_return_on_peak_cash_capital": six["return_on_peak_cash_capital"],
                "six_month_peak_cash_capital": six["peak_combined_cash_capital"],
                "six_month_incremental_pnl_vs_stock": six["incremental_total_pnl_vs_stock"],
                "three_month_trades": three["trades"],
                "three_month_pnl": three["total_pnl"],
            })
    with (output / "recent_call_combo_trades.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = list(ComboTrade.__dataclass_fields__)
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for key in sorted(runs):
            for trade in runs[key].trades:
                row = asdict(trade)
                for field in ("decision_date", "expiration_date", "settlement_date"):
                    row[field] = row[field].isoformat()
                for field in ("leg_symbols", "leg_types", "leg_quantities", "leg_strikes"):
                    row[field] = json.dumps(row[field])
                writer.writerow(row)
