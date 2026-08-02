"""Event-driven portfolio runner for point-in-time option positions.

The lower-level execution engine handles quotes, fills, fees, margin, and
settlement. This module adds a deterministic portfolio ledger with:

- collateral and position-count limits;
- liquidation-value mark-to-market equity;
- realized/unrealized P&L separation;
- scheduled open, close, mark, expiration, and assignment-risk events;
- equity snapshots and max-drawdown calculation;
- explicit rejection of unresolved physical assignment transitions.

It remains a research engine, not an order-routing system. Assignment events
are detected but cannot mutate stock inventory until a separate physical-share
ledger exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Mapping, Sequence

try:
    from .option_backtest_engine import (
        AssignmentEvent,
        ClosedPosition,
        OptionLeg,
        OptionPosition,
        OptionsBacktestError,
        PointInTimeOptionsEngine,
        QuoteUnavailableError,
    )
except ImportError:  # Direct module import in tests/scripts.
    from option_backtest_engine import (
        AssignmentEvent,
        ClosedPosition,
        OptionLeg,
        OptionPosition,
        OptionsBacktestError,
        PointInTimeOptionsEngine,
        QuoteUnavailableError,
    )


class PortfolioBacktestError(RuntimeError):
    """Base error for portfolio-level backtest failures."""


class InsufficientBuyingPowerError(PortfolioBacktestError):
    """Raised when a new position violates collateral constraints."""


class PositionNotFoundError(PortfolioBacktestError):
    """Raised when an event references a missing open position."""


class DuplicatePositionReferenceError(PortfolioBacktestError):
    """Raised when a scheduled open reuses an active reference."""


class UnresolvedAssignmentError(PortfolioBacktestError):
    """Raised when physical assignment needs a stock-inventory transition."""


class InvalidScheduledActionError(PortfolioBacktestError):
    """Raised when a scheduled action is incomplete or unsupported."""


def _utc(value: datetime, *, field_name: str = "timestamp") -> datetime:
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
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    starting_cash: float = 100_000.0
    max_open_positions: int = 10
    max_collateral_fraction: float = 1.0

    def __post_init__(self) -> None:
        starting_cash = _finite_number(self.starting_cash, field_name="starting_cash")
        max_collateral_fraction = _finite_number(
            self.max_collateral_fraction,
            field_name="max_collateral_fraction",
        )
        if starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        if not isinstance(self.max_open_positions, int) or isinstance(
            self.max_open_positions, bool
        ):
            raise ValueError("max_open_positions must be an integer")
        if self.max_open_positions <= 0:
            raise ValueError("max_open_positions must be positive")
        if not 0 < max_collateral_fraction <= 1:
            raise ValueError("max_collateral_fraction must be in (0, 1]")
        object.__setattr__(self, "starting_cash", starting_cash)
        object.__setattr__(
            self,
            "max_collateral_fraction",
            max_collateral_fraction,
        )


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    timestamp: datetime
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    collateral_used: float
    free_buying_power: float
    open_positions: int


@dataclass(frozen=True, slots=True)
class PortfolioEvent:
    timestamp: datetime
    action: str
    reference: str
    status: str
    position_id: str | None = None
    pnl: float | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduledOptionAction:
    """A deterministic event consumed by ``run_scheduled_backtest``.

    ``reference`` is a user-defined alias. Open actions create the alias;
    close/expire/assignment_check actions resolve it to the active position.
    """

    timestamp: datetime
    action: str
    reference: str
    strategy: str | None = None
    legs: tuple[OptionLeg, ...] = ()
    spot: float | None = None
    ex_dividend_amount: float = 0.0
    annual_risk_free_rate: float = 0.0
    underlying_shares: int = 0
    cash_available: float | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        timestamp = _utc(self.timestamp)
        if not isinstance(self.action, str):
            raise ValueError("action must be a string")
        if not isinstance(self.reference, str):
            raise ValueError("reference must be a string")
        if self.strategy is not None and not isinstance(self.strategy, str):
            raise ValueError("strategy must be a string when supplied")
        action = self.action.lower().strip()
        reference = self.reference.strip()
        strategy = None if self.strategy is None else self.strategy.strip()
        if action not in {"open", "close", "mark", "expire", "assignment_check"}:
            raise ValueError(f"unsupported scheduled action {self.action!r}")
        if not reference:
            raise ValueError("reference is required")

        spot = None
        if self.spot is not None:
            spot = _finite_number(self.spot, field_name="spot")
            if spot < 0:
                raise ValueError("spot cannot be negative")
        ex_dividend_amount = _finite_number(
            self.ex_dividend_amount,
            field_name="ex_dividend_amount",
        )
        annual_risk_free_rate = _finite_number(
            self.annual_risk_free_rate,
            field_name="annual_risk_free_rate",
        )
        if ex_dividend_amount < 0:
            raise ValueError("ex_dividend_amount cannot be negative")
        if not isinstance(self.underlying_shares, int) or isinstance(
            self.underlying_shares, bool
        ):
            raise ValueError("underlying_shares must be an integer")
        if self.underlying_shares < 0:
            raise ValueError("underlying_shares cannot be negative")
        cash_available = None
        if self.cash_available is not None:
            cash_available = _finite_number(
                self.cash_available,
                field_name="cash_available",
            )
            if cash_available < 0:
                raise ValueError("cash_available cannot be negative")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")

        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "strategy", strategy)
        object.__setattr__(self, "spot", spot)
        object.__setattr__(self, "ex_dividend_amount", ex_dividend_amount)
        object.__setattr__(self, "annual_risk_free_rate", annual_risk_free_rate)
        object.__setattr__(self, "cash_available", cash_available)
        object.__setattr__(self, "legs", tuple(self.legs))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class ScheduledBacktestResult:
    config: PortfolioConfig
    events: tuple[PortfolioEvent, ...]
    snapshots: tuple[PortfolioSnapshot, ...]
    ending_snapshot: PortfolioSnapshot
    total_return: float
    max_drawdown: float
    closed_positions: tuple[ClosedPosition, ...]
    assignment_risks: tuple[AssignmentEvent, ...]


class OptionsPortfolio:
    """Stateful option portfolio with conservative collateral accounting."""

    def __init__(
        self,
        engine: PointInTimeOptionsEngine,
        *,
        config: PortfolioConfig | None = None,
    ):
        self.engine = engine
        self.config = config or PortfolioConfig()
        self._positions: dict[str, OptionPosition] = {}
        self._realized_pnl = 0.0
        self._closed: list[ClosedPosition] = []
        self._assignment_risks: list[AssignmentEvent] = []

    @property
    def open_positions(self) -> tuple[OptionPosition, ...]:
        return tuple(self._positions.values())

    @property
    def closed_positions(self) -> tuple[ClosedPosition, ...]:
        return tuple(self._closed)

    @property
    def assignment_risks(self) -> tuple[AssignmentEvent, ...]:
        return tuple(self._assignment_risks)

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def collateral_used(self) -> float:
        return sum(position.collateral_required for position in self._positions.values())

    def get_position(self, position_id: str) -> OptionPosition:
        try:
            return self._positions[position_id]
        except KeyError as exc:
            raise PositionNotFoundError(f"open position not found: {position_id}") from exc

    def mark(self, timestamp: datetime) -> PortfolioSnapshot:
        """Mark open positions at executable liquidation bid/ask prices."""
        timestamp = _utc(timestamp)
        unrealized = 0.0
        for position in self._positions.values():
            marked = self.engine.close_position(position, timestamp)
            unrealized += marked.pnl

        equity = self.config.starting_cash + self._realized_pnl + unrealized
        collateral = self.collateral_used
        return PortfolioSnapshot(
            timestamp=timestamp,
            equity=equity,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=unrealized,
            collateral_used=collateral,
            free_buying_power=equity - collateral,
            open_positions=len(self._positions),
        )

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
        timestamp = _utc(timestamp)
        if len(self._positions) >= self.config.max_open_positions:
            raise InsufficientBuyingPowerError(
                f"max_open_positions={self.config.max_open_positions} reached"
            )

        before = self.mark(timestamp)
        candidate = self.engine.open_position(
            strategy,
            timestamp,
            legs,
            underlying_shares=underlying_shares,
            cash_available=cash_available,
            metadata=metadata,
        )
        collateral_cap = before.equity * self.config.max_collateral_fraction
        projected_collateral = self.collateral_used + candidate.collateral_required
        conservative_requirement = projected_collateral + candidate.entry_execution.fees
        if conservative_requirement > collateral_cap:
            raise InsufficientBuyingPowerError(
                f"position requires ${candidate.collateral_required:.2f} collateral "
                f"plus ${candidate.entry_execution.fees:.2f} entry fees; "
                f"projected requirement ${conservative_requirement:.2f} exceeds "
                f"portfolio cap ${collateral_cap:.2f}"
            )

        self._positions[candidate.position_id] = candidate
        return candidate

    def close_position(
        self,
        position_id: str,
        timestamp: datetime,
    ) -> ClosedPosition:
        timestamp = _utc(timestamp)
        position = self.get_position(position_id)
        closed = self.engine.close_position(position, timestamp)
        self._realized_pnl += closed.pnl
        self._closed.append(closed)
        del self._positions[position_id]
        return closed

    def expire_position(
        self,
        position_id: str,
        timestamp: datetime,
        *,
        spot: float,
    ) -> float:
        timestamp = _utc(timestamp)
        position = self.get_position(position_id)
        settlement = self.engine.settle_expiration(
            position,
            timestamp=timestamp,
            spot=spot,
        )
        self._realized_pnl += settlement.pnl
        del self._positions[position_id]
        return settlement.pnl

    def check_assignment_risk(
        self,
        position_id: str,
        timestamp: datetime,
        *,
        spot: float,
        ex_dividend_amount: float = 0.0,
        annual_risk_free_rate: float = 0.0,
        require_resolution: bool = True,
    ) -> tuple[AssignmentEvent, ...]:
        timestamp = _utc(timestamp)
        position = self.get_position(position_id)
        events = self.engine.early_assignment_risk(
            position,
            timestamp=timestamp,
            spot=spot,
            ex_dividend_amount=ex_dividend_amount,
            annual_risk_free_rate=annual_risk_free_rate,
        )
        self._assignment_risks.extend(events)
        if events and require_resolution:
            symbols = ", ".join(event.contract.symbol for event in events)
            raise UnresolvedAssignmentError(
                "physical assignment transition is required for "
                f"{symbols}; close the position or add a stock-inventory ledger"
            )
        return events


def _max_drawdown(snapshots: Sequence[PortfolioSnapshot]) -> float:
    if not snapshots:
        return 0.0
    peak = snapshots[0].equity
    max_dd = 0.0
    for snapshot in snapshots:
        peak = max(peak, snapshot.equity)
        if peak > 0:
            max_dd = min(max_dd, (snapshot.equity - peak) / peak)
    return max_dd


def run_scheduled_backtest(
    engine: PointInTimeOptionsEngine,
    actions: Sequence[ScheduledOptionAction],
    *,
    config: PortfolioConfig | None = None,
    continue_on_rejection: bool = False,
    require_assignment_resolution: bool = True,
) -> ScheduledBacktestResult:
    """Run deterministic actions in chronological order.

    Events are sorted by timestamp while preserving input order for ties.
    Rejected actions either raise immediately or are recorded and skipped.
    """
    if not actions:
        raise ValueError("at least one scheduled action is required")

    portfolio = OptionsPortfolio(engine, config=config)
    ordered = sorted(enumerate(actions), key=lambda item: (item[1].timestamp, item[0]))
    aliases: dict[str, str] = {}
    events: list[PortfolioEvent] = []
    snapshots: list[PortfolioSnapshot] = []

    for _, action in ordered:
        try:
            if action.action == "open":
                if action.reference in aliases:
                    raise DuplicatePositionReferenceError(
                        f"active reference already exists: {action.reference}"
                    )
                if not action.strategy or not action.legs:
                    raise InvalidScheduledActionError(
                        "open action requires strategy and at least one leg"
                    )
                position = portfolio.open_position(
                    action.strategy,
                    action.timestamp,
                    action.legs,
                    underlying_shares=action.underlying_shares,
                    cash_available=action.cash_available,
                    metadata=action.metadata,
                )
                aliases[action.reference] = position.position_id
                events.append(
                    PortfolioEvent(
                        timestamp=action.timestamp,
                        action="open",
                        reference=action.reference,
                        status="filled",
                        position_id=position.position_id,
                        detail=(
                            f"collateral=${position.collateral_required:.2f}; "
                            f"entry_fees=${position.entry_execution.fees:.2f}"
                        ),
                    )
                )

            elif action.action == "mark":
                events.append(
                    PortfolioEvent(
                        timestamp=action.timestamp,
                        action="mark",
                        reference=action.reference,
                        status="recorded",
                    )
                )

            else:
                position_id = aliases.get(action.reference)
                if not position_id:
                    raise PositionNotFoundError(
                        f"active reference not found: {action.reference}"
                    )

                if action.action == "close":
                    closed = portfolio.close_position(position_id, action.timestamp)
                    del aliases[action.reference]
                    events.append(
                        PortfolioEvent(
                            timestamp=action.timestamp,
                            action="close",
                            reference=action.reference,
                            status="filled",
                            position_id=position_id,
                            pnl=closed.pnl,
                        )
                    )

                elif action.action == "expire":
                    if action.spot is None:
                        raise InvalidScheduledActionError(
                            "expire action requires underlying spot"
                        )
                    pnl = portfolio.expire_position(
                        position_id,
                        action.timestamp,
                        spot=action.spot,
                    )
                    del aliases[action.reference]
                    events.append(
                        PortfolioEvent(
                            timestamp=action.timestamp,
                            action="expire",
                            reference=action.reference,
                            status="settled",
                            position_id=position_id,
                            pnl=pnl,
                        )
                    )

                elif action.action == "assignment_check":
                    if action.spot is None:
                        raise InvalidScheduledActionError(
                            "assignment_check requires underlying spot"
                        )
                    risks = portfolio.check_assignment_risk(
                        position_id,
                        action.timestamp,
                        spot=action.spot,
                        ex_dividend_amount=action.ex_dividend_amount,
                        annual_risk_free_rate=action.annual_risk_free_rate,
                        require_resolution=require_assignment_resolution,
                    )
                    events.append(
                        PortfolioEvent(
                            timestamp=action.timestamp,
                            action="assignment_check",
                            reference=action.reference,
                            status="risk_detected" if risks else "clear",
                            position_id=position_id,
                            detail=(
                                f"{len(risks)} deterministic assignment risk(s)"
                                if risks
                                else None
                            ),
                        )
                    )

            snapshots.append(portfolio.mark(action.timestamp))

        except (
            OptionsBacktestError,
            PortfolioBacktestError,
            QuoteUnavailableError,
            ValueError,
        ) as exc:
            if not continue_on_rejection:
                raise
            events.append(
                PortfolioEvent(
                    timestamp=action.timestamp,
                    action=action.action,
                    reference=action.reference,
                    status="rejected",
                    detail=str(exc),
                )
            )
            # A rejection snapshot may itself be impossible if the quote used to
            # mark an existing position is stale. Preserve the original rejection.
            try:
                snapshots.append(portfolio.mark(action.timestamp))
            except OptionsBacktestError:
                pass

    ending_timestamp = ordered[-1][1].timestamp
    ending = portfolio.mark(ending_timestamp)
    if not snapshots or snapshots[-1] != ending:
        snapshots.append(ending)

    total_return = (ending.equity / portfolio.config.starting_cash) - 1.0
    return ScheduledBacktestResult(
        config=portfolio.config,
        events=tuple(events),
        snapshots=tuple(snapshots),
        ending_snapshot=ending,
        total_return=total_return,
        max_drawdown=_max_drawdown(snapshots),
        closed_positions=portfolio.closed_positions,
        assignment_risks=portfolio.assignment_risks,
    )
