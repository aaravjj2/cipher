"""Point-in-time options execution and settlement primitives.

This module is intentionally separate from ``price_backtest.py``. It prices
actual option contracts from timestamped bid/ask quotes and refuses to fall
back to underlying-price proxies or fabricated spreads.

The engine is research-only and read-only: it contains no broker/order API.
It provides deterministic building blocks that can later feed a larger event-
driven backtester or an adapter for LEAN/Optopsy-compatible datasets.

Implemented controls:
- strict as-of quote lookup (never selects a future quote);
- stale-quote rejection;
- side-aware bid/ask execution with configurable slippage;
- commissions and per-contract fees applied exactly once per fill;
- atomic multi-leg package accounting;
- defined-risk vertical/iron-condor collateral calculations;
- covered-call and cash-secured-put collateral checks;
- expiration intrinsic-value settlement;
- deterministic early-assignment risk checks for American short options.

Not yet modeled here:
- partial fills or legging risk;
- portfolio-level cross-margin/portfolio margin;
- stock inventory changes after physical exercise/assignment;
- OCC contract-adjustment history;
- stochastic assignment behavior.

Those omissions are explicit blockers for live-deployment claims.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from math import exp, isfinite
from typing import Iterable, Mapping, Sequence


class OptionsBacktestError(RuntimeError):
    """Base error for deterministic backtest failures."""


class QuoteUnavailableError(OptionsBacktestError):
    """Raised when no valid point-in-time quote is available."""


class MarginModelError(OptionsBacktestError):
    """Raised when a position cannot be represented by the v1 margin model."""


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite_number(value: float, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


@dataclass(frozen=True, slots=True)
class OptionContract:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiration: date
    multiplier: int = 100
    exercise_style: str = "american"
    settlement: str = "physical"

    def __post_init__(self) -> None:
        for field_name, value in {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "option_type": self.option_type,
            "exercise_style": self.exercise_style,
            "settlement": self.settlement,
        }.items():
            if not isinstance(value, str):
                raise ValueError(f"{field_name} must be a string")
        symbol = self.symbol.strip()
        underlying = self.underlying.upper().strip()
        option_type = self.option_type.lower().strip()
        style = self.exercise_style.lower().strip()
        settlement = self.settlement.lower().strip()
        strike = _finite_number(self.strike, field_name="strike")
        if not symbol:
            raise ValueError("symbol is required")
        if not underlying:
            raise ValueError("underlying is required")
        if option_type not in {"call", "put"}:
            raise ValueError("option_type must be 'call' or 'put'")
        if strike <= 0:
            raise ValueError("strike must be positive")
        if not isinstance(self.expiration, date) or isinstance(self.expiration, datetime):
            raise ValueError("expiration must be a date")
        if not isinstance(self.multiplier, int) or isinstance(self.multiplier, bool):
            raise ValueError("multiplier must be an integer")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")
        if style not in {"american", "european"}:
            raise ValueError("exercise_style must be 'american' or 'european'")
        if settlement not in {"physical", "cash"}:
            raise ValueError("settlement must be 'physical' or 'cash'")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "underlying", underlying)
        object.__setattr__(self, "option_type", option_type)
        object.__setattr__(self, "strike", strike)
        object.__setattr__(self, "exercise_style", style)
        object.__setattr__(self, "settlement", settlement)


@dataclass(frozen=True, slots=True)
class OptionQuote:
    contract: OptionContract
    timestamp: datetime
    bid: float
    ask: float
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    def __post_init__(self) -> None:
        timestamp = _require_aware_utc(self.timestamp, field_name="timestamp")
        bid = _finite_number(self.bid, field_name="bid")
        ask = _finite_number(self.ask, field_name="ask")
        if bid < 0 or ask < 0:
            raise ValueError("bid and ask must be non-negative")
        if ask < bid:
            raise ValueError("crossed quote: ask is below bid")

        optional_numbers = {
            "last": self.last,
            "implied_volatility": self.implied_volatility,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
        }
        normalized_optional: dict[str, float | None] = {}
        for name, value in optional_numbers.items():
            normalized_optional[name] = (
                None if value is None else _finite_number(value, field_name=name)
            )
        if normalized_optional["last"] is not None and normalized_optional["last"] < 0:
            raise ValueError("last cannot be negative")
        if (
            normalized_optional["implied_volatility"] is not None
            and normalized_optional["implied_volatility"] < 0
        ):
            raise ValueError("implied_volatility cannot be negative")
        if normalized_optional["delta"] is not None and not -1 <= normalized_optional["delta"] <= 1:
            raise ValueError("delta must be between -1 and 1")

        for name, value in {"volume": self.volume, "open_interest": self.open_interest}.items():
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{name} must be an integer")
                if value < 0:
                    raise ValueError(f"{name} cannot be negative")

        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)
        for name, value in normalized_optional.items():
            object.__setattr__(self, name, value)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """Signed position/order quantity: positive=long/buy, negative=short/sell."""

    contract: OptionContract
    quantity: int

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise ValueError("leg quantity must be an integer")
        if self.quantity == 0:
            raise ValueError("leg quantity cannot be zero")


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    entry_slippage_bps: float = 0.0
    exit_slippage_bps: float = 0.0
    max_quote_age_seconds: int = 15 * 60
    commission_per_contract: float = 0.65
    exchange_fee_per_contract: float = 0.03
    regulatory_fee_per_contract: float = 0.0
    exercise_assignment_fee_per_contract: float = 0.0
    assignment_extrinsic_buffer: float = 0.01

    def __post_init__(self) -> None:
        numeric_nonnegative = {
            "entry_slippage_bps": self.entry_slippage_bps,
            "exit_slippage_bps": self.exit_slippage_bps,
            "commission_per_contract": self.commission_per_contract,
            "exchange_fee_per_contract": self.exchange_fee_per_contract,
            "regulatory_fee_per_contract": self.regulatory_fee_per_contract,
            "exercise_assignment_fee_per_contract": self.exercise_assignment_fee_per_contract,
            "assignment_extrinsic_buffer": self.assignment_extrinsic_buffer,
        }
        for name, value in numeric_nonnegative.items():
            number = _finite_number(value, field_name=name)
            if number < 0:
                raise ValueError(f"{name} cannot be negative")
            object.__setattr__(self, name, number)
        if not isinstance(self.max_quote_age_seconds, int) or isinstance(
            self.max_quote_age_seconds, bool
        ):
            raise ValueError("max_quote_age_seconds must be an integer")
        if self.max_quote_age_seconds < 0:
            raise ValueError("max_quote_age_seconds cannot be negative")

    @property
    def per_contract_fill_fee(self) -> float:
        return (
            self.commission_per_contract
            + self.exchange_fee_per_contract
            + self.regulatory_fee_per_contract
        )


class QuoteBook:
    """Immutable-enough point-in-time quote index with strict as-of lookup."""

    def __init__(self, quotes: Iterable[OptionQuote]):
        grouped: dict[str, list[OptionQuote]] = {}
        for quote in quotes:
            grouped.setdefault(quote.contract.symbol, []).append(quote)

        self._quotes: dict[str, tuple[OptionQuote, ...]] = {}
        self._timestamps: dict[str, tuple[datetime, ...]] = {}
        for symbol, rows in grouped.items():
            rows.sort(key=lambda row: row.timestamp)
            self._quotes[symbol] = tuple(rows)
            self._timestamps[symbol] = tuple(row.timestamp for row in rows)

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._quotes))

    def asof(
        self,
        symbol: str,
        timestamp: datetime,
        *,
        max_age_seconds: int,
    ) -> OptionQuote:
        timestamp = _require_aware_utc(timestamp, field_name="timestamp")
        if not isinstance(max_age_seconds, int) or isinstance(max_age_seconds, bool):
            raise ValueError("max_age_seconds must be an integer")
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds cannot be negative")
        rows = self._quotes.get(symbol)
        times = self._timestamps.get(symbol)
        if not rows or not times:
            raise QuoteUnavailableError(f"no quotes available for {symbol}")

        index = bisect_right(times, timestamp) - 1
        if index < 0:
            raise QuoteUnavailableError(
                f"no quote for {symbol} at or before {timestamp.isoformat()}"
            )

        quote = rows[index]
        age_seconds = (timestamp - quote.timestamp).total_seconds()
        if age_seconds > max_age_seconds:
            raise QuoteUnavailableError(
                f"stale quote for {symbol}: age={age_seconds:.0f}s, "
                f"limit={max_age_seconds}s"
            )
        return quote


@dataclass(frozen=True, slots=True)
class LegFill:
    contract: OptionContract
    quantity: int
    quote_timestamp: datetime
    fill_timestamp: datetime
    fill_price: float
    gross_cash_flow: float
    fees: float

    @property
    def net_cash_flow(self) -> float:
        return self.gross_cash_flow - self.fees


@dataclass(frozen=True, slots=True)
class PackageExecution:
    timestamp: datetime
    fills: tuple[LegFill, ...]

    @property
    def gross_cash_flow(self) -> float:
        return sum(fill.gross_cash_flow for fill in self.fills)

    @property
    def fees(self) -> float:
        return sum(fill.fees for fill in self.fills)

    @property
    def net_cash_flow(self) -> float:
        return sum(fill.net_cash_flow for fill in self.fills)


@dataclass(frozen=True, slots=True)
class OptionPosition:
    position_id: str
    strategy: str
    opened_at: datetime
    legs: tuple[OptionLeg, ...]
    entry_execution: PackageExecution
    collateral_required: float
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClosedPosition:
    position: OptionPosition
    closed_at: datetime
    exit_execution: PackageExecution

    @property
    def pnl(self) -> float:
        return self.position.entry_execution.net_cash_flow + self.exit_execution.net_cash_flow


@dataclass(frozen=True, slots=True)
class AssignmentEvent:
    contract: OptionContract
    timestamp: datetime
    contracts: int
    spot: float
    option_mid: float
    intrinsic: float
    extrinsic: float
    reason: str
    cash_equivalent_flow: float
    fee: float


@dataclass(frozen=True, slots=True)
class ExpirationSettlement:
    position: OptionPosition
    timestamp: datetime
    spot: float
    intrinsic_cash_flow: float
    fees: float

    @property
    def pnl(self) -> float:
        return self.position.entry_execution.net_cash_flow + self.intrinsic_cash_flow - self.fees


def option_intrinsic(contract: OptionContract, spot: float) -> float:
    normalized_spot = _finite_number(spot, field_name="spot")
    if normalized_spot < 0:
        raise ValueError("spot cannot be negative")
    if contract.option_type == "call":
        return max(normalized_spot - contract.strike, 0.0)
    return max(contract.strike - normalized_spot, 0.0)


def _slipped_price(base_price: float, *, is_buy: bool, slippage_bps: float) -> float:
    normalized_base = _finite_number(base_price, field_name="base_price")
    normalized_slippage = _finite_number(slippage_bps, field_name="slippage_bps")
    if normalized_base < 0:
        raise ValueError("base_price cannot be negative")
    if normalized_slippage < 0:
        raise ValueError("slippage_bps cannot be negative")
    fraction = normalized_slippage / 10_000.0
    if is_buy:
        return normalized_base * (1.0 + fraction)
    return max(normalized_base * (1.0 - fraction), 0.0)


def _execute_package(
    quote_book: QuoteBook,
    legs: Sequence[OptionLeg],
    timestamp: datetime,
    *,
    config: ExecutionConfig,
    is_entry: bool,
) -> PackageExecution:
    timestamp = _require_aware_utc(timestamp, field_name="timestamp")
    if not legs:
        raise ValueError("at least one leg is required")

    slippage_bps = config.entry_slippage_bps if is_entry else config.exit_slippage_bps
    fills: list[LegFill] = []
    for leg in legs:
        quote = quote_book.asof(
            leg.contract.symbol,
            timestamp,
            max_age_seconds=config.max_quote_age_seconds,
        )
        if quote.contract != leg.contract:
            raise OptionsBacktestError(
                f"contract metadata mismatch for {leg.contract.symbol}"
            )

        is_buy = leg.quantity > 0
        executable = quote.ask if is_buy else quote.bid
        fill_price = _slipped_price(
            executable,
            is_buy=is_buy,
            slippage_bps=slippage_bps,
        )
        gross_cash_flow = -leg.quantity * fill_price * leg.contract.multiplier
        fees = abs(leg.quantity) * config.per_contract_fill_fee
        fills.append(
            LegFill(
                contract=leg.contract,
                quantity=leg.quantity,
                quote_timestamp=quote.timestamp,
                fill_timestamp=timestamp,
                fill_price=fill_price,
                gross_cash_flow=gross_cash_flow,
                fees=fees,
            )
        )

    return PackageExecution(timestamp=timestamp, fills=tuple(fills))


def _vertical_width_and_contracts(legs: Sequence[OptionLeg]) -> tuple[float, int] | None:
    if len(legs) != 2:
        return None
    first, second = legs
    if first.contract.option_type != second.contract.option_type:
        return None
    if first.contract.expiration != second.contract.expiration:
        return None
    if first.contract.underlying != second.contract.underlying:
        return None
    if first.quantity != -second.quantity:
        return None
    contracts = abs(first.quantity)
    width = abs(first.contract.strike - second.contract.strike)
    if width <= 0:
        return None
    return width, contracts


def calculate_collateral(
    legs: Sequence[OptionLeg],
    *,
    entry_gross_cash_flow: float,
    underlying_shares: int = 0,
    cash_available: float | None = None,
) -> float:
    """Calculate conservative Reg-T-style collateral for supported structures.

    The function intentionally rejects naked/ratio structures instead of
    guessing broker-specific margin. ``entry_gross_cash_flow`` excludes fees;
    positive values are credits and negative values are debits.
    """
    if not legs:
        raise ValueError("at least one leg is required")
    entry_gross_cash_flow = _finite_number(
        entry_gross_cash_flow,
        field_name="entry_gross_cash_flow",
    )
    if not isinstance(underlying_shares, int) or isinstance(underlying_shares, bool):
        raise ValueError("underlying_shares must be an integer")
    if underlying_shares < 0:
        raise ValueError("underlying_shares cannot be negative")
    if cash_available is not None:
        cash_available = _finite_number(cash_available, field_name="cash_available")
        if cash_available < 0:
            raise ValueError("cash_available cannot be negative")

    # Pure long premium positions are fully paid debit positions.
    if all(leg.quantity > 0 for leg in legs):
        return max(-entry_gross_cash_flow, 0.0)

    vertical = _vertical_width_and_contracts(legs)
    if vertical:
        width, contracts = vertical
        max_width_loss = width * legs[0].contract.multiplier * contracts
        if entry_gross_cash_flow >= 0:
            return max(max_width_loss - entry_gross_cash_flow, 0.0)
        return -entry_gross_cash_flow

    # Balanced iron condor: one vertical call spread plus one vertical put spread.
    if len(legs) == 4:
        calls = [leg for leg in legs if leg.contract.option_type == "call"]
        puts = [leg for leg in legs if leg.contract.option_type == "put"]
        call_vertical = _vertical_width_and_contracts(calls)
        put_vertical = _vertical_width_and_contracts(puts)
        expirations = {leg.contract.expiration for leg in legs}
        underlyings = {leg.contract.underlying for leg in legs}
        if call_vertical and put_vertical and len(expirations) == 1 and len(underlyings) == 1:
            call_width, call_contracts = call_vertical
            put_width, put_contracts = put_vertical
            if call_contracts != put_contracts:
                raise MarginModelError("unbalanced iron-condor quantities are unsupported")
            multiplier = legs[0].contract.multiplier
            max_side_width = max(call_width, put_width) * multiplier * call_contracts
            if entry_gross_cash_flow >= 0:
                return max(max_side_width - entry_gross_cash_flow, 0.0)
            return -entry_gross_cash_flow

    # Covered call: only the short call needs coverage; long option legs are debit-paid.
    short_calls = [
        leg for leg in legs
        if leg.quantity < 0 and leg.contract.option_type == "call"
    ]
    other_shorts = [
        leg for leg in legs
        if leg.quantity < 0 and leg.contract.option_type != "call"
    ]
    if short_calls and not other_shorts:
        required_shares = sum(
            abs(leg.quantity) * leg.contract.multiplier for leg in short_calls
        )
        if underlying_shares >= required_shares:
            return max(-entry_gross_cash_flow, 0.0)

    # Cash-secured put: support one or more short puts with no other short legs.
    short_puts = [
        leg for leg in legs
        if leg.quantity < 0 and leg.contract.option_type == "put"
    ]
    other_shorts = [
        leg for leg in legs
        if leg.quantity < 0 and leg.contract.option_type != "put"
    ]
    if short_puts and not other_shorts:
        strike_obligation = sum(
            abs(leg.quantity)
            * leg.contract.strike
            * leg.contract.multiplier
            for leg in short_puts
        )
        required = max(strike_obligation - max(entry_gross_cash_flow, 0.0), 0.0)
        if cash_available is not None and cash_available < required:
            raise MarginModelError(
                f"cash-secured put requires ${required:.2f}, "
                f"only ${cash_available:.2f} supplied"
            )
        return required

    raise MarginModelError(
        "unsupported or undefined-risk structure; use a broker/LEAN margin model "
        "instead of approximating naked or ratio-option margin"
    )


class PointInTimeOptionsEngine:
    """Deterministic options fill, margin, close, and settlement engine."""

    def __init__(
        self,
        quotes: Iterable[OptionQuote],
        *,
        config: ExecutionConfig | None = None,
    ):
        self.quote_book = QuoteBook(quotes)
        self.config = config or ExecutionConfig()
        self._sequence = 0

    def open_position(
        self,
        strategy: str,
        timestamp: datetime,
        legs: Sequence[OptionLeg],
        *,
        underlying_shares: int = 0,
        cash_available: float | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> OptionPosition:
        timestamp = _require_aware_utc(timestamp, field_name="timestamp")
        execution = _execute_package(
            self.quote_book,
            legs,
            timestamp,
            config=self.config,
            is_entry=True,
        )
        collateral = calculate_collateral(
            legs,
            entry_gross_cash_flow=execution.gross_cash_flow,
            underlying_shares=underlying_shares,
            cash_available=cash_available,
        )
        self._sequence += 1
        position_id = f"OPT-{timestamp.strftime('%Y%m%dT%H%M%S')}-{self._sequence:06d}"
        return OptionPosition(
            position_id=position_id,
            strategy=strategy,
            opened_at=timestamp,
            legs=tuple(legs),
            entry_execution=execution,
            collateral_required=collateral,
            metadata=dict(metadata or {}),
        )

    def close_position(
        self,
        position: OptionPosition,
        timestamp: datetime,
    ) -> ClosedPosition:
        timestamp = _require_aware_utc(timestamp, field_name="timestamp")
        if timestamp < position.opened_at:
            raise ValueError("close timestamp cannot precede open timestamp")
        closing_legs = tuple(
            OptionLeg(contract=leg.contract, quantity=-leg.quantity)
            for leg in position.legs
        )
        execution = _execute_package(
            self.quote_book,
            closing_legs,
            timestamp,
            config=self.config,
            is_entry=False,
        )
        return ClosedPosition(
            position=position,
            closed_at=timestamp,
            exit_execution=execution,
        )

    def settle_expiration(
        self,
        position: OptionPosition,
        *,
        timestamp: datetime,
        spot: float,
    ) -> ExpirationSettlement:
        timestamp = _require_aware_utc(timestamp, field_name="timestamp")
        spot = _finite_number(spot, field_name="spot")
        if spot < 0:
            raise ValueError("spot cannot be negative")
        for leg in position.legs:
            if timestamp.date() < leg.contract.expiration:
                raise ValueError(
                    f"cannot expire {leg.contract.symbol} before {leg.contract.expiration}"
                )

        intrinsic_cash_flow = 0.0
        exercised_contracts = 0
        for leg in position.legs:
            intrinsic = option_intrinsic(leg.contract, spot)
            intrinsic_cash_flow += (
                leg.quantity * intrinsic * leg.contract.multiplier
            )
            if intrinsic > 0:
                exercised_contracts += abs(leg.quantity)

        fees = (
            exercised_contracts
            * self.config.exercise_assignment_fee_per_contract
        )
        return ExpirationSettlement(
            position=position,
            timestamp=timestamp,
            spot=spot,
            intrinsic_cash_flow=intrinsic_cash_flow,
            fees=fees,
        )

    def early_assignment_risk(
        self,
        position: OptionPosition,
        *,
        timestamp: datetime,
        spot: float,
        ex_dividend_amount: float = 0.0,
        annual_risk_free_rate: float = 0.0,
    ) -> tuple[AssignmentEvent, ...]:
        """Return deterministic economically-rational assignment events.

        This method models the option cash-equivalent only. Physical share
        delivery/receipt must be handled by a separate stock-inventory ledger.
        European-style contracts and long option legs are never assigned here.
        """
        timestamp = _require_aware_utc(timestamp, field_name="timestamp")
        spot = _finite_number(spot, field_name="spot")
        ex_dividend_amount = _finite_number(
            ex_dividend_amount,
            field_name="ex_dividend_amount",
        )
        annual_risk_free_rate = _finite_number(
            annual_risk_free_rate,
            field_name="annual_risk_free_rate",
        )
        if spot < 0:
            raise ValueError("spot cannot be negative")
        if ex_dividend_amount < 0:
            raise ValueError("ex_dividend_amount cannot be negative")

        events: list[AssignmentEvent] = []
        for leg in position.legs:
            contract = leg.contract
            if leg.quantity >= 0 or contract.exercise_style != "american":
                continue
            if timestamp.date() >= contract.expiration:
                continue

            quote = self.quote_book.asof(
                contract.symbol,
                timestamp,
                max_age_seconds=self.config.max_quote_age_seconds,
            )
            intrinsic = option_intrinsic(contract, spot)
            if intrinsic <= 0:
                continue
            extrinsic = max(quote.mid - intrinsic, 0.0)
            buffer = self.config.assignment_extrinsic_buffer
            reason: str | None = None

            if contract.option_type == "call":
                if ex_dividend_amount > extrinsic + buffer:
                    reason = "dividend_exceeds_extrinsic"
            else:
                days = max((contract.expiration - timestamp.date()).days, 0)
                tau = days / 365.0
                carry_benefit = contract.strike * (1.0 - exp(-annual_risk_free_rate * tau))
                if carry_benefit > extrinsic + buffer:
                    reason = "put_carry_exceeds_extrinsic"

            if reason is None:
                continue

            contracts = abs(leg.quantity)
            cash_equivalent_flow = (
                leg.quantity * intrinsic * contract.multiplier
            )
            fee = (
                contracts
                * self.config.exercise_assignment_fee_per_contract
            )
            events.append(
                AssignmentEvent(
                    contract=contract,
                    timestamp=timestamp,
                    contracts=contracts,
                    spot=spot,
                    option_mid=quote.mid,
                    intrinsic=intrinsic,
                    extrinsic=extrinsic,
                    reason=reason,
                    cash_equivalent_flow=cash_equivalent_flow,
                    fee=fee,
                )
            )

        return tuple(events)


def audit_quote_history(quotes: Iterable[OptionQuote]) -> dict:
    """Return a compact data-quality audit for point-in-time quote history."""
    rows = list(quotes)
    by_symbol: dict[str, list[OptionQuote]] = {}
    for row in rows:
        by_symbol.setdefault(row.contract.symbol, []).append(row)

    errors: list[str] = []
    warnings: list[str] = []
    duplicate_keys: set[tuple[str, datetime]] = set()
    seen_keys: set[tuple[str, datetime]] = set()
    unique_timestamps: set[datetime] = set()
    relative_spread_pairs: list[tuple[float, float]] = []

    for row in rows:
        key = (row.contract.symbol, row.timestamp)
        if key in seen_keys:
            duplicate_keys.add(key)
        seen_keys.add(key)
        unique_timestamps.add(row.timestamp)
        if row.bid == 0 and row.ask == 0:
            warnings.append(f"zero market for {row.contract.symbol} at {row.timestamp.isoformat()}")
        if (
            row.last is not None
            and row.last > 0
            and row.bid <= row.last <= row.ask
        ):
            relative_spread_pairs.append(
                (
                    round((row.last - row.bid) / row.last, 6),
                    round((row.ask - row.last) / row.last, 6),
                )
            )

    if duplicate_keys:
        errors.append(f"duplicate symbol/timestamp quotes: {len(duplicate_keys)}")
    if len(unique_timestamps) < 2:
        errors.append("dataset has fewer than two distinct quote timestamps")

    # Detect mechanically generated bid/ask values such as bid=0.98*close and
    # ask=1.02*close across an entire legacy export. Real observed markets may
    # repeat tick spreads, but a constant non-zero *relative* offset across at
    # least 20 varied observations is strong evidence of synthetic prices.
    if len(relative_spread_pairs) >= 20:
        pair, count = Counter(relative_spread_pairs).most_common(1)[0]
        dominance = count / len(relative_spread_pairs)
        if dominance >= 0.95 and (pair[0] > 0 or pair[1] > 0):
            errors.append(
                "bid/ask appear mechanically derived from last: "
                f"relative offsets {pair} repeat in {dominance:.1%} of quotes"
            )

    single_snapshot_contracts = [
        symbol for symbol, symbol_rows in by_symbol.items() if len(symbol_rows) < 2
    ]
    if single_snapshot_contracts:
        warnings.append(
            f"contracts with only one quote: {len(single_snapshot_contracts)}"
        )

    return {
        "quote_count": len(rows),
        "contract_count": len(by_symbol),
        "distinct_timestamps": len(unique_timestamps),
        "errors": errors,
        "warnings": warnings,
        "eligible_for_time_series_backtest": not errors,
    }
