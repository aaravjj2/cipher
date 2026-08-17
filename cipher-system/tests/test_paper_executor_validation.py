from datetime import datetime, timezone

from core.paper_executor.config import ExecutorConfig
from core.paper_executor.validation import validate_card


def test_valid_card_accepts_directional_geometry():
    now = datetime.now(timezone.utc)
    card, reasons = validate_card({
        "ticker": "AAPL",
        "scanner_type": "flash_agentic",
        "direction": "bullish",
        "setup": "ceiling rejection",
        "captured_timestamp": now.isoformat(),
        "spot": 100,
        "target": 102,
        "invalidation": 99,
    }, ExecutorConfig(), now)
    assert card is not None
    assert reasons == []


def test_missing_levels_are_auditable_skip():
    now = datetime.now(timezone.utc)
    card, reasons = validate_card({
        "ticker": "AAPL",
        "scanner_type": "flash",
        "direction": "bearish",
        "setup": "ceiling rejection",
        "captured_timestamp": now.isoformat(),
        "spot": 100,
    }, ExecutorConfig(), now)
    assert card is None
    assert "SKIPPED_MISSING_LEVEL" in reasons


def test_scanner_setup_rank_suffix_is_normalized():
    now = datetime.now(timezone.utc)
    card, reasons = validate_card({
        "ticker": "GOOGL",
        "scanner_type": "flash",
        "direction": "bullish",
        "setup": "FLOOR BOUNCE#9",
        "captured_timestamp": now.isoformat(),
        "spot": 100,
        "target": 102,
        "invalidation": 99,
    }, ExecutorConfig(), now)
    assert card is not None
    assert reasons == []
    assert card.setup == "floor bounce"


def test_explicit_non_actionable_scanner_card_is_rejected():
    now = datetime.now(timezone.utc)
    card, reasons = validate_card({
        "ticker": "NVDA",
        "scanner_type": "flash",
        "direction": "bullish",
        "setup": "triple cluster (3 peaks, above)",
        "captured_timestamp": now.isoformat(),
        "spot": 100,
        "target": 102,
        "invalidation": 99,
        "geometry_valid": True,
        "actionable": False,
    }, ExecutorConfig(), now)
    assert card is None
    assert reasons == ["SKIPPED_NOT_ACTIONABLE"]
