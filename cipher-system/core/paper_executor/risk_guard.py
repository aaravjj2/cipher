from __future__ import annotations

from .config import ExecutorConfig
from .models import Mode, PaperPosition, SkipReason


class RiskGuard:
    def __init__(self, cfg: ExecutorConfig):
        self.cfg = cfg

    def entry_skip(self, *, mode: Mode, kill_switch: bool, open_positions: list[PaperPosition], ticker: str, new_positions_today: int, stopped_today: int) -> SkipReason | None:
        if mode == Mode.DISABLED:
            return SkipReason.SKIPPED_MODE_DISABLED
        if kill_switch:
            return SkipReason.SKIPPED_KILL_SWITCH
        if len(open_positions) >= self.cfg.portfolio.maximum_open_positions:
            return SkipReason.SKIPPED_MAX_POSITIONS
        if sum(1 for p in open_positions if p.ticker == ticker and p.status == "OPEN") >= self.cfg.portfolio.maximum_positions_per_ticker:
            return SkipReason.SKIPPED_POSITION_EXISTS
        if new_positions_today >= self.cfg.portfolio.maximum_new_positions_per_day:
            return SkipReason.SKIPPED_DAILY_LIMIT
        if stopped_today >= self.cfg.portfolio.stop_after_daily_losses:
            return SkipReason.SKIPPED_DAILY_STOP_LIMIT
        return None
