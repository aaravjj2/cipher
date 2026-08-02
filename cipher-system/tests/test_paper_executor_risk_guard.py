from datetime import datetime, timezone

from core.paper_executor.config import ExecutorConfig
from core.paper_executor.models import Direction, Mode, PaperPosition, SkipReason
from core.paper_executor.risk_guard import RiskGuard


def test_risk_guard_blocks_duplicate_ticker_position():
    cfg = ExecutorConfig()
    guard = RiskGuard(cfg)
    open_positions = [PaperPosition("p1", "AAPL", Direction.BULLISH, "AAPL260730C00100000", 1, 1, datetime.now(timezone.utc), 102, 99)]
    assert guard.entry_skip(mode=Mode.PAPER, kill_switch=False, open_positions=open_positions, ticker="AAPL", new_positions_today=0, stopped_today=0) == SkipReason.SKIPPED_POSITION_EXISTS
