from datetime import datetime, timezone

from core.paper_executor.config import ExecutorConfig, StrategyConfig
from core.paper_executor.models import Direction, SignalCard, SkipReason
from core.paper_executor.policy import eligibility_skip, setup_allowed, ticker_allowed


def test_setup_allowlist_initial_scope():
    cfg = ExecutorConfig()
    card = SignalCard("NVDA", "flash", Direction.BULLISH, "floor bounce", datetime(2026, 7, 28, 14, 30, tzinfo=timezone.utc), 100, 101, 99, {})
    assert setup_allowed(card, cfg)
    assert ticker_allowed(card, cfg)
    disabled = SignalCard("AAPL", "flash_agentic", Direction.BULLISH, "ceiling rejection", datetime(2026, 7, 28, 14, 30, tzinfo=timezone.utc), 100, 101, 99, {})
    assert eligibility_skip(disabled, cfg, False, False) == SkipReason.SKIPPED_SETUP_DISABLED


def test_fronttest_ticker_filter_and_disabled_time_filter():
    cfg = ExecutorConfig()
    pre_window = datetime(2026, 7, 28, 13, 30, tzinfo=timezone.utc)
    off_ticker = SignalCard("AAPL", "flash", Direction.BULLISH, "floor bounce", pre_window, 100, 101, 99, {})
    assert eligibility_skip(off_ticker, cfg, False, False) == SkipReason.SKIPPED_TICKER_DISABLED

    off_hours = SignalCard("NVDA", "flash", Direction.BULLISH, "floor bounce", pre_window, 100, 101, 99, {})
    assert eligibility_skip(off_hours, cfg, False, False) is None


def test_entry_window_filter_when_configured():
    cfg = ExecutorConfig(strategy=StrategyConfig(entry_window_et_start="10:00", entry_window_et_end="14:00"))
    off_hours = SignalCard("NVDA", "flash", Direction.BULLISH, "floor bounce", datetime(2026, 7, 28, 13, 30, tzinfo=timezone.utc), 100, 101, 99, {})
    assert eligibility_skip(off_hours, cfg, False, False) == SkipReason.SKIPPED_ENTRY_WINDOW
