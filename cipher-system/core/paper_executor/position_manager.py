from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .config import ExitConfig
from .models import Direction, PaperPosition, Quote


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


def exit_reason(position: PaperPosition, option_quote: Quote, underlying_price: float, cfg: ExitConfig, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    current_pnl = pnl_pct(position, option_quote.bid)
    if cfg.exit_on_underlying_invalidation:
        if position.direction == Direction.BULLISH and underlying_price <= position.invalidation:
            return "underlying_invalidation"
        if position.direction == Direction.BEARISH and underlying_price >= position.invalidation:
            return "underlying_invalidation"
    if current_pnl <= -abs(cfg.stop_loss_pct):
        return "option_stop_loss"
    if current_pnl >= abs(cfg.take_profit_pct):
        return "option_take_profit"
    if cfg.exit_on_underlying_target:
        if position.direction == Direction.BULLISH and underlying_price >= position.target:
            return "underlying_target"
        if position.direction == Direction.BEARISH and underlying_price <= position.target:
            return "underlying_target"
    if (now - position.opened_at).total_seconds() >= cfg.maximum_hold_minutes * 60:
        return "maximum_holding_time"
    et = now.astimezone(ZoneInfo("America/New_York"))
    hh, mm = [int(p) for p in cfg.force_close_time_et.split(":", 1)]
    if et.time() >= time(hh, mm):
        return "force_close_time"
    return None
