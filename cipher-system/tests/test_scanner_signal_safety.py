from __future__ import annotations

import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import scanner  # noqa: E402
from scanner import _flash_components, _research_quality, _signal_geometry  # noqa: E402


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


def test_flash_scan_preserves_an_explicit_rate_limited_universe(monkeypatch) -> None:
    visited: list[str] = []

    def fake_analyze(_matrix, ticker, *_args, **_kwargs):
        visited.append(ticker)
        return {
            "ticker": ticker,
            "score": 60.0,
            "abs_score": 60.0,
            "supports": [99.0],
            "geometry_valid": True,
            "actionable": True,
        }

    monkeypatch.setattr(scanner, "analyze_ticker", fake_analyze)
    result = scanner.run_scan(
        lambda *_args, **_kwargs: {},
        strategy="flash",
        universe=["NVDA", "AAPL"],
        workers=1,
        save_history=False,
    )

    assert visited == ["NVDA", "AAPL"]
    assert result["universe_size"] == 2


def test_quality_gate_keeps_score_separate_from_evidence_confidence() -> None:
    rich = {
        "spot": 100, "direction": "BULLISH", "score": 83, "actionable": True,
        "geometry_valid": True, "coverage_cells": 30, "contracts": 200, "feed": "opra",
    }
    sparse = {**rich, "coverage_cells": None, "contracts": None}
    assert _research_quality(rich)["confidence"] == "higher"
    quality = _research_quality(sparse)
    assert quality["rank_eligible"] is False
    assert quality["confidence"] == "insufficient"
    assert "options_coverage_unknown" in quality["quality_reasons"]


def test_quality_gate_uses_shared_evidence_coverage_when_present() -> None:
    item = {
        "spot": 100, "direction": "BULLISH", "score": 83, "actionable": True,
        "geometry_valid": True, "coverage_cells": 99, "contracts": 999, "feed": "opra",
        "evidence_snapshot": {
            "feed": "opra",
            "coverage": {"status": "limited", "calculated_cells": 4, "contracts": 10},
            "missing_reasons": ["options_coverage_thin"],
        },
    }
    quality = _research_quality(item)
    assert quality["coverage_status"] == "limited"
    assert quality["rank_eligible"] is False
    assert quality["quality_reasons"] == ["options_coverage_thin"]


def test_run_scan_reports_rejection_funnel(monkeypatch) -> None:
    def fake_analyze(_matrix, ticker, *_args, **_kwargs):
        base = {
            "ticker": ticker, "spot": 100.0, "score": 90.0, "direction": "BULLISH",
            "supports": [99.0], "geometry_valid": True, "actionable": True,
            "coverage_cells": 20, "contracts": 80, "feed": "opra",
        }
        if ticker == "THIN":
            base["contracts"] = 2
        return base

    monkeypatch.setattr(scanner, "analyze_ticker", fake_analyze)
    result = scanner.run_scan(
        lambda *_args, **_kwargs: {}, universe=["GOOD", "THIN"],
        cache_seconds=0, save_history=False,
    )
    assert [row["ticker"] for row in result["top"]] == ["GOOD"]
    assert result["rejected"] == 1
    assert result["rejection_counts"]["options_coverage_thin"] == 1
    assert result["rejected_examples"][0]["ticker"] == "THIN"
