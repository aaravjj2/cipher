from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd


from conftest import load_artifact, require_artifact

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_frame(start: str, periods: int = 5) -> pd.DataFrame:
    timestamps = pd.bdate_range(start, periods=periods, tz="UTC")
    rows = []
    for timestamp in timestamps:
        for ticker in ("SPY", "QQQ", "IWM"):
            rows.append(
                {
                    "timestamp": timestamp,
                    "ticker": ticker,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1_000.0,
                    "vwap": 100.2,
                    "trades": 100,
                }
            )
    return pd.DataFrame(rows)


def test_broad_panel_locked_slice_uses_observed_provider_start():
    module = load_script("download_register_broad_equity_panel")
    locked = module.SLICES[0]
    assert locked["name"] == "alpaca_broad_daily_2016_2019_locked_validation_v1"
    assert locked["start"] == "2016-01-04"
    assert locked["end"] == "2019-12-31"
    assert locked["research_role"] == "locked_temporal_validation_fixed_pre_download_candidates_only"


def test_broad_panel_validation_rejects_material_start_truncation():
    module = load_script("download_register_broad_equity_panel")
    quality = module.validate_slice(sample_frame("2016-01-04"), "2010-01-01", "2016-01-08")
    assert quality["passed"] is False
    assert "observed_start_outside_tolerance" in quality["failures"]


def test_cross_period_matrix_uses_candidate_identity_and_no_execution_authority():
    module = load_script("build_cross_period_strategy_matrix")
    assert module.DATASETS == {
        "locked_2016_2019": "ds_fb1e8d9aeb51f12407b08123",
        "phase3_2020_2022": "ds_532bf7c42462c24a7c1a0a1f",
        "original_2023_2025": "ds_380c76da95f0c3787529c6b8",
        "locked_2026_ytd": "ds_f20f2e15e7d1041ce6a1858d",
    }
    artifact = require_artifact("data/governance/cross_period_strategy_matrix.json", non_empty_key="matrix")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["identity_key"] == "candidate_id"
    assert payload["summary"]["tested_all_three"] >= 1
    assert payload["summary"]["tested_all_four"] >= 1
    assert payload["summary"]["passed_at_least_two"] >= 1
    assert payload["summary"]["passed_all_four"] == 0
    assert payload["automatic_promotion"] is False
    assert payload["execution_authority"] is False
    assert all(row["candidate_id"] for row in payload["matrix"])


def test_2026_ytd_validation_and_stress_artifacts_are_guarded():
    validation = load_artifact("data/governance/strategy_research_2026_ytd/latest_2026_ytd_locked_validation.json")
    assert validation["dataset"]["dataset_id"] == "ds_f20f2e15e7d1041ce6a1858d"
    assert validation["dataset"]["evaluation_start"] == "2026-01-02"
    assert validation["dataset"]["evaluation_end"] == "2026-08-04"
    assert validation["summary"] == {
        "candidates": 194,
        "passes": 5,
        "conditional_passes": 0,
        "failures": 189,
        "errors": 0,
    }
    assert validation["adaptive_feedback_allowed"] is False
    assert validation["automatic_promotion"] is False
    assert validation["execution_authority"] is False
    assert all(
        row.get("quality_checks", {}).get("evaluation_window_enforced") is True
        for row in validation["results"]
        if row.get("verdict") != "ERROR"
    )

    robustness = load_artifact("data/governance/strategy_research_2026_ytd/latest_2026_ytd_robustness.json")
    assert robustness["candidate_count"] == 6
    assert robustness["robust_candidate_count"] == 1
    assert robustness["automatic_promotion"] is False
    assert robustness["execution_authority"] is False

    annual = load_artifact("data/governance/annual_regime_stability.json")
    assert annual["candidate_count"] == 15
    assert annual["stable_candidate_count"] == 0
    assert annual["automatic_promotion"] is False
    assert annual["execution_authority"] is False


def test_locked_validation_is_complete_and_cannot_feed_adaptation():
    artifact = require_artifact("data/governance/strategy_research_validation/latest_locked_broad_validation.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["dataset"]["dataset_id"] == "ds_fb1e8d9aeb51f12407b08123"
    assert payload["candidate_family_freeze"]["count"] == 92
    assert payload["summary"]["candidates"] == 92
    assert payload["summary"]["errors"] == 0
    assert payload["summary"]["passes"] == 3
    assert payload["adaptive_feedback_allowed"] is False
    assert payload["automatic_promotion"] is False
    assert payload["execution_authority"] is False
