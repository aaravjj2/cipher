from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from .config import ExecutorConfig
from .models import SignalCard, SkipReason


def setup_allowed(card: SignalCard, cfg: ExecutorConfig) -> bool:
    if cfg.strategy.allowed_patterns:
        return any(
            card.scanner_type == pattern.get("scanner_type")
            and card.setup == pattern.get("setup")
            and card.direction.value == pattern.get("direction")
            for pattern in cfg.strategy.allowed_patterns
        )
    return card.setup in cfg.strategy.allowed_setups.get(card.scanner_type, ())


def ticker_allowed(card: SignalCard, cfg: ExecutorConfig) -> bool:
    allowed = {ticker.upper() for ticker in cfg.strategy.allowed_tickers}
    return not allowed or card.ticker.upper() in allowed


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def is_us_dst(dt_utc: datetime) -> bool:
    year = dt_utc.year
    march = datetime(year, 3, 8, 7, tzinfo=timezone.utc)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7)
    november = datetime(year, 11, 1, 6, tzinfo=timezone.utc)
    dst_end = november + timedelta(days=(6 - november.weekday()) % 7)
    return dst_start <= dt_utc < dst_end


def to_et_time(dt: datetime) -> time:
    dt_utc = dt.astimezone(timezone.utc)
    offset = timedelta(hours=-4 if is_us_dst(dt_utc) else -5)
    return (dt_utc + offset).time().replace(second=0, microsecond=0)


def entry_window_allowed(card: SignalCard, cfg: ExecutorConfig) -> bool:
    if not cfg.strategy.entry_window_et_start or not cfg.strategy.entry_window_et_end:
        return True
    start = parse_hhmm(cfg.strategy.entry_window_et_start)
    end = parse_hhmm(cfg.strategy.entry_window_et_end)
    current = to_et_time(card.captured_at)
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def eligibility_skip(card: SignalCard, cfg: ExecutorConfig, kill_active: bool, feed_degraded: bool) -> SkipReason | None:
    if kill_active:
        return SkipReason.SKIPPED_KILL_SWITCH
    if feed_degraded:
        return SkipReason.SKIPPED_DATA_FEED_DEGRADED
    if not setup_allowed(card, cfg):
        return SkipReason.SKIPPED_SETUP_DISABLED
    if not ticker_allowed(card, cfg):
        return SkipReason.SKIPPED_TICKER_DISABLED
    if not entry_window_allowed(card, cfg):
        return SkipReason.SKIPPED_ENTRY_WINDOW
    return None
