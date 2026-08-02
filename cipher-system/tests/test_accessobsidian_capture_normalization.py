from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from capture_accessobsidian_scans import enrich_rows  # noqa: E402


def test_flash_rows_preserve_mode_and_card_identity() -> None:
    rows = enrich_rows(
        "flash_agentic",
        "2026-07-27T13:00:00-04:00",
        [
            {
                "rank": 1,
                "state": "ARMING",
                "ticker": "NVDA",
                "bias": "BULLISH",
                "score": 96,
                "setup": "FLOOR BOUNCE",
                "spot": 210.0,
                "pivot": 210.0,
                "first_target": 212.5,
                "invalidation": 207.5,
            }
        ],
    )

    row = rows[0]
    assert row["source"] == "accessobsidian"
    assert row["scan_type"] == "flash_agentic"
    assert row["card_index"] == 0
    assert row["direction"] == "BULLISH"
    assert row["setup_family"] == "floor_bounce"
    assert row["geometry_valid"] is True
    assert row["actionable"] is True
    assert len(row["signal_signature"]) == 24


def test_cross_card_target_is_flagged_and_not_actionable() -> None:
    rows = enrich_rows(
        "flash",
        "2026-07-27T13:00:00-04:00",
        [
            {
                "ticker": "TSLA",
                "bias": "BULLISH",
                "score": 99,
                "setup": "REJECTION REVERSAL",
                "spot": 330.0,
                "pivot": 327.5,
                "first_target": 195.0,
                "invalidation": 325.0,
            }
        ],
    )

    row = rows[0]
    assert row["geometry_valid"] is False
    assert row["actionable"] is False
    assert "bullish_target_not_above_spot" in row["validation_errors"]
    assert "target_more_than_12pct_from_spot" in row["validation_errors"]


def test_cluster_strength_remains_separate_from_score() -> None:
    rows = enrich_rows(
        "cluster",
        "2026-07-27T13:00:00-04:00",
        [
            {
                "rank": 1,
                "ticker": "GD",
                "setup": "QUAD DOWNSIDE",
                "spot": 385.7,
                "cluster_target": 375.0,
                "strength": 251.0,
            }
        ],
    )

    row = rows[0]
    assert row["direction"] == "BEARISH"
    assert row["score"] is None
    assert row["strength"] == 251.0
    assert row["target"] == 375.0
    assert row["scan_type"] == "cluster"
