from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .config import ExitConfig
from .models import Direction, PaperPosition, Quote
from .exchange_calendar import closing_reason


EXIT_PRIORITY = (
    "underlying_invalidation",
    "option_stop_loss",
    "option_take_profit",
    "underlying_target",
    "maximum_holding_time",
    "force_close_time",
    "system_shutdown_recovery",
)


def pnl_pct(position: PaperPosition, exit_bid: float) -> float:
    return (exit_bid - position.entry_price) / position.entry_price * 100.0


def exit_reason(position: PaperPosition, option_quote: Quote, underlying_price: float | None, cfg: ExitConfig, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    current_pnl = pnl_pct(position, option_quote.bid)
    if cfg.exit_on_underlying_invalidation and underlying_price is not None:
        if position.direction == Direction.BULLISH and underlying_price <= position.invalidation:
            return "underlying_invalidation"
        if position.direction == Direction.BEARISH and underlying_price >= position.invalidation:
            return "underlying_invalidation"
    if current_pnl <= -abs(cfg.stop_loss_pct):
        return "option_stop_loss"
    if current_pnl >= abs(cfg.take_profit_pct):
        return "option_take_profit"
    if cfg.exit_on_underlying_target and underlying_price is not None:
        if position.direction == Direction.BULLISH and underlying_price >= position.target:
            return "underlying_target"
        if position.direction == Direction.BEARISH and underlying_price <= position.target:
            return "underlying_target"
    if (now - position.opened_at).total_seconds() >= cfg.maximum_hold_minutes * 60:
        return "maximum_holding_time"
    return closing_reason(position.opened_at, now, cfg.force_close_time_et, cfg.allow_overnight)
