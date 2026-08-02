from datetime import datetime, timedelta, timezone

from core.paper_executor.config import ExitConfig
from core.paper_executor.models import Direction, PaperPosition, Quote
from core.paper_executor.position_manager import exit_reason


def pos(opened_at):
    return PaperPosition("p1", "AAPL", Direction.BULLISH, "AAPL260730C00100000", 1, 1.0, opened_at, 102, 99)


def test_exit_priority_invalidation_before_profit():
    now = datetime.now(timezone.utc)
    assert exit_reason(pos(now), Quote("x", 1.5, 1.6, now), 98.9, ExitConfig(), now) == "underlying_invalidation"


def test_max_hold_exit():
    now = datetime.now(timezone.utc)
    assert exit_reason(pos(now - timedelta(minutes=46)), Quote("x", 1.0, 1.1, now), 100, ExitConfig(), now) == "maximum_holding_time"
