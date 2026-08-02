"""Research specifications for literature-backed options strategies.

This module is intentionally read-only. It does not place, stage, or recommend
live orders. Its purpose is to turn published strategy hypotheses into explicit,
testable gates for the strict point-in-time options backtester.

The primary candidate is a conditional broad-index put-write:

* baseline replication: fully cash-secured monthly put writing;
* safety adaptation: defined-risk put vertical using the same entry signal.

Neither variant is considered validated until it passes the project's strict
point-in-time dataset gate and out-of-sample tests across multiple regimes.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence


class StrategyInputError(ValueError):
    """Raised when a research signal receives invalid or incomplete inputs."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyInputError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite_number(value: float, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StrategyInputError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise StrategyInputError(f"{field_name} must be a finite number")
    return number


def _finite_positive(value: float, *, field_name: str) -> float:
    number = _finite_number(value, field_name=field_name)
    if number <= 0:
        raise StrategyInputError(f"{field_name} must be finite and positive")
    return number


def annualized_realized_volatility(
    closes: Sequence[float],
    *,
    periods_per_year: int = 252,
) -> float:
    """Calculate close-to-close annualized realized volatility.

    At least three closes are required so that the sample standard deviation of
    log returns is defined. The result is returned as a decimal (0.20 = 20%).
    """
    if periods_per_year <= 0:
        raise StrategyInputError("periods_per_year must be positive")
    if len(closes) < 3:
        raise StrategyInputError("at least three closes are required")

    normalized = [
        _finite_positive(value, field_name=f"closes[{index}]")
        for index, value in enumerate(closes)
    ]
    log_returns = [
        math.log(normalized[index] / normalized[index - 1])
        for index in range(1, len(normalized))
    ]
    mean_return = sum(log_returns) / len(log_returns)
    variance = sum((value - mean_return) ** 2 for value in log_returns) / (
        len(log_returns) - 1
    )
    return math.sqrt(variance * periods_per_year)


@dataclass(frozen=True)
class ConditionalPutWriteConfig:
    """Conservative research gates for the primary strategy candidate.

    Thresholds are hypotheses to test, not proven optimal parameters. The
    literature supports conditioning option writing on a positive volatility
    premium; the exact production thresholds must be selected only through
    embargoed walk-forward analysis.
    """

    allowed_underlyings: tuple[str, ...] = ("SPX", "XSP", "SPY")
    minimum_iv_rv_ratio: float = 1.15
    minimum_vix_percentile: float = 0.60
    maximum_vix_percentile: float = 0.95
    maximum_front_second_vix_ratio: float = 1.05
    minimum_dte: int = 28
    maximum_dte: int = 45
    minimum_short_put_abs_delta: float = 0.15
    maximum_short_put_abs_delta: float = 0.30
    maximum_bid_ask_pct: float = 0.12
    require_spot_above_200d_average: bool = True
    require_observed_quotes: bool = True

    def __post_init__(self) -> None:
        normalized_underlyings = tuple(
            dict.fromkeys(str(value).upper().strip() for value in self.allowed_underlyings)
        )
        if not normalized_underlyings or any(not value for value in normalized_underlyings):
            raise StrategyInputError("allowed_underlyings cannot be empty")
        object.__setattr__(self, "allowed_underlyings", normalized_underlyings)

        minimum_iv_rv_ratio = _finite_positive(
            self.minimum_iv_rv_ratio,
            field_name="minimum_iv_rv_ratio",
        )
        minimum_vix_percentile = _finite_number(
            self.minimum_vix_percentile,
            field_name="minimum_vix_percentile",
        )
        maximum_vix_percentile = _finite_number(
            self.maximum_vix_percentile,
            field_name="maximum_vix_percentile",
        )
        maximum_front_second_vix_ratio = _finite_positive(
            self.maximum_front_second_vix_ratio,
            field_name="maximum_front_second_vix_ratio",
        )
        minimum_short_put_abs_delta = _finite_number(
            self.minimum_short_put_abs_delta,
            field_name="minimum_short_put_abs_delta",
        )
        maximum_short_put_abs_delta = _finite_number(
            self.maximum_short_put_abs_delta,
            field_name="maximum_short_put_abs_delta",
        )
        maximum_bid_ask_pct = _finite_positive(
            self.maximum_bid_ask_pct,
            field_name="maximum_bid_ask_pct",
        )

        if minimum_iv_rv_ratio <= 1.0:
            raise StrategyInputError("minimum_iv_rv_ratio must exceed 1.0")
        if not 0 <= minimum_vix_percentile < maximum_vix_percentile <= 1:
            raise StrategyInputError("invalid VIX percentile range")
        if not isinstance(self.minimum_dte, int) or isinstance(self.minimum_dte, bool):
            raise StrategyInputError("minimum_dte must be an integer")
        if not isinstance(self.maximum_dte, int) or isinstance(self.maximum_dte, bool):
            raise StrategyInputError("maximum_dte must be an integer")
        if not 0 <= self.minimum_dte <= self.maximum_dte:
            raise StrategyInputError("invalid DTE range")
        if not 0 < minimum_short_put_abs_delta <= maximum_short_put_abs_delta < 1:
            raise StrategyInputError("invalid short-put delta range")

        object.__setattr__(self, "minimum_iv_rv_ratio", minimum_iv_rv_ratio)
        object.__setattr__(self, "minimum_vix_percentile", minimum_vix_percentile)
        object.__setattr__(self, "maximum_vix_percentile", maximum_vix_percentile)
        object.__setattr__(
            self,
            "maximum_front_second_vix_ratio",
            maximum_front_second_vix_ratio,
        )
        object.__setattr__(
            self,
            "minimum_short_put_abs_delta",
            minimum_short_put_abs_delta,
        )
        object.__setattr__(
            self,
            "maximum_short_put_abs_delta",
            maximum_short_put_abs_delta,
        )
        object.__setattr__(self, "maximum_bid_ask_pct", maximum_bid_ask_pct)


@dataclass(frozen=True)
class OptionMarketState:
    timestamp: datetime
    underlying: str
    spot: float
    atm_implied_volatility_30d: float
    realized_volatility_30d: float
    vix_percentile: float
    front_second_vix_ratio: float
    spot_above_200d_average: bool
    candidate_dte: int
    candidate_short_put_abs_delta: float
    candidate_bid_ask_pct: float
    observed_bid_ask: bool

    def __post_init__(self) -> None:
        timestamp = _aware_utc(self.timestamp)
        underlying = str(self.underlying).upper().strip()
        if not underlying:
            raise StrategyInputError("underlying cannot be empty")
        spot = _finite_positive(self.spot, field_name="spot")
        implied = _finite_positive(
            self.atm_implied_volatility_30d,
            field_name="atm_implied_volatility_30d",
        )
        realized = _finite_positive(
            self.realized_volatility_30d,
            field_name="realized_volatility_30d",
        )
        vix_percentile = _finite_number(
            self.vix_percentile,
            field_name="vix_percentile",
        )
        front_second_vix_ratio = _finite_positive(
            self.front_second_vix_ratio,
            field_name="front_second_vix_ratio",
        )
        short_put_delta = _finite_number(
            self.candidate_short_put_abs_delta,
            field_name="candidate_short_put_abs_delta",
        )
        bid_ask_pct = _finite_number(
            self.candidate_bid_ask_pct,
            field_name="candidate_bid_ask_pct",
        )

        if not 0 <= vix_percentile <= 1:
            raise StrategyInputError("vix_percentile must be between 0 and 1")
        if not isinstance(self.candidate_dte, int) or isinstance(self.candidate_dte, bool):
            raise StrategyInputError("candidate_dte must be an integer")
        if self.candidate_dte < 0:
            raise StrategyInputError("candidate_dte cannot be negative")
        if not 0 < short_put_delta < 1:
            raise StrategyInputError("candidate_short_put_abs_delta must be between 0 and 1")
        if bid_ask_pct < 0:
            raise StrategyInputError("candidate_bid_ask_pct cannot be negative")
        if not isinstance(self.spot_above_200d_average, bool):
            raise StrategyInputError("spot_above_200d_average must be boolean")
        if not isinstance(self.observed_bid_ask, bool):
            raise StrategyInputError("observed_bid_ask must be boolean")
        if not math.isfinite(implied / realized):
            raise StrategyInputError("iv_rv_ratio must be finite")

        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "underlying", underlying)
        object.__setattr__(self, "spot", spot)
        object.__setattr__(self, "atm_implied_volatility_30d", implied)
        object.__setattr__(self, "realized_volatility_30d", realized)
        object.__setattr__(self, "vix_percentile", vix_percentile)
        object.__setattr__(self, "front_second_vix_ratio", front_second_vix_ratio)
        object.__setattr__(self, "candidate_short_put_abs_delta", short_put_delta)
        object.__setattr__(self, "candidate_bid_ask_pct", bid_ask_pct)

    @property
    def iv_rv_ratio(self) -> float:
        return self.atm_implied_volatility_30d / self.realized_volatility_30d


@dataclass(frozen=True)
class StrategyDecision:
    eligible: bool
    strategy_name: str
    benchmark_structure: str
    safety_adaptation: str
    iv_rv_ratio: float
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    required_backtests: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_conditional_index_put_write(
    state: OptionMarketState,
    config: ConditionalPutWriteConfig | None = None,
) -> StrategyDecision:
    """Evaluate whether a market observation enters the research sample.

    Passing this gate means only that the observation qualifies for a backtest
    event. It is not a live-trading recommendation and does not imply a proven
    positive expected return.
    """
    config = config or ConditionalPutWriteConfig()
    blockers: list[str] = []
    warnings: list[str] = []

    if state.underlying not in config.allowed_underlyings:
        blockers.append("underlying_not_approved_broad_index")
    if state.iv_rv_ratio < config.minimum_iv_rv_ratio:
        blockers.append("implied_realized_volatility_ratio_too_low")
    if state.vix_percentile < config.minimum_vix_percentile:
        blockers.append("implied_volatility_regime_not_elevated")
    if state.vix_percentile > config.maximum_vix_percentile:
        blockers.append("acute_volatility_regime_excluded")
    if state.front_second_vix_ratio > config.maximum_front_second_vix_ratio:
        blockers.append("vix_term_structure_in_stress_backwardation")
    if config.require_spot_above_200d_average and not state.spot_above_200d_average:
        blockers.append("long_term_trend_filter_failed")
    if not config.minimum_dte <= state.candidate_dte <= config.maximum_dte:
        blockers.append("candidate_expiration_outside_dte_window")
    if not (
        config.minimum_short_put_abs_delta
        <= state.candidate_short_put_abs_delta
        <= config.maximum_short_put_abs_delta
    ):
        blockers.append("candidate_short_put_delta_outside_window")
    if state.candidate_bid_ask_pct > config.maximum_bid_ask_pct:
        blockers.append("candidate_spread_too_wide")
    if config.require_observed_quotes and not state.observed_bid_ask:
        blockers.append("bid_ask_not_observed")

    if state.iv_rv_ratio >= 1.50:
        warnings.append("extreme_iv_rv_ratio_may_reflect_event_or_data_issue")
    if state.vix_percentile >= 0.90:
        warnings.append("high_tail_risk_even_if_entry_gate_passes")
    if state.candidate_short_put_abs_delta > 0.25:
        warnings.append("higher_directional_and_assignment_exposure")

    return StrategyDecision(
        eligible=not blockers,
        strategy_name="conditional_index_put_write",
        benchmark_structure="monthly_fully_cash_secured_put",
        safety_adaptation="defined_risk_put_vertical_same_signal",
        iv_rv_ratio=round(state.iv_rv_ratio, 6),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        required_backtests=(
            "unconditional_cash_secured_put_benchmark",
            "conditional_cash_secured_put",
            "conditional_defined_risk_put_vertical",
            "signal_ablation_iv_rv_only",
            "signal_ablation_vix_only",
            "cost_and_slippage_stress",
            "2008_2020_2022_style_tail_regimes",
            "anchored_walk_forward_with_embargo",
        ),
    )


@dataclass(frozen=True)
class LiteratureCandidate:
    rank: int
    name: str
    evidence_grade: str
    implementation_grade: str
    principal_edge: str
    primary_failure_mode: str
    project_verdict: str


def ranked_literature_candidates() -> tuple[LiteratureCandidate, ...]:
    """Return the current literature ranking used by the research roadmap."""
    return (
        LiteratureCandidate(
            rank=1,
            name="conditional_index_put_write_iv_over_rv",
            evidence_grade="A-",
            implementation_grade="A",
            principal_edge="index variance and crash-insurance premium",
            primary_failure_mode="left-tail loss and regime-dependent premium compression",
            project_verdict="PRIMARY_STRICT_BACKTEST_CANDIDATE",
        ),
        LiteratureCandidate(
            rank=2,
            name="cross_sectional_option_momentum",
            evidence_grade="A",
            implementation_grade="C+",
            principal_edge="persistent continuation in option portfolio returns",
            primary_failure_mode="large universe, historical surface, and turnover burden",
            project_verdict="SECONDARY_RESEARCH_ARM",
        ),
        LiteratureCandidate(
            rank=3,
            name="cross_sectional_implied_realized_volatility",
            evidence_grade="A-",
            implementation_grade="C",
            principal_edge="relative option expensiveness across stocks",
            primary_failure_mode="single-name gap risk, survivorship, and microstructure bias",
            project_verdict="INSTITUTIONAL_DATA_REQUIRED",
        ),
        LiteratureCandidate(
            rank=4,
            name="weekly_index_put_write",
            evidence_grade="B+",
            implementation_grade="A-",
            principal_edge="frequent collection of index volatility premium",
            primary_failure_mode="high gamma concentration and weekly tail exposure",
            project_verdict="BENCHMARK_ONLY_UNTIL_INTRADAY_DATA",
        ),
        LiteratureCandidate(
            rank=5,
            name="index_iron_condor",
            evidence_grade="B",
            implementation_grade="B",
            principal_edge="defined-risk sale of both variance tails",
            primary_failure_mode="call-side premium is weaker and costs consume credit",
            project_verdict="COMPARATOR_NOT_DEFAULT_WINNER",
        ),
        LiteratureCandidate(
            rank=6,
            name="covered_call",
            evidence_grade="B+",
            implementation_grade="A",
            principal_edge="call premium and volatility reduction",
            primary_failure_mode="systematic sacrifice of equity upside",
            project_verdict="INCOME_BENCHMARK_NOT_ALPHA_WINNER",
        ),
        LiteratureCandidate(
            rank=7,
            name="dispersion_correlation_risk",
            evidence_grade="A-",
            implementation_grade="D",
            principal_edge="index-versus-component correlation premium",
            primary_failure_mode="realistic frictions erase theoretical alpha",
            project_verdict="REJECT_FOR_CURRENT_PROJECT",
        ),
        LiteratureCandidate(
            rank=8,
            name="unconditional_long_options",
            evidence_grade="A-",
            implementation_grade="A",
            principal_edge="convex protection during rare shocks",
            primary_failure_mode="persistent premium drag",
            project_verdict="HEDGE_ONLY_NOT_RETURN_ENGINE",
        ),
    )


def candidate_names(candidates: Iterable[LiteratureCandidate] | None = None) -> tuple[str, ...]:
    values = tuple(candidates) if candidates is not None else ranked_literature_candidates()
    return tuple(candidate.name for candidate in values)
