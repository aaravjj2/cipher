from __future__ import annotations

import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from scanner import _flash_components, _signal_geometry  # noqa: E402


def test_signal_geometry_requires_directional_levels_and_one_to_one_reward_risk() -> None:
    valid = _signal_geometry(
        100.0,
        "BULLISH",
        101.0,
        99.5,
        minimum_reward_risk=1.0,
    )
    assert valid["geometry_valid"] is True
    assert valid["actionable"] is True
    assert valid["reward_risk"] == 2.0

    weak = _signal_geometry(
        100.0,
        "BULLISH",
        100.5,
        98.0,
        minimum_reward_risk=1.0,
    )
    assert weak["geometry_valid"] is True
    assert weak["actionable"] is False
    assert "reward_risk_below_1.00" in weak["validation_errors"]

    leaked = _signal_geometry(
        330.0,
        "BULLISH",
        195.0,
        325.0,
        minimum_reward_risk=1.0,
    )
    assert leaked["geometry_valid"] is False
    assert leaked["actionable"] is False
    assert "bullish_target_not_above_spot" in leaked["validation_errors"]
    assert "target_more_than_12pct_from_spot" in leaked["validation_errors"]


def test_flash_score_is_normalized_instead_of_saturating_at_99() -> None:
    profile = [
        {"strike": 99.0, "abs": 100.0, "abs_vex": 100.0, "volume": 50.0, "oi": 1_000.0},
        {"strike": 100.0, "abs": 100.0, "abs_vex": 100.0, "volume": 500.0, "oi": 1_000.0},
        {"strike": 101.5, "abs": 100.0, "abs_vex": 100.0, "volume": 50.0, "oi": 1_000.0},
    ]
    model = {
        "first_support": 99.0,
        "put_wall": 99.0,
        "first_resistance": 101.5,
        "call_wall": 101.5,
        "pull_target": 101.5,
        "direction": "BULLISH",
        "close_under": 99.5,
        "reclaim": None,
    }

    result = _flash_components(model, 100.0, profile, 2.5)
    assert result is not None
    assert 90.0 < result["score"] < 99.0
    assert result["geometry_valid"] is True
    assert result["actionable"] is True
    assert result["reward_risk"] >= 1.0
