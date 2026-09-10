from datetime import datetime, timezone
from pathlib import Path

from core.paper_executor.config import ExecutorConfig, StrategyConfig, load_config
from core.paper_executor.models import Direction, SignalCard, SkipReason
from core.paper_executor.policy import eligibility_skip, entry_window_allowed, setup_allowed, ticker_allowed


def _card(direction: Direction, setup: str, scanner_type: str = "flash_agentic") -> SignalCard:
    return SignalCard("NVDA", scanner_type, direction, setup, datetime(2026, 7, 28, 14, 30, tzinfo=timezone.utc), 100, 101, 99, {})


def test_shadow_config_comma_setups_not_truncated():
    """Regression: YAML flow mappings ({...}) split values at commas, so setups like
    'triple cluster (3 peaks, above)' parsed as 'triple cluster (3 peaks' and every
    card was rejected SKIPPED_SETUP_DISABLED. The shadow config must load the full
    setup strings and accept the matching real-world cards."""
    path = Path(__file__).resolve().parents[1] / "config" / "paper_autopilot_shadow.yaml"
    cfg = load_config(path)
    assert cfg.scanner.accepted_types == ("cipher",)
    assert "cipher" in cfg.scanner.accepted_types
    loaded_setups = {pattern["setup"] for pattern in cfg.strategy.allowed_patterns}
    for comma_setup in (
        "triple cluster (3 peaks, above)",
        "triple cluster (3 peaks, below)",
        "quad cluster (4 peaks, above)",
        "quad cluster (4 peaks, below)",
    ):
        assert comma_setup in loaded_setups, comma_setup
    for direction, setup in (
        (Direction.BULLISH, "triple cluster (3 peaks, above)"),
        (Direction.BEARISH, "triple cluster (3 peaks, below)"),
        (Direction.BULLISH, "quad cluster (4 peaks, above)"),
        (Direction.BEARISH, "golden / top-pull"),
    ):
        assert setup_allowed(_card(direction, setup, "cipher"), cfg)


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


def test_premarket_entries_allowed_only_for_cipher_before_window():
    """Autopilot premarket-entry mode: cipher cards may enter only during
    premarket hours and only when the flag is on; the window close still binds
    and no other scanner type is affected."""
    cfg = ExecutorConfig(strategy=StrategyConfig(
        entry_window_et_start="09:35", entry_window_et_end="11:30",
        allow_premarket_entries=True,
    ))
    premarket = datetime(2026, 7, 28, 12, 45, tzinfo=timezone.utc)  # 08:45 ET
    assert entry_window_allowed(
        SignalCard("NVDA", "cipher", Direction.BULLISH, "cipher model", premarket, 100, 101, 99, {}), cfg)
    # Flash cards are not affected by the autopilot knob.
    assert not entry_window_allowed(
        SignalCard("NVDA", "flash", Direction.BULLISH, "floor bounce", premarket, 100, 101, 99, {}), cfg)
    # The window close still binds for cipher cards.
    afternoon = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)  # 12:00 ET
    assert not entry_window_allowed(
        SignalCard("NVDA", "cipher", Direction.BULLISH, "cipher model", afternoon, 100, 101, 99, {}), cfg)
    # Default config (flag off) keeps premarket blocked.
    off = ExecutorConfig(strategy=StrategyConfig(
        entry_window_et_start="09:35", entry_window_et_end="11:30"))
    assert not entry_window_allowed(
        SignalCard("NVDA", "cipher", Direction.BULLISH, "cipher model", premarket, 100, 101, 99, {}), off)


def test_shadow_config_defers_cipher_card_until_options_market_opens():
    """The shipped config must not send option orders before options trade."""
    path = Path(__file__).resolve().parents[1] / "config" / "paper_autopilot_shadow.yaml"
    cfg = load_config(path)
    assert cfg.strategy.allow_premarket_entries is False
    premarket = datetime(2026, 7, 28, 12, 45, tzinfo=timezone.utc)  # 08:45 ET
    cipher = SignalCard("NVDA", "cipher", Direction.BULLISH, "cipher model", premarket, 100, 101, 99, {})
    assert eligibility_skip(cipher, cfg, False, False) is SkipReason.SKIPPED_ENTRY_WINDOW


def test_shipped_policy_handles_weekend_expiration_gap_and_blocks_holidays():
    path = Path(__file__).resolve().parents[1] / "config" / "paper_autopilot_shadow.yaml"
    cfg = load_config(path)
    assert cfg.contract.maximum_dte == 7
    labor_day = datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc)
    card = SignalCard("SPY", "cipher", Direction.BULLISH, "cipher model", labor_day, 100, 101, 99, {})
    assert eligibility_skip(card, cfg, False, False) is SkipReason.SKIPPED_ENTRY_WINDOW
