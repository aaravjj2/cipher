"""Deterministic synthetic stress tests for the primary options candidate.

This module tests accounting and payoff behavior only. The quotes and underlying
paths are deliberately synthetic and MUST NOT be interpreted as evidence of a
tradable edge, expected return, win rate, or historical performance.

It compares two implementations of the same short-put thesis:

* a fully cash-secured short put; and
* a defined-risk short-put vertical with a farther-OTM protective put.

The harness routes all entries and quote-based exits through the strict
point-in-time execution engine, including observed-side fills, slippage, fees,
and collateral calculations. Expiration scenarios use intrinsic settlement.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import math
from typing import Iterable

try:
    from .option_backtest_engine import (
        ExecutionConfig,
        OptionContract,
        OptionLeg,
        OptionQuote,
        PointInTimeOptionsEngine,
    )
except ImportError:  # Direct module import in tests/scripts.
    from option_backtest_engine import (
        ExecutionConfig,
        OptionContract,
        OptionLeg,
        OptionQuote,
        PointInTimeOptionsEngine,
    )


ENTRY_TIMESTAMP = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
CLOSE_TIMESTAMP = datetime(2026, 1, 16, 15, 0, tzinfo=timezone.utc)
EXPIRATION_TIMESTAMP = datetime(2026, 2, 6, 21, 0, tzinfo=timezone.utc)
EXPIRATION = date(2026, 2, 6)


@dataclass(frozen=True, slots=True)
class SyntheticScenario:
    name: str
    description: str
    exit_method: str
    exit_spot: float | None = None
    short_put_exit_bid: float | None = None
    short_put_exit_ask: float | None = None
    long_put_exit_bid: float | None = None
    long_put_exit_ask: float | None = None

    def __post_init__(self) -> None:
        method = self.exit_method.lower()
        if method not in {"close", "expire"}:
            raise ValueError("exit_method must be 'close' or 'expire'")
        object.__setattr__(self, "exit_method", method)
        if method == "expire":
            if self.exit_spot is None or self.exit_spot < 0:
                raise ValueError("expiration scenario requires non-negative exit_spot")
        else:
            values = (
                self.short_put_exit_bid,
                self.short_put_exit_ask,
                self.long_put_exit_bid,
                self.long_put_exit_ask,
            )
            if any(value is None or value < 0 for value in values):
                raise ValueError("close scenario requires non-negative exit quotes")
            if self.short_put_exit_ask < self.short_put_exit_bid:
                raise ValueError("short-put exit quote is crossed")
            if self.long_put_exit_ask < self.long_put_exit_bid:
                raise ValueError("long-put exit quote is crossed")


@dataclass(frozen=True, slots=True)
class SyntheticStructureOutcome:
    scenario: str
    structure: str
    exit_method: str
    entry_net_credit: float
    collateral_required: float
    pnl: float
    return_on_collateral: float
    theoretical_max_loss: float
    pnl_as_fraction_of_max_loss: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SyntheticStressReport:
    status: str
    evidence_scope: str
    assumptions: dict[str, object]
    outcomes: tuple[SyntheticStructureOutcome, ...]
    comparisons: tuple[dict[str, object], ...]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "evidence_scope": self.evidence_scope,
            "assumptions": dict(self.assumptions),
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "comparisons": [dict(comparison) for comparison in self.comparisons],
        }


@dataclass(frozen=True, slots=True)
class ExpirationPayoffPoint:
    spot: float
    cash_secured_put_pnl: float
    defined_risk_put_vertical_pnl: float

    def to_dict(self) -> dict:
        return asdict(self)


def default_scenarios() -> tuple[SyntheticScenario, ...]:
    """Return deterministic paths spanning decay, friction, and tail outcomes."""
    return (
        SyntheticScenario(
            name="calm_decay_close",
            description="Option premium decays and both structures close before expiry.",
            exit_method="close",
            short_put_exit_bid=2.00,
            short_put_exit_ask=2.20,
            long_put_exit_bid=0.40,
            long_put_exit_ask=0.50,
        ),
        SyntheticScenario(
            name="unchanged_but_spreads_widen",
            description="Underlying thesis is unchanged but executable spreads deteriorate.",
            exit_method="close",
            short_put_exit_bid=8.50,
            short_put_exit_ask=11.50,
            long_put_exit_bid=1.20,
            long_put_exit_ask=3.20,
        ),
        SyntheticScenario(
            name="expires_above_short_strike",
            description="Both puts expire out of the money and retain the entry credit.",
            exit_method="expire",
            exit_spot=520.0,
        ),
        SyntheticScenario(
            name="moderate_decline_at_expiry",
            description="The short put finishes modestly in the money; the wing is inactive.",
            exit_method="expire",
            exit_spot=485.0,
        ),
        SyntheticScenario(
            name="severe_crash_at_expiry",
            description="Both puts finish deep in the money and the vertical reaches max loss.",
            exit_method="expire",
            exit_spot=400.0,
        ),
    )


def _contracts() -> tuple[OptionContract, OptionContract]:
    short_put = OptionContract(
        symbol="XSP260206P00500000",
        underlying="XSP",
        option_type="put",
        strike=500.0,
        expiration=EXPIRATION,
        exercise_style="european",
        settlement="cash",
    )
    long_put = OptionContract(
        symbol="XSP260206P00450000",
        underlying="XSP",
        option_type="put",
        strike=450.0,
        expiration=EXPIRATION,
        exercise_style="european",
        settlement="cash",
    )
    return short_put, long_put


def _quotes_for_scenario(scenario: SyntheticScenario) -> tuple[OptionQuote, ...]:
    short_put, long_put = _contracts()
    quotes = [
        OptionQuote(
            contract=short_put,
            timestamp=ENTRY_TIMESTAMP,
            bid=10.00,
            ask=10.40,
            last=10.20,
            implied_volatility=0.24,
            delta=-0.22,
        ),
        OptionQuote(
            contract=long_put,
            timestamp=ENTRY_TIMESTAMP,
            bid=1.90,
            ask=2.20,
            last=2.05,
            implied_volatility=0.29,
            delta=-0.08,
        ),
    ]
    if scenario.exit_method == "close":
        quotes.extend(
            [
                OptionQuote(
                    contract=short_put,
                    timestamp=CLOSE_TIMESTAMP,
                    bid=float(scenario.short_put_exit_bid),
                    ask=float(scenario.short_put_exit_ask),
                ),
                OptionQuote(
                    contract=long_put,
                    timestamp=CLOSE_TIMESTAMP,
                    bid=float(scenario.long_put_exit_bid),
                    ask=float(scenario.long_put_exit_ask),
                ),
            ]
        )
    return tuple(quotes)


def _run_structure(
    scenario: SyntheticScenario,
    *,
    structure: str,
    config: ExecutionConfig,
) -> SyntheticStructureOutcome:
    short_put, long_put = _contracts()
    if structure == "cash_secured_put":
        legs = (OptionLeg(short_put, -1),)
    elif structure == "defined_risk_put_vertical":
        legs = (OptionLeg(short_put, -1), OptionLeg(long_put, 1))
    else:
        raise ValueError(f"unsupported structure {structure!r}")

    engine = PointInTimeOptionsEngine(_quotes_for_scenario(scenario), config=config)
    position = engine.open_position(
        structure,
        ENTRY_TIMESTAMP,
        legs,
        cash_available=100_000.0,
        metadata={"synthetic": True, "scenario": scenario.name},
    )
    entry_credit = position.entry_execution.net_cash_flow

    if scenario.exit_method == "close":
        pnl = engine.close_position(position, CLOSE_TIMESTAMP).pnl
    else:
        pnl = engine.settle_expiration(
            position,
            timestamp=EXPIRATION_TIMESTAMP,
            spot=float(scenario.exit_spot),
        ).pnl

    if structure == "cash_secured_put":
        gross_tail_obligation = short_put.strike * short_put.multiplier
    else:
        gross_tail_obligation = (
            short_put.strike - long_put.strike
        ) * short_put.multiplier
    theoretical_max_loss = max(gross_tail_obligation - entry_credit, 0.0)
    collateral = position.collateral_required

    return SyntheticStructureOutcome(
        scenario=scenario.name,
        structure=structure,
        exit_method=scenario.exit_method,
        entry_net_credit=round(entry_credit, 6),
        collateral_required=round(collateral, 6),
        pnl=round(pnl, 6),
        return_on_collateral=round(pnl / collateral, 8) if collateral else 0.0,
        theoretical_max_loss=round(theoretical_max_loss, 6),
        pnl_as_fraction_of_max_loss=(
            round(pnl / theoretical_max_loss, 8) if theoretical_max_loss else 0.0
        ),
    )


def expiration_payoff_surface(
    spots: Iterable[float],
    *,
    execution_config: ExecutionConfig | None = None,
) -> tuple[ExpirationPayoffPoint, ...]:
    """Evaluate both structures across deterministic expiration spots.

    This is a payoff-geometry check, not a probabilistic simulation. Input order
    is preserved so callers can test monotonicity and plateaus explicitly.
    """
    selected = tuple(float(spot) for spot in spots)
    if not selected:
        raise ValueError("at least one expiration spot is required")
    if any(not math.isfinite(spot) or spot < 0 for spot in selected):
        raise ValueError("expiration spots must be finite and non-negative")

    config = execution_config or ExecutionConfig(
        entry_slippage_bps=5.0,
        exit_slippage_bps=5.0,
        max_quote_age_seconds=60,
        commission_per_contract=0.65,
        exchange_fee_per_contract=0.03,
    )
    points: list[ExpirationPayoffPoint] = []
    for index, spot in enumerate(selected):
        scenario = SyntheticScenario(
            name=f"payoff_surface_{index}",
            description="Synthetic expiration payoff-surface point.",
            exit_method="expire",
            exit_spot=spot,
        )
        cash_secured = _run_structure(
            scenario,
            structure="cash_secured_put",
            config=config,
        )
        vertical = _run_structure(
            scenario,
            structure="defined_risk_put_vertical",
            config=config,
        )
        points.append(
            ExpirationPayoffPoint(
                spot=spot,
                cash_secured_put_pnl=cash_secured.pnl,
                defined_risk_put_vertical_pnl=vertical.pnl,
            )
        )
    return tuple(points)


def run_synthetic_stress_suite(
    scenarios: Iterable[SyntheticScenario] | None = None,
    *,
    execution_config: ExecutionConfig | None = None,
) -> SyntheticStressReport:
    """Run the deterministic comparison through the strict execution engine."""
    selected = tuple(scenarios) if scenarios is not None else default_scenarios()
    if not selected:
        raise ValueError("at least one synthetic scenario is required")
    config = execution_config or ExecutionConfig(
        entry_slippage_bps=5.0,
        exit_slippage_bps=5.0,
        max_quote_age_seconds=60,
        commission_per_contract=0.65,
        exchange_fee_per_contract=0.03,
    )

    outcomes: list[SyntheticStructureOutcome] = []
    comparisons: list[dict[str, object]] = []
    for scenario in selected:
        cash_secured = _run_structure(
            scenario,
            structure="cash_secured_put",
            config=config,
        )
        vertical = _run_structure(
            scenario,
            structure="defined_risk_put_vertical",
            config=config,
        )
        outcomes.extend((cash_secured, vertical))
        comparisons.append(
            {
                "scenario": scenario.name,
                "cash_secured_put_pnl": cash_secured.pnl,
                "defined_risk_put_vertical_pnl": vertical.pnl,
                "vertical_minus_cash_secured_pnl": round(
                    vertical.pnl - cash_secured.pnl,
                    6,
                ),
                "lower_tail_loss_structure": (
                    "defined_risk_put_vertical"
                    if vertical.theoretical_max_loss < cash_secured.theoretical_max_loss
                    else "cash_secured_put"
                ),
            }
        )

    return SyntheticStressReport(
        status="SYNTHETIC_ACCOUNTING_TEST_ONLY",
        evidence_scope=(
            "Deterministic validation of fills, fees, collateral, expiration payoff, "
            "and tail-loss geometry. Not historical or predictive evidence."
        ),
        assumptions={
            "underlying": "XSP",
            "short_put_strike": 500.0,
            "protective_put_strike": 450.0,
            "contract_multiplier": 100,
            "entry_short_put_bid_ask": [10.0, 10.4],
            "entry_protective_put_bid_ask": [1.9, 2.2],
            "entry_slippage_bps": config.entry_slippage_bps,
            "exit_slippage_bps": config.exit_slippage_bps,
            "per_contract_fill_fee": config.per_contract_fill_fee,
        },
        outcomes=tuple(outcomes),
        comparisons=tuple(comparisons),
    )
