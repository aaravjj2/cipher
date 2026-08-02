"""Leveraged-ETF cash-secured-put and wheel research engine.

This module implements the mechanical thesis supplied by the user while
preserving the project's read-only execution boundary.  It contains no broker
client and no order-submission code.

Key controls
------------
* Curated fundamental-quality whitelist for leveraged ETFs and their parent or
  reference basket.  This is explicitly not a point-in-time fundamentals
  database and is reported as a survivorship-bias limitation.
* Prior-completed-week Ripster EMA cloud filter using 5/12, 34/50, and 72/89
  weekly EMAs.  At least two clouds must be bullish.
* Weekly Wilder RSI position sizing with a normal 10%-30% allocation range.
  Optional 60%-80% stress profiles are disabled by default.
* Close-to-close down-day entry filter, defaulting to -5%.
* Contract modes for standard, conservative, set-and-forget, and
  assignment-seeking cash-secured puts.
* Entry IV filter, default 40%-70%.  When historical IV is absent, the SQLite
  adapter estimates IV from the last observed pre-entry option trade price.
* Conservative trade-bar execution approximation: short entries use an
  observed low minus slippage; buy-to-close exits use an observed high plus
  slippage.
* Fifty-percent premium capture, defensive rolling, physical assignment,
  averaging down subject to symbol-allocation limits, and covered calls.
* Portfolio concentration and collateral limits.

Historical Alpaca option archives contain trades/minute trade bars, not NBBO.
Results produced from those archives must therefore remain labeled as
exploratory rather than research-grade executable evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")
UTC = timezone.utc
CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
DEFAULT_EQUITY_DB = (
    ROOT / "data" / "historical_equities" / "leveraged_etf_wheel" / "equity_bars.sqlite"
)
DEFAULT_ARCHIVE_ROOT = ROOT / "data" / "historical_options" / "leveraged_etf_wheel"
DEFAULT_OUTPUT = ROOT / "data" / "leveraged_etf_wheel"


class WheelBacktestError(RuntimeError):
    """Raised when a hard strategy or data invariant is violated."""


def _finite(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _safe_mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _safe_median(values: Sequence[float]) -> float | None:
    return median(values) if values else None


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _market_datetime(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=NY)


def _parse_timestamp(raw: str) -> datetime:
    value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(NY)


def _round_money(value: float) -> float:
    return round(float(value) + 1e-12, 2)


@dataclass(frozen=True, slots=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close", "volume"):
            value = _finite(getattr(self, name), name=name)
            object.__setattr__(self, name, value)
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC values must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("daily high is inconsistent")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("daily low is inconsistent")

    @property
    def green(self) -> bool:
        return self.close > self.open


@dataclass(frozen=True, slots=True)
class UniverseAsset:
    symbol: str
    reference: str
    quality_kind: str
    quality_approved: bool
    quality_as_of: str
    leverage_multiple: float
    parent_market_cap_billion: float | None = None
    revenue_growth_positive: bool | None = None
    gross_margin_pct: float | None = None
    gross_margin_exempt: bool = False
    all_time_high_history: bool | None = None
    dividend_yield: float = 0.0
    quality_override_reason: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip().upper()
        reference = str(self.reference).strip().upper()
        kind = str(self.quality_kind).strip().lower()
        if not symbol or not reference:
            raise ValueError("asset symbol and reference are required")
        if kind not in {"single_company", "index_basket"}:
            raise ValueError("quality_kind must be single_company or index_basket")
        leverage = _finite(self.leverage_multiple, name="leverage_multiple")
        dividend_yield = _finite(self.dividend_yield, name="dividend_yield")
        if leverage <= 1:
            raise ValueError("leveraged ETF multiple must exceed 1")
        if dividend_yield < 0:
            raise ValueError("dividend_yield cannot be negative")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "quality_kind", kind)
        object.__setattr__(self, "leverage_multiple", leverage)
        object.__setattr__(self, "dividend_yield", dividend_yield)

    def quality_check(self) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if not self.quality_approved:
            reasons.append("curated_quality_not_approved")
        if self.quality_approved and self.quality_override_reason:
            return True, ("explicit_quality_override",)
        if self.quality_kind == "single_company":
            if self.parent_market_cap_billion is None:
                reasons.append("missing_parent_market_cap")
            elif self.parent_market_cap_billion < 200:
                reasons.append("parent_market_cap_below_200b")
            if self.revenue_growth_positive is not True:
                reasons.append("revenue_growth_requirement_failed")
            if not self.gross_margin_exempt:
                if self.gross_margin_pct is None:
                    reasons.append("missing_gross_margin")
                elif self.gross_margin_pct < 20:
                    reasons.append("gross_margin_below_20pct")
            if self.all_time_high_history is not True:
                reasons.append("all_time_high_history_requirement_failed")
        return not reasons, tuple(reasons)


@dataclass(frozen=True, slots=True)
class PutMode:
    name: str
    min_dte: int
    max_dte: int
    target_dte: int
    target_collateral_return: float
    minimum_collateral_return: float
    maximum_collateral_return: float | None
    target_pop: float | None
    selection_style: str
    seeking_assignment: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.min_dte <= self.target_dte <= self.max_dte:
            raise ValueError(f"invalid DTE window for {self.name}")
        for field_name in (
            "target_collateral_return",
            "minimum_collateral_return",
        ):
            value = _finite(getattr(self, field_name), name=field_name)
            if value < 0:
                raise ValueError(f"{field_name} cannot be negative")
            object.__setattr__(self, field_name, value)
        if self.maximum_collateral_return is not None:
            maximum = _finite(
                self.maximum_collateral_return,
                name="maximum_collateral_return",
            )
            if maximum < self.minimum_collateral_return:
                raise ValueError("maximum collateral return is below minimum")
            object.__setattr__(self, "maximum_collateral_return", maximum)
        if self.target_pop is not None and not 0 < self.target_pop < 1:
            raise ValueError("target_pop must be between 0 and 1")
        if self.selection_style not in {"target_return", "near_atm", "atm_or_below"}:
            raise ValueError("invalid mode selection style")


DEFAULT_MODES: dict[str, PutMode] = {
    "standard": PutMode(
        name="standard",
        min_dte=25,
        max_dte=35,
        target_dte=30,
        target_collateral_return=0.05,
        minimum_collateral_return=0.025,
        maximum_collateral_return=0.10,
        target_pop=0.75,
        selection_style="target_return",
    ),
    "conservative": PutMode(
        name="conservative",
        min_dte=7,
        max_dte=15,
        target_dte=10,
        target_collateral_return=0.01,
        minimum_collateral_return=0.005,
        maximum_collateral_return=0.04,
        target_pop=0.85,
        selection_style="target_return",
    ),
    "set_and_forget": PutMode(
        name="set_and_forget",
        min_dte=60,
        max_dte=90,
        target_dte=75,
        target_collateral_return=0.15,
        minimum_collateral_return=0.10,
        maximum_collateral_return=0.30,
        target_pop=None,
        selection_style="near_atm",
    ),
    "advanced_assignment": PutMode(
        name="advanced_assignment",
        min_dte=7,
        max_dte=14,
        target_dte=10,
        target_collateral_return=0.08,
        minimum_collateral_return=0.04,
        maximum_collateral_return=0.20,
        target_pop=None,
        selection_style="atm_or_below",
        seeking_assignment=True,
    ),
}


@dataclass(frozen=True, slots=True)
class WheelConfig:
    mode: PutMode = DEFAULT_MODES["standard"]
    down_day_threshold: float = -0.05
    minimum_iv: float = 0.40
    maximum_iv: float = 0.70
    weekly_rsi_period: int = 14
    required_bullish_clouds: int = 2
    weekly_clouds: tuple[tuple[int, int], ...] = ((5, 12), (34, 50), (72, 89))
    max_open_option_positions: int = 5
    min_trade_allocation: float = 0.10
    max_trade_allocation: float = 0.30
    max_symbol_allocation: float = 0.30
    enable_aggressive_scaling: bool = False
    aggressive_low_rsi_allocation: float = 0.60
    aggressive_extreme_rsi_allocation: float = 0.80
    aggressive_low_rsi_threshold: float = 25.0
    aggressive_extreme_rsi_threshold: float = 20.0
    profit_take_fraction: float = 0.50
    roll_trigger_dte: int = 5
    roll_min_extension_days: int = 30
    roll_max_extension_days: int = 90
    require_roll_net_credit: bool = True
    assignment_unwanted: bool = True
    average_down_enabled: bool = True
    covered_calls_enabled: bool = True
    covered_call_min_dte: int = 7
    covered_call_max_dte: int = 15
    covered_call_target_dte: int = 10
    covered_call_trigger_appreciation: float = 0.10
    covered_call_style: str = "atm"
    covered_call_otm_pct: float = 0.10
    entry_time_et: time = time(15, 45)
    risk_free_rate: float = 0.04
    entry_slippage_fraction: float = 0.05
    entry_slippage_floor: float = 0.05
    exit_slippage_fraction: float = 0.05
    exit_slippage_floor: float = 0.05
    fee_per_contract_side: float = 0.75
    collateral_net_of_premium: bool = False
    minimum_pre_entry_volume: float = 1.0
    allow_missing_iv: bool = False
    enforce_target_pop: bool = True
    target_pop_tolerance: float = 0.05

    def __post_init__(self) -> None:
        if not -1 < self.down_day_threshold < 0:
            raise ValueError("down_day_threshold must be a negative decimal return")
        if not 0 <= self.minimum_iv <= self.maximum_iv:
            raise ValueError("invalid IV range")
        if not 0 <= self.target_pop_tolerance < 1:
            raise ValueError("target_pop_tolerance must be in [0,1)")
        if self.weekly_rsi_period < 2:
            raise ValueError("weekly_rsi_period must be at least 2")
        if not 1 <= self.required_bullish_clouds <= len(self.weekly_clouds):
            raise ValueError("invalid required_bullish_clouds")
        if not 1 <= self.max_open_option_positions <= 5:
            raise ValueError("max_open_option_positions must be in 1..5")
        for field_name in (
            "min_trade_allocation",
            "max_trade_allocation",
            "max_symbol_allocation",
            "profit_take_fraction",
        ):
            value = _finite(getattr(self, field_name), name=field_name)
            if not 0 < value <= 1:
                raise ValueError(f"{field_name} must be in (0,1]")
            object.__setattr__(self, field_name, value)
        if self.min_trade_allocation > self.max_trade_allocation:
            raise ValueError("minimum allocation exceeds maximum allocation")
        if self.max_trade_allocation > self.max_symbol_allocation:
            raise ValueError("trade allocation exceeds symbol cap")
        if self.covered_call_style not in {"atm", "otm"}:
            raise ValueError("covered_call_style must be atm or otm")
        if not 1 <= self.covered_call_min_dte <= self.covered_call_target_dte <= self.covered_call_max_dte:
            raise ValueError("invalid covered-call DTE window")
        if self.roll_min_extension_days <= 0 or self.roll_max_extension_days < self.roll_min_extension_days:
            raise ValueError("invalid roll extension range")


@dataclass(frozen=True, slots=True)
class WeeklyTrendState:
    as_of: date
    weekly_close_date: date | None
    weekly_close: float | None
    cloud_states: tuple[bool | None, ...]
    bullish_clouds: int
    weekly_rsi: float | None
    history_weeks: int

    @property
    def passes(self) -> bool:
        return self.bullish_clouds >= 2


@dataclass(frozen=True, slots=True)
class OptionCandidate:
    contract_symbol: str
    underlying: str
    option_type: str
    expiration: date
    strike: float
    pre_entry_price: float
    entry_price_proxy: float
    pre_entry_volume: float
    source_archive: str
    implied_volatility: float | None = None
    delta: float | None = None
    open_interest: float | None = None
    entry_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        option_type = str(self.option_type).strip().lower()
        if option_type not in {"put", "call"}:
            raise ValueError("option_type must be put or call")
        for field_name in (
            "strike",
            "pre_entry_price",
            "entry_price_proxy",
            "pre_entry_volume",
        ):
            value = _finite(getattr(self, field_name), name=field_name)
            if value < 0:
                raise ValueError(f"{field_name} cannot be negative")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "option_type", option_type)
        object.__setattr__(self, "underlying", str(self.underlying).upper())

    def dte(self, decision_day: date) -> int:
        return (self.expiration - decision_day).days

    def moneyness(self, spot: float) -> float:
        return self.strike / spot


@dataclass(frozen=True, slots=True)
class BuybackOpportunity:
    timestamp: datetime
    price_proxy: float
    raw_high: float
    source_archive: str


@dataclass(frozen=True, slots=True)
class DataRequest:
    symbol: str
    decision_day: date
    option_type: str
    min_dte: int
    max_dte: int
    target_dte: int
    reason: str
    minimum_moneyness: float
    maximum_moneyness: float
    target_moneyness: float


class WheelMarketData(Protocol):
    def trading_days(self, start: date, end: date) -> Sequence[date]: ...
    def daily_bar(self, symbol: str, day: date) -> DailyBar | None: ...
    def daily_history(self, symbol: str, end: date) -> Sequence[DailyBar]: ...
    def option_chain(self, symbol: str, day: date, option_type: str) -> Sequence[OptionCandidate]: ...
    def buyback_opportunity(
        self,
        contract_symbol: str,
        day: date,
        target_price: float,
        *,
        exit_slippage_fraction: float,
        exit_slippage_floor: float,
    ) -> BuybackOpportunity | None: ...
    def close_buyback_price(
        self,
        contract_symbol: str,
        day: date,
        *,
        exit_slippage_fraction: float,
        exit_slippage_floor: float,
    ) -> float | None: ...
    def option_liability_mark(self, contract_symbol: str, day: date) -> float | None: ...


@dataclass(slots=True)
class StockPosition:
    symbol: str
    shares: int = 0
    gross_cost: float = 0.0
    premium_offsets: float = 0.0
    covered_shares: int = 0

    @property
    def effective_basis_total(self) -> float:
        return self.gross_cost - self.premium_offsets

    @property
    def average_effective_basis(self) -> float | None:
        return self.effective_basis_total / self.shares if self.shares else None

    def add_assignment(self, shares: int, strike_cost: float, premium_offset: float) -> None:
        if shares <= 0:
            raise ValueError("assigned shares must be positive")
        self.shares += shares
        self.gross_cost += strike_cost
        self.premium_offsets += premium_offset

    def remove_called_shares(self, shares: int) -> float:
        if shares <= 0 or shares > self.shares:
            raise ValueError("invalid called share quantity")
        fraction = shares / self.shares
        basis_removed = self.effective_basis_total * fraction
        self.gross_cost *= 1.0 - fraction
        self.premium_offsets *= 1.0 - fraction
        self.shares -= shares
        self.covered_shares = max(0, self.covered_shares - shares)
        return basis_removed


@dataclass(slots=True)
class ShortOptionPosition:
    position_id: str
    strategy: str
    underlying: str
    option_type: str
    contract_symbol: str
    opened_on: date
    expiration: date
    strike: float
    contracts: int
    entry_credit_per_share: float
    entry_net_credit_total: float
    entry_fees: float
    collateral: float
    profit_target_price: float
    entry_iv: float | None
    entry_pop: float | None
    entry_weekly_rsi: float | None
    entry_bullish_clouds: int
    mode: str
    source_archive: str
    rolled_from: str | None = None
    roll_count: int = 0

    @property
    def shares_equivalent(self) -> int:
        return self.contracts * 100


@dataclass(frozen=True, slots=True)
class WheelEvent:
    day: date
    timestamp: str
    event: str
    symbol: str
    position_id: str | None
    cash_flow: float
    realized_pnl: float | None
    details: str


@dataclass(frozen=True, slots=True)
class DailyEquity:
    day: date
    cash: float
    reserved_collateral: float
    stock_value: float
    option_liability: float
    equity: float
    free_cash: float
    open_options: int


@dataclass(slots=True)
class PortfolioState:
    initial_cash: float
    cash: float
    reserved_collateral: float = 0.0
    stocks: dict[str, StockPosition] = field(default_factory=dict)
    short_options: dict[str, ShortOptionPosition] = field(default_factory=dict)
    events: list[WheelEvent] = field(default_factory=list)
    daily_equity: list[DailyEquity] = field(default_factory=list)
    skips: list[dict[str, Any]] = field(default_factory=list)
    data_requests: list[DataRequest] = field(default_factory=list)
    sequence: int = 0

    @classmethod
    def create(cls, initial_cash: float) -> "PortfolioState":
        amount = _finite(initial_cash, name="initial_cash")
        if amount <= 0:
            raise ValueError("initial_cash must be positive")
        return cls(initial_cash=amount, cash=amount)

    @property
    def free_cash(self) -> float:
        return self.cash - self.reserved_collateral

    def next_position_id(self, day: date, symbol: str, option_type: str) -> str:
        self.sequence += 1
        return f"WHEEL-{day:%Y%m%d}-{symbol}-{option_type[0].upper()}-{self.sequence:05d}"


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate: OptionCandidate
    iv: float | None
    pop: float | None
    gross_credit: float
    collateral_return: float
    score: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    config: dict[str, Any]
    summary: dict[str, Any]
    events: tuple[WheelEvent, ...]
    daily_equity: tuple[DailyEquity, ...]
    skips: tuple[dict[str, Any], ...]
    data_requests: tuple[DataRequest, ...]
    open_options: tuple[dict[str, Any], ...]
    stock_positions: tuple[dict[str, Any], ...]


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def ema_series(values: Sequence[float], length: int) -> list[float | None]:
    if length <= 0:
        raise ValueError("EMA length must be positive")
    output: list[float | None] = [None] * len(values)
    if len(values) < length:
        return output
    seed = mean(float(value) for value in values[:length])
    output[length - 1] = seed
    alpha = 2.0 / (length + 1.0)
    previous = seed
    for index in range(length, len(values)):
        previous = float(values[index]) * alpha + previous * (1.0 - alpha)
        output[index] = previous
    return output


def wilder_rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    if period < 2:
        raise ValueError("RSI period must be at least 2")
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return output
    changes = [float(values[index]) - float(values[index - 1]) for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return 100.0 - 100.0 / (1.0 + rs)

    output[period] = value(avg_gain, avg_loss)
    for price_index in range(period + 1, len(values)):
        change_index = price_index - 1
        avg_gain = (avg_gain * (period - 1) + gains[change_index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[change_index]) / period
        output[price_index] = value(avg_gain, avg_loss)
    return output


def completed_weekly_closes(
    daily_bars: Sequence[DailyBar],
    as_of: date,
) -> list[tuple[date, float]]:
    """Return closes for weeks completed before ``as_of``.

    Grouping uses ISO week and takes the last available trading day.  The week
    containing ``as_of`` is excluded, even when ``as_of`` is Friday, so the
    backtest never uses an incompletely known weekly close at entry time.
    """
    grouped: dict[tuple[int, int], DailyBar] = {}
    as_of_iso = as_of.isocalendar()[:2]
    for bar in sorted(daily_bars, key=lambda row: row.day):
        if bar.day >= as_of:
            break
        key = bar.day.isocalendar()[:2]
        if key == as_of_iso:
            continue
        grouped[key] = bar
    return [(bar.day, bar.close) for _key, bar in sorted(grouped.items())]


def weekly_trend_state(
    daily_bars: Sequence[DailyBar],
    as_of: date,
    config: WheelConfig,
) -> WeeklyTrendState:
    weekly = completed_weekly_closes(daily_bars, as_of)
    values = [close for _day, close in weekly]
    cloud_states: list[bool | None] = []
    for fast, slow in config.weekly_clouds:
        fast_series = ema_series(values, fast)
        slow_series = ema_series(values, slow)
        fast_value = fast_series[-1] if fast_series else None
        slow_value = slow_series[-1] if slow_series else None
        cloud_states.append(
            None if fast_value is None or slow_value is None else fast_value >= slow_value
        )
    rsi_values = wilder_rsi(values, config.weekly_rsi_period)
    rsi = rsi_values[-1] if rsi_values else None
    return WeeklyTrendState(
        as_of=as_of,
        weekly_close_date=weekly[-1][0] if weekly else None,
        weekly_close=weekly[-1][1] if weekly else None,
        cloud_states=tuple(cloud_states),
        bullish_clouds=sum(state is True for state in cloud_states),
        weekly_rsi=rsi,
        history_weeks=len(weekly),
    )


def allocation_from_weekly_rsi(rsi: float | None, config: WheelConfig) -> float:
    if rsi is None:
        return config.min_trade_allocation
    if config.enable_aggressive_scaling:
        if rsi < config.aggressive_extreme_rsi_threshold:
            return config.aggressive_extreme_rsi_allocation
        if rsi < config.aggressive_low_rsi_threshold:
            return config.aggressive_low_rsi_allocation
    if rsi < 30:
        fraction = 0.30
    elif rsi < 35:
        fraction = 0.25
    elif rsi < 40:
        fraction = 0.20
    elif rsi < 50:
        fraction = 0.15
    elif rsi < 60:
        fraction = 0.125
    else:
        fraction = 0.10
    return min(max(fraction, config.min_trade_allocation), config.max_trade_allocation)


# ---------------------------------------------------------------------------
# Option calculations and selection
# ---------------------------------------------------------------------------


def black_scholes_price(
    *,
    option_type: str,
    spot: float,
    strike: float,
    time_years: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
) -> float:
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if time_years <= 0:
        return max(strike - spot, 0.0) if option_type == "put" else max(spot - strike, 0.0)
    if volatility <= 0:
        forward = spot * math.exp((rate - dividend_yield) * time_years)
        discounted = math.exp(-rate * time_years)
        if option_type == "put":
            return discounted * max(strike - forward, 0.0)
        return discounted * max(forward - strike, 0.0)
    root_t = math.sqrt(time_years)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend_yield + 0.5 * volatility * volatility) * time_years
    ) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    if option_type == "call":
        return (
            spot * math.exp(-dividend_yield * time_years) * _norm_cdf(d1)
            - strike * math.exp(-rate * time_years) * _norm_cdf(d2)
        )
    return (
        strike * math.exp(-rate * time_years) * _norm_cdf(-d2)
        - spot * math.exp(-dividend_yield * time_years) * _norm_cdf(-d1)
    )


def implied_volatility(
    *,
    option_type: str,
    price: float,
    spot: float,
    strike: float,
    time_years: float,
    rate: float,
    dividend_yield: float,
) -> float | None:
    if price <= 0 or spot <= 0 or strike <= 0 or time_years <= 0:
        return None
    intrinsic = max(strike - spot, 0.0) if option_type == "put" else max(spot - strike, 0.0)
    if price + 1e-9 < intrinsic:
        return None
    low, high = 0.001, 5.0
    high_price = black_scholes_price(
        option_type=option_type,
        spot=spot,
        strike=strike,
        time_years=time_years,
        rate=rate,
        dividend_yield=dividend_yield,
        volatility=high,
    )
    if high_price < price:
        return None
    for _ in range(100):
        mid = (low + high) / 2.0
        model = black_scholes_price(
            option_type=option_type,
            spot=spot,
            strike=strike,
            time_years=time_years,
            rate=rate,
            dividend_yield=dividend_yield,
            volatility=mid,
        )
        if model < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def risk_neutral_pop(
    *,
    option_type: str,
    spot: float,
    strike: float,
    time_years: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
) -> float | None:
    if min(spot, strike, time_years, volatility) <= 0:
        return None
    root_t = math.sqrt(time_years)
    d2 = (
        math.log(spot / strike)
        + (rate - dividend_yield - 0.5 * volatility * volatility) * time_years
    ) / (volatility * root_t)
    if option_type == "put":
        return _norm_cdf(d2)  # Probability S_T > K under the pricing measure.
    return _norm_cdf(-d2)  # Covered call expires OTM when S_T <= K.


def short_entry_credit(candidate: OptionCandidate, config: WheelConfig) -> float | None:
    observed = candidate.entry_price_proxy
    if observed <= 0:
        return None
    haircut = max(config.entry_slippage_floor, observed * config.entry_slippage_fraction)
    credit = observed - haircut
    return credit if credit > 0 else None


def collateral_per_contract(strike: float, credit: float, config: WheelConfig) -> float:
    gross = strike * 100.0
    if config.collateral_net_of_premium:
        return max(gross - credit * 100.0, 0.0)
    return gross


def _candidate_iv(
    candidate: OptionCandidate,
    *,
    day: date,
    spot: float,
    asset: UniverseAsset,
    config: WheelConfig,
) -> float | None:
    if candidate.implied_volatility is not None:
        return candidate.implied_volatility
    return implied_volatility(
        option_type=candidate.option_type,
        price=candidate.pre_entry_price,
        spot=spot,
        strike=candidate.strike,
        time_years=max(candidate.dte(day), 1) / 365.0,
        rate=config.risk_free_rate,
        dividend_yield=asset.dividend_yield,
    )


def evaluate_put_candidates(
    candidates: Sequence[OptionCandidate],
    *,
    day: date,
    spot: float,
    asset: UniverseAsset,
    mode: PutMode,
    config: WheelConfig,
) -> list[CandidateEvaluation]:
    unique_strikes = sorted({candidate.strike for candidate in candidates})
    below_or_at = [strike for strike in unique_strikes if strike <= spot]
    atm_below = set(below_or_at[-3:])
    rows: list[CandidateEvaluation] = []
    for candidate in candidates:
        if candidate.option_type != "put":
            continue
        dte = candidate.dte(day)
        if not mode.min_dte <= dte <= mode.max_dte:
            continue
        if candidate.pre_entry_volume < config.minimum_pre_entry_volume:
            continue
        credit = short_entry_credit(candidate, config)
        if credit is None:
            continue
        iv = _candidate_iv(candidate, day=day, spot=spot, asset=asset, config=config)
        if iv is None and not config.allow_missing_iv:
            continue
        if iv is not None and not config.minimum_iv <= iv <= config.maximum_iv:
            continue
        collateral = collateral_per_contract(candidate.strike, credit, config)
        if collateral <= 0:
            continue
        roc = credit * 100.0 / collateral
        if roc < mode.minimum_collateral_return:
            continue
        if mode.maximum_collateral_return is not None and roc > mode.maximum_collateral_return:
            continue
        pop = None
        if iv is not None:
            pop = risk_neutral_pop(
                option_type="put",
                spot=spot,
                strike=candidate.strike,
                time_years=max(dte, 1) / 365.0,
                rate=config.risk_free_rate,
                dividend_yield=asset.dividend_yield,
                volatility=iv,
            )
        if config.enforce_target_pop and mode.target_pop is not None:
            minimum_pop = max(mode.target_pop - config.target_pop_tolerance, 0.0)
            if pop is None or pop < minimum_pop:
                continue
        dte_penalty = abs(dte - mode.target_dte) / max(mode.max_dte - mode.min_dte, 1)
        return_penalty = abs(roc - mode.target_collateral_return) / max(mode.target_collateral_return, 0.01)
        pop_penalty = (
            abs((pop or 0.0) - mode.target_pop) / max(mode.target_pop, 0.01)
            if mode.target_pop is not None and pop is not None
            else 0.0
        )
        moneyness = candidate.moneyness(spot)
        if mode.selection_style == "near_atm":
            style_penalty = abs(moneyness - 1.0) * 20.0
        elif mode.selection_style == "atm_or_below":
            if candidate.strike not in atm_below:
                continue
            style_penalty = abs(moneyness - 1.0) * 10.0
        else:
            style_penalty = max(moneyness - 1.0, 0.0) * 20.0
        liquidity_bonus = min(math.log1p(candidate.pre_entry_volume) / 10.0, 1.0)
        score = 100.0 - 25.0 * dte_penalty - 35.0 * return_penalty - 20.0 * pop_penalty - 10.0 * style_penalty + 5.0 * liquidity_bonus
        rows.append(
            CandidateEvaluation(
                candidate=candidate,
                iv=iv,
                pop=pop,
                gross_credit=credit,
                collateral_return=roc,
                score=score,
            )
        )
    return sorted(rows, key=lambda row: (row.score, row.candidate.pre_entry_volume), reverse=True)


def evaluate_call_candidates(
    candidates: Sequence[OptionCandidate],
    *,
    day: date,
    spot: float,
    config: WheelConfig,
) -> list[CandidateEvaluation]:
    rows: list[CandidateEvaluation] = []
    target_strike = spot if config.covered_call_style == "atm" else spot * (1.0 + config.covered_call_otm_pct)
    for candidate in candidates:
        if candidate.option_type != "call":
            continue
        dte = candidate.dte(day)
        if not config.covered_call_min_dte <= dte <= config.covered_call_max_dte:
            continue
        if candidate.pre_entry_volume < config.minimum_pre_entry_volume:
            continue
        credit = short_entry_credit(candidate, config)
        if credit is None:
            continue
        distance = abs(candidate.strike - target_strike) / spot
        score = 100.0 - distance * 300.0 - abs(dte - config.covered_call_target_dte) * 2.0
        rows.append(
            CandidateEvaluation(
                candidate=candidate,
                iv=candidate.implied_volatility,
                pop=None,
                gross_credit=credit,
                collateral_return=credit / max(spot, 1e-9),
                score=score,
            )
        )
    return sorted(rows, key=lambda row: (row.score, row.candidate.pre_entry_volume), reverse=True)


def find_entry_signal_days(
    data: WheelMarketData,
    asset: UniverseAsset,
    start: date,
    end: date,
    config: WheelConfig,
) -> list[dict[str, Any]]:
    """Return point-in-time-safe down-day signals before option-chain filtering."""
    quality, quality_reasons = asset.quality_check()
    if not quality:
        return [
            {
                "symbol": asset.symbol,
                "eligible": False,
                "reason": ";".join(quality_reasons),
            }
        ]
    output: list[dict[str, Any]] = []
    history = list(data.daily_history(asset.symbol, end))
    by_day = {bar.day: bar for bar in history}
    ordered_days = sorted(by_day)
    previous_by_day: dict[date, DailyBar | None] = {}
    previous: DailyBar | None = None
    for day in ordered_days:
        previous_by_day[day] = previous
        previous = by_day[day]
    for day in ordered_days:
        if not start <= day <= end:
            continue
        bar = by_day[day]
        prior = previous_by_day[day]
        if prior is None:
            continue
        daily_return = bar.close / prior.close - 1.0
        if daily_return > config.down_day_threshold:
            continue
        weekly = weekly_trend_state(history, day, config)
        if weekly.bullish_clouds < config.required_bullish_clouds or weekly.weekly_rsi is None:
            continue
        output.append(
            {
                "symbol": asset.symbol,
                "reference": asset.reference,
                "day": day.isoformat(),
                "close": bar.close,
                "prior_close": prior.close,
                "daily_return_pct": daily_return * 100.0,
                "bullish_clouds": weekly.bullish_clouds,
                "cloud_states": list(weekly.cloud_states),
                "weekly_rsi": weekly.weekly_rsi,
                "allocation_fraction": allocation_from_weekly_rsi(weekly.weekly_rsi, config),
                "weekly_close_date": (
                    weekly.weekly_close_date.isoformat()
                    if weekly.weekly_close_date else None
                ),
                "eligible": True,
                "quality_override": bool(asset.quality_override_reason),
            }
        )
    return output


# ---------------------------------------------------------------------------
# SQLite market-data adapter
# ---------------------------------------------------------------------------


class SQLiteWheelMarketData:
    """Read daily equity bars and selected option contracts from local archives."""

    def __init__(
        self,
        equity_db: str | Path,
        archive_paths: Sequence[str | Path] = (),
        *,
        entry_time_et: time = time(15, 45),
    ) -> None:
        self.equity_db = Path(equity_db)
        if not self.equity_db.exists():
            raise FileNotFoundError(self.equity_db)
        self.entry_time_et = entry_time_et
        self._daily: dict[str, list[DailyBar]] = self._load_daily_bars()
        self._daily_map = {
            symbol: {bar.day: bar for bar in rows}
            for symbol, rows in self._daily.items()
        }
        self.archives: list[tuple[Path, sqlite3.Connection]] = []
        self.contract_sources: dict[str, list[sqlite3.Connection]] = defaultdict(list)
        self.contract_archive_names: dict[tuple[int, str], str] = {}
        for raw_path in archive_paths:
            path = Path(raw_path)
            db_path = path / "historical_options.sqlite" if path.is_dir() else path
            if not db_path.exists():
                continue
            connection = sqlite3.connect(db_path)
            connection.execute("pragma query_only=on")
            self.archives.append((db_path, connection))
            try:
                symbols = connection.execute("select symbol from contracts").fetchall()
            except sqlite3.Error:
                symbols = []
            for (symbol,) in symbols:
                self.contract_sources[str(symbol)].append(connection)
                self.contract_archive_names[(id(connection), str(symbol))] = str(db_path.parent)

    def close(self) -> None:
        for _path, connection in self.archives:
            connection.close()

    def _load_daily_bars(self) -> dict[str, list[DailyBar]]:
        with sqlite3.connect(self.equity_db) as db:
            tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
            if "bars" not in tables:
                raise WheelBacktestError(f"bars table is missing from {self.equity_db}")
            rows = db.execute(
                """select symbol,timestamp,open,high,low,close,volume
                   from bars where timeframe='1Day' order by symbol,timestamp"""
            ).fetchall()
        output: dict[str, list[DailyBar]] = defaultdict(list)
        for symbol, timestamp, op, high, low, close, volume in rows:
            if None in (op, high, low, close):
                continue
            day = _parse_timestamp(str(timestamp)).date()
            output[str(symbol).upper()].append(
                DailyBar(day, float(op), float(high), float(low), float(close), float(volume or 0.0))
            )
        for symbol in list(output):
            unique = {bar.day: bar for bar in output[symbol]}
            output[symbol] = [unique[day] for day in sorted(unique)]
        return dict(output)

    def trading_days(self, start: date, end: date) -> Sequence[date]:
        union: set[date] = set()
        for rows in self._daily.values():
            union.update(bar.day for bar in rows if start <= bar.day <= end)
        return tuple(sorted(union))

    def daily_bar(self, symbol: str, day: date) -> DailyBar | None:
        return self._daily_map.get(symbol.upper(), {}).get(day)

    def daily_history(self, symbol: str, end: date) -> Sequence[DailyBar]:
        return tuple(bar for bar in self._daily.get(symbol.upper(), ()) if bar.day <= end)

    @staticmethod
    def _option_rows_for_day(
        connection: sqlite3.Connection,
        contract_symbol: str,
        day: date,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        start = datetime.combine(day, time(0, 0), tzinfo=NY).astimezone(UTC)
        end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=NY).astimezone(UTC)
        rows = connection.execute(
            """select timestamp,open,high,low,close,volume
               from option_bars where symbol=? and timestamp>=? and timestamp<?
               order by timestamp""",
            (
                contract_symbol,
                start.isoformat().replace("+00:00", "Z"),
                end.isoformat().replace("+00:00", "Z"),
            ),
        ).fetchall()
        result = []
        for timestamp, op, high, low, close, volume in rows:
            if None in (op, high, low, close):
                continue
            result.append(
                (
                    _parse_timestamp(str(timestamp)),
                    float(op),
                    float(high),
                    float(low),
                    float(close),
                    float(volume or 0.0),
                )
            )
        return result

    def option_chain(self, symbol: str, day: date, option_type: str) -> Sequence[OptionCandidate]:
        result: dict[str, OptionCandidate] = {}
        symbol = symbol.upper()
        option_type = option_type.lower()
        for db_path, connection in self.archives:
            try:
                selections = connection.execute(
                    """select s.symbol,s.expiration_date,s.strike,s.option_type,
                              c.open_interest
                       from decision_selections s
                       left join contracts c on c.symbol=s.symbol
                       where s.decision_date=? and s.option_type=? and c.underlying=?
                       order by s.rank""",
                    (day.isoformat(), option_type, symbol),
                ).fetchall()
            except sqlite3.Error:
                continue
            for contract_symbol, expiration, strike, row_type, open_interest in selections:
                rows = self._option_rows_for_day(connection, str(contract_symbol), day)
                pre = [row for row in rows if row[0].time() < self.entry_time_et]
                entry = [row for row in rows if row[0].time() >= self.entry_time_et]
                if not pre or not entry:
                    continue
                last_pre = pre[-1]
                pre_price = last_pre[4]
                entry_low = min(row[3] for row in entry)
                entry_volume = sum(row[5] for row in pre)
                candidate = OptionCandidate(
                    contract_symbol=str(contract_symbol),
                    underlying=symbol,
                    option_type=str(row_type),
                    expiration=date.fromisoformat(str(expiration)),
                    strike=float(strike),
                    pre_entry_price=pre_price,
                    entry_price_proxy=entry_low,
                    pre_entry_volume=entry_volume,
                    source_archive=str(db_path.parent),
                    open_interest=float(open_interest) if open_interest is not None else None,
                    entry_timestamp=entry[0][0],
                )
                existing = result.get(candidate.contract_symbol)
                if existing is None or candidate.pre_entry_volume > existing.pre_entry_volume:
                    result[candidate.contract_symbol] = candidate
        return tuple(result.values())

    def _connections_for_contract(self, contract_symbol: str) -> Sequence[sqlite3.Connection]:
        return tuple(self.contract_sources.get(contract_symbol, ()))

    def buyback_opportunity(
        self,
        contract_symbol: str,
        day: date,
        target_price: float,
        *,
        exit_slippage_fraction: float,
        exit_slippage_floor: float,
    ) -> BuybackOpportunity | None:
        candidates: list[BuybackOpportunity] = []
        for connection in self._connections_for_contract(contract_symbol):
            archive = self.contract_archive_names.get((id(connection), contract_symbol), "")
            for timestamp, _op, high, _low, _close, _volume in self._option_rows_for_day(
                connection, contract_symbol, day
            ):
                cost = high + max(exit_slippage_floor, high * exit_slippage_fraction)
                if cost <= target_price:
                    candidates.append(BuybackOpportunity(timestamp, cost, high, archive))
                    break
        return min(candidates, key=lambda row: row.timestamp) if candidates else None

    def close_buyback_price(
        self,
        contract_symbol: str,
        day: date,
        *,
        exit_slippage_fraction: float,
        exit_slippage_floor: float,
    ) -> float | None:
        values: list[float] = []
        for connection in self._connections_for_contract(contract_symbol):
            rows = self._option_rows_for_day(connection, contract_symbol, day)
            if not rows:
                continue
            high = rows[-1][2]
            values.append(high + max(exit_slippage_floor, high * exit_slippage_fraction))
        return max(values) if values else None

    def option_liability_mark(self, contract_symbol: str, day: date) -> float | None:
        values: list[float] = []
        for connection in self._connections_for_contract(contract_symbol):
            rows = self._option_rows_for_day(connection, contract_symbol, day)
            if rows:
                values.append(rows[-1][4])
        return max(values) if values else None


# ---------------------------------------------------------------------------
# Stateful wheel engine
# ---------------------------------------------------------------------------


class LeveragedEtfWheelBacktester:
    def __init__(
        self,
        data: WheelMarketData,
        universe: Sequence[UniverseAsset],
        config: WheelConfig,
        *,
        initial_cash: float,
    ) -> None:
        self.data = data
        self.universe = {asset.symbol: asset for asset in universe}
        self.config = config
        self.state = PortfolioState.create(initial_cash)

    def _log(
        self,
        day: date,
        event: str,
        symbol: str,
        position_id: str | None,
        cash_flow: float,
        realized_pnl: float | None,
        details: str,
        timestamp: str = "16:00",
    ) -> None:
        self.state.events.append(
            WheelEvent(
                day=day,
                timestamp=timestamp,
                event=event,
                symbol=symbol,
                position_id=position_id,
                cash_flow=_round_money(cash_flow),
                realized_pnl=None if realized_pnl is None else _round_money(realized_pnl),
                details=details,
            )
        )

    def _skip(self, day: date, symbol: str, reason: str, **details: Any) -> None:
        self.state.skips.append(
            {"day": day.isoformat(), "symbol": symbol, "reason": reason, **details}
        )

    def _request_chain(
        self,
        symbol: str,
        day: date,
        option_type: str,
        min_dte: int,
        max_dte: int,
        target_dte: int,
        reason: str,
        minimum_moneyness: float,
        maximum_moneyness: float,
        target_moneyness: float,
    ) -> None:
        request = DataRequest(
            symbol=symbol,
            decision_day=day,
            option_type=option_type,
            min_dte=min_dte,
            max_dte=max_dte,
            target_dte=target_dte,
            reason=reason,
            minimum_moneyness=minimum_moneyness,
            maximum_moneyness=maximum_moneyness,
            target_moneyness=target_moneyness,
        )
        if request not in self.state.data_requests:
            self.state.data_requests.append(request)

    def _portfolio_equity(self, day: date) -> float:
        stock_value = 0.0
        for symbol, stock in self.state.stocks.items():
            bar = self.data.daily_bar(symbol, day)
            if bar is not None:
                stock_value += stock.shares * bar.close
        liability = 0.0
        for position in self.state.short_options.values():
            mark = self.data.option_liability_mark(position.contract_symbol, day)
            if mark is None:
                bar = self.data.daily_bar(position.underlying, day)
                spot = bar.close if bar else position.strike
                if position.option_type == "put":
                    mark = max(position.strike - spot, 0.0)
                else:
                    mark = max(spot - position.strike, 0.0)
            liability += mark * 100.0 * position.contracts
        return self.state.cash + stock_value - liability

    def _symbol_allocation(self, symbol: str, day: date, equity: float) -> float:
        if equity <= 0:
            return 1.0
        amount = sum(
            position.collateral
            for position in self.state.short_options.values()
            if position.underlying == symbol and position.option_type == "put"
        )
        stock = self.state.stocks.get(symbol)
        bar = self.data.daily_bar(symbol, day)
        if stock and bar:
            amount += stock.shares * bar.close
        return amount / equity

    def _open_option_position(
        self,
        *,
        day: date,
        asset: UniverseAsset,
        evaluation: CandidateEvaluation,
        contracts: int,
        strategy: str,
        weekly_state: WeeklyTrendState,
        rolled_from: str | None = None,
        roll_count: int = 0,
    ) -> ShortOptionPosition:
        candidate = evaluation.candidate
        gross_credit_total = evaluation.gross_credit * 100.0 * contracts
        entry_fees = self.config.fee_per_contract_side * contracts
        net_credit = gross_credit_total - entry_fees
        collateral = (
            collateral_per_contract(candidate.strike, evaluation.gross_credit, self.config)
            * contracts
            if candidate.option_type == "put"
            else 0.0
        )
        if candidate.option_type == "put" and self.state.free_cash + 1e-9 < collateral:
            raise WheelBacktestError("insufficient free cash after sizing")
        position_id = self.state.next_position_id(day, asset.symbol, candidate.option_type)
        position = ShortOptionPosition(
            position_id=position_id,
            strategy=strategy,
            underlying=asset.symbol,
            option_type=candidate.option_type,
            contract_symbol=candidate.contract_symbol,
            opened_on=day,
            expiration=candidate.expiration,
            strike=candidate.strike,
            contracts=contracts,
            entry_credit_per_share=evaluation.gross_credit,
            entry_net_credit_total=net_credit,
            entry_fees=entry_fees,
            collateral=collateral,
            profit_target_price=evaluation.gross_credit * self.config.profit_take_fraction,
            entry_iv=evaluation.iv,
            entry_pop=evaluation.pop,
            entry_weekly_rsi=weekly_state.weekly_rsi,
            entry_bullish_clouds=weekly_state.bullish_clouds,
            mode=self.config.mode.name,
            source_archive=candidate.source_archive,
            rolled_from=rolled_from,
            roll_count=roll_count,
        )
        self.state.cash += net_credit
        self.state.reserved_collateral += collateral
        if candidate.option_type == "call":
            stock = self.state.stocks.get(asset.symbol)
            if stock is None or stock.shares - stock.covered_shares < contracts * 100:
                raise WheelBacktestError("covered-call shares became unavailable")
            stock.covered_shares += contracts * 100
            stock.premium_offsets += net_credit
        self.state.short_options[position_id] = position
        self._log(
            day,
            "sell_to_open_put" if candidate.option_type == "put" else "sell_to_open_call",
            asset.symbol,
            position_id,
            net_credit,
            None,
            (
                f"contract={candidate.contract_symbol};strike={candidate.strike:.2f};"
                f"contracts={contracts};credit={evaluation.gross_credit:.4f};"
                f"iv={evaluation.iv};pop={evaluation.pop};roc={evaluation.collateral_return:.4f};"
                f"clouds={weekly_state.bullish_clouds};weekly_rsi={weekly_state.weekly_rsi}"
            ),
            timestamp=candidate.entry_timestamp.strftime("%H:%M") if candidate.entry_timestamp else "15:45",
        )
        return position

    def _close_for_profit(
        self,
        day: date,
        position: ShortOptionPosition,
        opportunity: BuybackOpportunity,
    ) -> None:
        exit_cost = opportunity.price_proxy * 100.0 * position.contracts
        exit_fees = self.config.fee_per_contract_side * position.contracts
        total_cost = exit_cost + exit_fees
        self.state.cash -= total_cost
        self.state.reserved_collateral -= position.collateral
        pnl = position.entry_net_credit_total - total_cost
        if position.option_type == "call":
            stock = self.state.stocks.get(position.underlying)
            if stock:
                stock.covered_shares = max(0, stock.covered_shares - position.shares_equivalent)
        del self.state.short_options[position.position_id]
        self._log(
            day,
            "buy_to_close_50pct",
            position.underlying,
            position.position_id,
            -total_cost,
            pnl,
            f"buyback={opportunity.price_proxy:.4f};target={position.profit_target_price:.4f}",
            timestamp=opportunity.timestamp.strftime("%H:%M"),
        )

    def _expire_or_assign(self, day: date, position: ShortOptionPosition, spot: float) -> None:
        self.state.reserved_collateral -= position.collateral
        if position.option_type == "put":
            if spot < position.strike:
                shares = position.shares_equivalent
                purchase = position.strike * shares
                self.state.cash -= purchase
                stock = self.state.stocks.setdefault(position.underlying, StockPosition(position.underlying))
                stock.add_assignment(
                    shares,
                    purchase,
                    position.entry_net_credit_total,
                )
                self._log(
                    day,
                    "put_assigned",
                    position.underlying,
                    position.position_id,
                    -purchase,
                    None,
                    f"shares={shares};strike={position.strike:.2f};spot={spot:.2f}",
                )
            else:
                self._log(
                    day,
                    "put_expired_otm",
                    position.underlying,
                    position.position_id,
                    0.0,
                    position.entry_net_credit_total,
                    f"strike={position.strike:.2f};spot={spot:.2f}",
                )
        else:
            stock = self.state.stocks.get(position.underlying)
            if stock:
                stock.covered_shares = max(0, stock.covered_shares - position.shares_equivalent)
            if spot > position.strike and stock and stock.shares >= position.shares_equivalent:
                shares = position.shares_equivalent
                proceeds = position.strike * shares
                basis_removed = stock.remove_called_shares(shares)
                self.state.cash += proceeds
                pnl = proceeds - basis_removed
                self._log(
                    day,
                    "call_assigned_shares_called",
                    position.underlying,
                    position.position_id,
                    proceeds,
                    pnl,
                    f"shares={shares};strike={position.strike:.2f};spot={spot:.2f}",
                )
                if stock.shares == 0:
                    del self.state.stocks[position.underlying]
            else:
                self._log(
                    day,
                    "call_expired_otm",
                    position.underlying,
                    position.position_id,
                    0.0,
                    position.entry_net_credit_total,
                    f"strike={position.strike:.2f};spot={spot:.2f}",
                )
        del self.state.short_options[position.position_id]

    def _attempt_roll(self, day: date, position: ShortOptionPosition, spot: float) -> bool:
        asset = self.universe[position.underlying]
        chain = self.data.option_chain(position.underlying, day, "put")
        min_expiration = position.expiration + timedelta(days=self.config.roll_min_extension_days)
        max_expiration = position.expiration + timedelta(days=self.config.roll_max_extension_days)
        candidates = [
            candidate
            for candidate in chain
            if candidate.option_type == "put"
            and min_expiration <= candidate.expiration <= max_expiration
            and candidate.strike <= position.strike
        ]
        if not candidates:
            self._request_chain(
                position.underlying,
                day,
                "put",
                max((min_expiration - day).days, 1),
                max((max_expiration - day).days, 2),
                max((min_expiration - day).days, 1),
                "defensive_roll",
                0.60,
                min(position.strike / max(spot, 1e-9), 1.02),
                min(position.strike / max(spot, 1e-9), 0.95),
            )
            return False
        old_buyback = self.data.close_buyback_price(
            position.contract_symbol,
            day,
            exit_slippage_fraction=self.config.exit_slippage_fraction,
            exit_slippage_floor=self.config.exit_slippage_floor,
        )
        if old_buyback is None:
            return False
        evaluations = evaluate_put_candidates(
            candidates,
            day=day,
            spot=spot,
            asset=asset,
            mode=PutMode(
                name="roll",
                min_dte=max((min_expiration - day).days, 1),
                max_dte=max((max_expiration - day).days, 2),
                target_dte=max((min_expiration - day).days, 1),
                target_collateral_return=0.01,
                minimum_collateral_return=0.0,
                maximum_collateral_return=None,
                target_pop=None,
                selection_style="target_return",
            ),
            config=self.config,
        )
        valid = []
        old_cost_total = (
            old_buyback * 100.0 * position.contracts
            + self.config.fee_per_contract_side * position.contracts
        )
        for evaluation in evaluations:
            new_net_credit = (
                evaluation.gross_credit * 100.0 * position.contracts
                - self.config.fee_per_contract_side * position.contracts
            )
            if self.config.require_roll_net_credit and new_net_credit + 1e-9 < old_cost_total:
                continue
            valid.append((evaluation.candidate.strike, new_net_credit - old_cost_total, evaluation))
        if not valid:
            return False
        _strike, roll_credit, evaluation = min(
            valid,
            key=lambda row: (row[0], -row[1]),
        )
        self.state.cash -= old_cost_total
        self.state.reserved_collateral -= position.collateral
        del self.state.short_options[position.position_id]
        self._log(
            day,
            "buy_to_close_for_roll",
            position.underlying,
            position.position_id,
            -old_cost_total,
            position.entry_net_credit_total - old_cost_total,
            f"buyback={old_buyback:.4f};roll_net_before_new={-old_cost_total:.2f}",
        )
        weekly = weekly_trend_state(
            self.data.daily_history(position.underlying, day),
            day,
            self.config,
        )
        new_position = self._open_option_position(
            day=day,
            asset=asset,
            evaluation=evaluation,
            contracts=position.contracts,
            strategy="defensive_roll",
            weekly_state=weekly,
            rolled_from=position.position_id,
            roll_count=position.roll_count + 1,
        )
        self._log(
            day,
            "roll_completed",
            position.underlying,
            new_position.position_id,
            roll_credit,
            None,
            f"old_strike={position.strike:.2f};new_strike={new_position.strike:.2f};roll_credit={roll_credit:.2f}",
        )
        return True

    def _manage_open_options(self, day: date) -> None:
        for position in list(self.state.short_options.values()):
            opportunity = self.data.buyback_opportunity(
                position.contract_symbol,
                day,
                position.profit_target_price,
                exit_slippage_fraction=self.config.exit_slippage_fraction,
                exit_slippage_floor=self.config.exit_slippage_floor,
            )
            if opportunity is not None and day >= position.opened_on:
                self._close_for_profit(day, position, opportunity)
                continue
            bar = self.data.daily_bar(position.underlying, day)
            if bar is None:
                continue
            dte = (position.expiration - day).days
            if (
                position.option_type == "put"
                and self.config.assignment_unwanted
                and not self.config.mode.seeking_assignment
                and dte <= self.config.roll_trigger_dte
                and dte > 0
                and bar.close < position.strike
            ):
                if self._attempt_roll(day, position, bar.close):
                    continue
            if day >= position.expiration:
                self._expire_or_assign(day, position, bar.close)

    def _entry_signal(
        self,
        asset: UniverseAsset,
        day: date,
    ) -> tuple[bool, WeeklyTrendState | None, DailyBar | None, str | None]:
        quality, reasons = asset.quality_check()
        if not quality:
            return False, None, None, ";".join(reasons)
        bar = self.data.daily_bar(asset.symbol, day)
        history = self.data.daily_history(asset.symbol, day)
        if bar is None or len(history) < 2:
            return False, None, bar, "missing_daily_history"
        prior = next((row for row in reversed(history[:-1]) if row.day < day), None)
        if prior is None:
            return False, None, bar, "missing_prior_close"
        daily_return = bar.close / prior.close - 1.0
        if daily_return > self.config.down_day_threshold:
            return False, None, bar, "down_day_threshold_failed"
        weekly = weekly_trend_state(history, day, self.config)
        if weekly.bullish_clouds < self.config.required_bullish_clouds:
            return False, weekly, bar, "weekly_cloud_filter_failed"
        if weekly.weekly_rsi is None:
            return False, weekly, bar, "weekly_rsi_unavailable"
        return True, weekly, bar, None

    def _open_new_puts(self, day: date) -> None:
        if len(self.state.short_options) >= self.config.max_open_option_positions:
            return
        equity = self._portfolio_equity(day)
        for asset in self.universe.values():
            if len(self.state.short_options) >= self.config.max_open_option_positions:
                break
            passed, weekly, bar, reason = self._entry_signal(asset, day)
            if not passed or weekly is None or bar is None:
                if reason not in {"down_day_threshold_failed"}:
                    self._skip(day, asset.symbol, reason or "signal_failed")
                continue
            stock = self.state.stocks.get(asset.symbol)
            if stock and not self.config.average_down_enabled:
                continue
            symbol_allocation = self._symbol_allocation(asset.symbol, day, equity)
            if symbol_allocation >= self.config.max_symbol_allocation - 1e-9:
                self._skip(day, asset.symbol, "symbol_allocation_cap_reached", allocation=symbol_allocation)
                continue
            chain = self.data.option_chain(asset.symbol, day, "put")
            if not chain:
                mode = self.config.mode
                self._request_chain(
                    asset.symbol,
                    day,
                    "put",
                    mode.min_dte,
                    mode.max_dte,
                    mode.target_dte,
                    "new_cash_secured_put",
                    0.70,
                    1.03,
                    0.95 if mode.selection_style == "target_return" else 1.0,
                )
                self._skip(day, asset.symbol, "missing_put_chain")
                continue
            evaluations = evaluate_put_candidates(
                chain,
                day=day,
                spot=bar.close,
                asset=asset,
                mode=self.config.mode,
                config=self.config,
            )
            if not evaluations:
                self._skip(day, asset.symbol, "no_put_contract_passed_iv_return_liquidity_filters")
                continue
            evaluation = evaluations[0]
            target_fraction = allocation_from_weekly_rsi(weekly.weekly_rsi, self.config)
            if not self.config.enable_aggressive_scaling:
                target_fraction = min(target_fraction, self.config.max_trade_allocation)
            remaining_fraction = max(self.config.max_symbol_allocation - symbol_allocation, 0.0)
            allocation = min(target_fraction, remaining_fraction)
            per_contract = collateral_per_contract(
                evaluation.candidate.strike,
                evaluation.gross_credit,
                self.config,
            )
            target_dollars = equity * allocation
            contracts = int(target_dollars // per_contract)
            max_by_cash = int(max(self.state.free_cash, 0.0) // per_contract)
            contracts = min(contracts, max_by_cash)
            if contracts < 1:
                self._skip(
                    day,
                    asset.symbol,
                    "one_contract_exceeds_allocation_or_free_cash",
                    per_contract=per_contract,
                    target_dollars=target_dollars,
                    free_cash=self.state.free_cash,
                )
                continue
            self._open_option_position(
                day=day,
                asset=asset,
                evaluation=evaluation,
                contracts=contracts,
                strategy="cash_secured_put_down_day",
                weekly_state=weekly,
            )

    def _sell_covered_calls(self, day: date) -> None:
        if not self.config.covered_calls_enabled:
            return
        for symbol, stock in list(self.state.stocks.items()):
            available_contracts = (stock.shares - stock.covered_shares) // 100
            if available_contracts <= 0:
                continue
            if len(self.state.short_options) >= self.config.max_open_option_positions:
                return
            bar = self.data.daily_bar(symbol, day)
            if bar is None or stock.average_effective_basis is None:
                continue
            appreciation = bar.close / stock.average_effective_basis - 1.0
            if not bar.green and appreciation < self.config.covered_call_trigger_appreciation:
                continue
            chain = self.data.option_chain(symbol, day, "call")
            if not chain:
                target = 1.0 if self.config.covered_call_style == "atm" else 1.0 + self.config.covered_call_otm_pct
                self._request_chain(
                    symbol,
                    day,
                    "call",
                    self.config.covered_call_min_dte,
                    self.config.covered_call_max_dte,
                    self.config.covered_call_target_dte,
                    "covered_call",
                    0.95,
                    max(1.35, target + 0.05),
                    target,
                )
                continue
            evaluations = evaluate_call_candidates(chain, day=day, spot=bar.close, config=self.config)
            if not evaluations:
                continue
            asset = self.universe[symbol]
            weekly = weekly_trend_state(self.data.daily_history(symbol, day), day, self.config)
            self._open_option_position(
                day=day,
                asset=asset,
                evaluation=evaluations[0],
                contracts=available_contracts,
                strategy="covered_call_after_assignment",
                weekly_state=weekly,
            )

    def _record_equity(self, day: date) -> None:
        stock_value = 0.0
        for symbol, stock in self.state.stocks.items():
            bar = self.data.daily_bar(symbol, day)
            if bar:
                stock_value += stock.shares * bar.close
        liability = 0.0
        for position in self.state.short_options.values():
            mark = self.data.option_liability_mark(position.contract_symbol, day)
            if mark is None:
                bar = self.data.daily_bar(position.underlying, day)
                spot = bar.close if bar else position.strike
                mark = (
                    max(position.strike - spot, 0.0)
                    if position.option_type == "put"
                    else max(spot - position.strike, 0.0)
                )
            liability += mark * 100.0 * position.contracts
        equity = self.state.cash + stock_value - liability
        self.state.daily_equity.append(
            DailyEquity(
                day=day,
                cash=_round_money(self.state.cash),
                reserved_collateral=_round_money(self.state.reserved_collateral),
                stock_value=_round_money(stock_value),
                option_liability=_round_money(liability),
                equity=_round_money(equity),
                free_cash=_round_money(self.state.free_cash),
                open_options=len(self.state.short_options),
            )
        )

    def run(
        self,
        start: date,
        end: date,
        *,
        entry_end: date | None = None,
    ) -> BacktestResult:
        effective_entry_end = entry_end or end
        if effective_entry_end < start or effective_entry_end > end:
            raise ValueError("entry_end must fall within the backtest period")
        for day in self.data.trading_days(start, end):
            self._manage_open_options(day)
            self._sell_covered_calls(day)
            if day <= effective_entry_end:
                self._open_new_puts(day)
            self._record_equity(day)
        result = self.result(start, end)
        result.config["entry_end"] = effective_entry_end.isoformat()
        return result

    def result(self, start: date, end: date) -> BacktestResult:
        equities = [row.equity for row in self.state.daily_equity]
        ending_equity = equities[-1] if equities else self.state.initial_cash
        peak = self.state.initial_cash
        max_drawdown = 0.0
        for value in equities:
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, value / peak - 1.0 if peak else 0.0)
        realized = [event.realized_pnl for event in self.state.events if event.realized_pnl is not None]
        closed_events = [
            event for event in self.state.events
            if event.event in {
                "buy_to_close_50pct",
                "put_expired_otm",
                "call_expired_otm",
                "call_assigned_shares_called",
                "buy_to_close_for_roll",
            }
        ]
        summary = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "initial_cash": self.state.initial_cash,
            "ending_equity": ending_equity,
            "total_return_pct": (ending_equity / self.state.initial_cash - 1.0) * 100.0,
            "max_drawdown_pct": max_drawdown * 100.0,
            "events": len(self.state.events),
            "closed_option_events": len(closed_events),
            "realized_pnl": sum(realized),
            "win_rate_pct": (
                sum((event.realized_pnl or 0.0) > 0 for event in closed_events)
                / len(closed_events)
                * 100.0
                if closed_events else None
            ),
            "open_options": len(self.state.short_options),
            "stock_symbols": len(self.state.stocks),
            "data_requests": len(self.state.data_requests),
            "skips": len(self.state.skips),
            "research_grade": False,
            "research_grade_blockers": [
                "historical option NBBO is unavailable; trade-bar proxies are used",
                "curated quality whitelist is not point-in-time fundamentals data",
                "historical borrow, taxes, dividends, distributions, and contract adjustments are incomplete",
                "risk-neutral POP is a model estimate, not an observed probability",
            ],
        }
        return BacktestResult(
            config={
                **asdict(self.config),
                "mode": asdict(self.config.mode),
                "entry_time_et": self.config.entry_time_et.isoformat(timespec="minutes"),
            },
            summary=summary,
            events=tuple(self.state.events),
            daily_equity=tuple(self.state.daily_equity),
            skips=tuple(self.state.skips),
            data_requests=tuple(self.state.data_requests),
            open_options=tuple(asdict(position) for position in self.state.short_options.values()),
            stock_positions=tuple(asdict(position) for position in self.state.stocks.values()),
        )


# ---------------------------------------------------------------------------
# Defaults, output, and CLI
# ---------------------------------------------------------------------------


def default_universe() -> tuple[UniverseAsset, ...]:
    """Return the thesis-specified universe as a curated research whitelist.

    Numeric fundamentals are deliberately left unset.  ``quality_approved`` is
    therefore False until a dated fundamentals snapshot or an explicit user
    override is loaded from JSON.  This prevents the backtester from silently
    fabricating point-in-time quality data.
    """
    return (
        UniverseAsset(
            "NVDL", "NVDA", "single_company", False, "unverified", 2.0,
            notes="Leveraged NVDA exposure; requires dated NVDA fundamentals approval.",
        ),
        UniverseAsset(
            "TSLL", "TSLA", "single_company", False, "unverified", 2.0,
            notes="Leveraged TSLA exposure; requires dated TSLA fundamentals approval.",
        ),
        UniverseAsset(
            "SOXL", "SOXX", "index_basket", False, "unverified", 3.0,
            notes="Semiconductor basket exception; not every component satisfies a $200B rule.",
        ),
        UniverseAsset(
            "TQQQ", "NDX", "index_basket", False, "unverified", 3.0,
            notes="Nasdaq-100 basket exception; quality applies at basket level.",
        ),
    )


def load_universe(path: str | Path | None) -> tuple[UniverseAsset, ...]:
    if path is None:
        return default_universe()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("assets") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("universe JSON must contain an assets array")
    return tuple(UniverseAsset(**row) for row in rows)


def discover_archives(root: str | Path) -> tuple[Path, ...]:
    path = Path(root)
    if not path.exists():
        return ()
    return tuple(sorted(db.parent for db in path.rglob("historical_options.sqlite")))


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_result(result: BacktestResult, output_dir: str | Path) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    events_path = output / "events.csv"
    equity_path = output / "daily_equity.csv"
    skips_path = output / "skips.csv"
    requests_path = output / "data_requests.csv"
    report_path = output / "report.json"
    write_csv(events_path, [asdict(row) for row in result.events])
    write_csv(equity_path, [asdict(row) for row in result.daily_equity])
    write_csv(skips_path, list(result.skips))
    write_csv(requests_path, [asdict(row) for row in result.data_requests])
    payload = {
        "config": result.config,
        "summary": result.summary,
        "open_options": list(result.open_options),
        "stock_positions": list(result.stock_positions),
        "files": {
            "events": str(events_path),
            "daily_equity": str(equity_path),
            "skips": str(skips_path),
            "data_requests": str(requests_path),
        },
    }
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    payload["files"]["report"] = str(report_path)
    return payload["files"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest the leveraged-ETF CSP/wheel thesis.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--entry-end",
        help="Last date on which new cash-secured puts may be opened; management continues through --end.",
    )
    parser.add_argument("--mode", choices=tuple(DEFAULT_MODES), default="standard")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--equity-db", default=str(DEFAULT_EQUITY_DB))
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE_ROOT))
    parser.add_argument(
        "--additional-archive-root",
        action="append",
        default=[],
        help="Add another recursively discovered option-archive root; repeatable.",
    )
    parser.add_argument("--universe-json")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT / "latest"))
    parser.add_argument("--down-day", type=float, default=-0.05)
    parser.add_argument("--minimum-iv", type=float, default=0.40)
    parser.add_argument("--maximum-iv", type=float, default=0.70)
    parser.add_argument(
        "--minimum-pop",
        type=float,
        help="Override the mode's minimum modeled probability of profit.",
    )
    parser.add_argument(
        "--required-bullish-clouds",
        type=int,
        choices=(1, 2, 3),
        default=2,
    )
    parser.add_argument("--aggressive-scaling", action="store_true")
    parser.add_argument("--seeking-assignment", action="store_true")
    parser.add_argument(
        "--relax-pop",
        action="store_true",
        help="Do not enforce the mode's modeled POP floor; diagnostic only.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = DEFAULT_MODES[args.mode]
    if args.seeking_assignment and not mode.seeking_assignment:
        mode = PutMode(
            **{**asdict(mode), "seeking_assignment": True}
        )
    if args.relax_pop and args.minimum_pop is not None:
        raise SystemExit("--minimum-pop cannot be combined with --relax-pop")
    target_pop_tolerance = 0.05
    if args.minimum_pop is not None:
        if mode.target_pop is None:
            raise SystemExit(f"mode {mode.name} does not define a target POP")
        if not 0 < args.minimum_pop <= mode.target_pop:
            raise SystemExit(
                f"--minimum-pop must be in (0, {mode.target_pop:.2f}] for mode {mode.name}"
            )
        target_pop_tolerance = mode.target_pop - args.minimum_pop
    config = WheelConfig(
        mode=mode,
        down_day_threshold=args.down_day,
        minimum_iv=args.minimum_iv,
        maximum_iv=args.maximum_iv,
        required_bullish_clouds=args.required_bullish_clouds,
        enable_aggressive_scaling=args.aggressive_scaling,
        assignment_unwanted=not (args.seeking_assignment or mode.seeking_assignment),
        enforce_target_pop=not args.relax_pop,
        target_pop_tolerance=target_pop_tolerance,
    )
    universe = load_universe(args.universe_json)
    archive_roots = [args.archive_root, *args.additional_archive_root]
    archives = tuple(
        dict.fromkeys(
            archive
            for root in archive_roots
            for archive in discover_archives(root)
        )
    )
    data = SQLiteWheelMarketData(args.equity_db, archives, entry_time_et=config.entry_time_et)
    try:
        backtester = LeveragedEtfWheelBacktester(
            data,
            universe,
            config,
            initial_cash=args.initial_cash,
        )
        result = backtester.run(
            date.fromisoformat(args.start),
            date.fromisoformat(args.end),
            entry_end=date.fromisoformat(args.entry_end) if args.entry_end else None,
        )
        files = write_result(result, args.output_dir)
        print(json.dumps({"summary": result.summary, "files": files}, indent=2, sort_keys=True, default=str))
    finally:
        data.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
