from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class Mode(StrEnum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    PAPER = "paper"


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"


class SkipReason(StrEnum):
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    SKIPPED_SETUP_DISABLED = "SKIPPED_SETUP_DISABLED"
    SKIPPED_TICKER_DISABLED = "SKIPPED_TICKER_DISABLED"
    SKIPPED_ENTRY_WINDOW = "SKIPPED_ENTRY_WINDOW"
    SKIPPED_STALE_SIGNAL = "SKIPPED_STALE_SIGNAL"
    SKIPPED_INVALID_GEOMETRY = "SKIPPED_INVALID_GEOMETRY"
    SKIPPED_MISSING_LEVEL = "SKIPPED_MISSING_LEVEL"
    SKIPPED_NO_CONTRACT = "SKIPPED_NO_CONTRACT"
    SKIPPED_WIDE_SPREAD = "SKIPPED_WIDE_SPREAD"
    SKIPPED_STALE_QUOTE = "SKIPPED_STALE_QUOTE"
    SKIPPED_MAX_COST = "SKIPPED_MAX_COST"
    SKIPPED_MAX_POSITIONS = "SKIPPED_MAX_POSITIONS"
    SKIPPED_POSITION_EXISTS = "SKIPPED_POSITION_EXISTS"
    SKIPPED_DAILY_LIMIT = "SKIPPED_DAILY_LIMIT"
    SKIPPED_DAILY_STOP_LIMIT = "SKIPPED_DAILY_STOP_LIMIT"
    SKIPPED_KILL_SWITCH = "SKIPPED_KILL_SWITCH"
    SKIPPED_MODE_DISABLED = "SKIPPED_MODE_DISABLED"
    SKIPPED_SYNTHETIC = "SKIPPED_SYNTHETIC"
    SKIPPED_DATA_FEED_DEGRADED = "SKIPPED_DATA_FEED_DEGRADED"


class Lifecycle(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    ELIGIBLE = "ELIGIBLE"
    CONTRACT_SELECTED = "CONTRACT_SELECTED"
    SHADOW_TRACKING = "SHADOW_TRACKING"
    PAPER_FILLED = "PAPER_FILLED"
    OPEN = "OPEN"
    EXIT_TRIGGERED = "EXIT_TRIGGERED"
    CLOSED = "CLOSED"
    ERROR = "ERROR"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True)
class SignalCard:
    ticker: str
    scanner_type: str
    direction: Direction
    setup: str
    captured_at: datetime
    spot: float
    target: float | None
    invalidation: float | None
    raw: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    rank: int | None = None

    @property
    def option_type(self) -> OptionType:
        return OptionType.CALL if self.direction == Direction.BULLISH else OptionType.PUT

    @property
    def episode_key(self) -> str:
        return "|".join([self.scanner_type, self.ticker, self.direction.value, self.setup])


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    timestamp: datetime
    last: float | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    volume: int | None = None
    open_interest: int | None = None

    @property
    def midpoint(self) -> float:
        return round((self.bid + self.ask) / 2.0, 4)

    @property
    def spread(self) -> float:
        return round(self.ask - self.bid, 4)

    @property
    def spread_pct(self) -> float:
        midpoint = self.midpoint
        return round(self.spread / midpoint * 100.0, 4) if midpoint > 0 else 999999.0


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    ticker: str
    expiration: str
    strike: float
    option_type: OptionType
    active: bool = True


@dataclass(frozen=True)
class ContractCandidate:
    contract: OptionContract
    quote: Quote | None
    dte: int
    rejection_reasons: tuple[str, ...]
    ranking_score: float

    @property
    def accepted(self) -> bool:
        return not self.rejection_reasons


@dataclass(frozen=True)
class SpreadCandidate:
    long_leg: ContractCandidate
    short_leg: ContractCandidate
    width: float
    entry_debit: float
    max_profit: float
    rejection_reasons: tuple[str, ...]
    ranking_score: float

    @property
    def accepted(self) -> bool:
        return not self.rejection_reasons

    @property
    def symbol(self) -> str:
        return f"{self.long_leg.contract.symbol}/{self.short_leg.contract.symbol}"


@dataclass(frozen=True)
class SimulatedFill:
    side: str
    bid: float
    ask: float
    midpoint: float
    slippage: float
    fill_price: float
    quote_timestamp: datetime
    quantity: int
    partial: bool = False


@dataclass
class PaperPosition:
    id: str
    ticker: str
    direction: Direction
    contract_symbol: str
    quantity: int
    entry_price: float
    opened_at: datetime
    target: float
    invalidation: float
    status: str = "OPEN"
    peak_pnl_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(stable_json(value).encode('utf-8')).hexdigest()[:32]}"
