from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
NOW = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    module_name = f"cipher_architecture_test_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_stack_spec_matches_original_eight_layer_topology():
    module = load_script("audit_original_architecture")
    evidence = module.stack_evidence()
    assert evidence["thesis_target_layer_count"] == 8
    assert evidence["implemented_layer_count"] == 8
    assert evidence["paper_execution_is_distinct_layer_in_spec"] is True
    assert evidence["attribution_layer_name"] == "attribution_and_anomaly_engine"
    assert evidence["boundary_violations"] == []


def test_registry_audit_detects_pytest_fixture_contamination(tmp_path: Path):
    module = load_script("audit_original_architecture")
    registry = tmp_path / "registry.sqlite"
    with sqlite3.connect(registry) as db:
        db.execute(
            """
            create table raw_objects (
                raw_object_id text primary key,
                source text not null,
                dataset text not null,
                uri text not null,
                received_at text not null,
                available_at text not null
            )
            """
        )
        db.execute(
            "insert into raw_objects values (?, ?, ?, ?, ?, ?)",
            (
                "raw_test",
                "browser_gcs_capture",
                "scanner_flash_raw",
                "file:///tmp/pytest-of-user/pytest-1/test_case/uploaded/flash.json",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    evidence = module.registry_evidence(registry)
    assert evidence["counts"]["raw_objects"] == 1
    assert len(evidence["test_contamination"]) == 1
    assert evidence["test_contamination"][0]["raw_object_id"] == "raw_test"


def test_work_package_status_never_claims_architecture_completion():
    module = load_script("update_master_end_state_status")
    status = module.build_status()
    assert status["work_package_complete"] == status["all_eight_closed"]
    assert status["architecture_complete"] is False
    assert status["architecture_status"] == "not_measured_by_this_work_package"
    assert status["architecture_audit_artifact"].endswith(
        "data/governance/original_architecture_self_audit.json"
    )


def test_news_availability_uses_observation_time_not_historical_publication_time():
    module = load_script("ingest_public_events")
    publication = NOW - timedelta(days=2)
    observed = NOW
    assert module.observed_availability(publication, observed) == observed
    future_publication = NOW + timedelta(minutes=10)
    assert module.observed_availability(future_publication, observed) == future_publication


def test_architecture_audit_is_strictly_incomplete_and_execution_free():
    module = load_script("audit_original_architecture")
    audit = module.build_audit()
    assert audit["verdict"] == "INCOMPLETE"
    assert audit["architecture_complete"] is False
    assert audit["execution_authority"] is False
    assert audit["live_execution"] is False
    assert audit["baseline"]["later_work_package_status_is_not_architecture_status"] is True
    finding_ids = {item["id"] for item in audit["critical_findings"]}
    assert "canonical_registry_not_adopted" in finding_ids
    assert "work_package_not_architecture_completion" in finding_ids
    assert "target_layer_count_mismatch" not in finding_ids


def test_holdout_gap_audit_uses_maximum_non_overlapping_windows():
    module = load_script("audit_holdout_c_existing_data_gap")
    days = [f"d{index:03d}" for index in range(104)]
    tickers = [f"S{index}" for index in range(8)]
    eligible = {day: tickers for day in days}
    windows = module.enumerate_valid_windows(days, eligible)
    selected = module.greedy_maximum(windows)
    assert len(selected) == 2
    assert selected[0]["start"] == "d000"
    assert selected[0]["end"] == "d051"
    assert selected[1]["start"] == "d052"
    assert selected[1]["end"] == "d103"


def test_master_checklist_records_base_correction_and_structural_rescue():
    text = (ROOT / "docs" / "master_end_state_checklist.md").read_text(encoding="utf-8")
    assert "11/12 strict independent origins" in text
    assert "essentially resolved but not yet cleared" in text
    assert "not a return to initial data discovery" in text
    assert "structural cohort eligibility cleared at 14/12 origins" in text
    assert "does not restore an untouched final holdout" in text


def test_rescue_v3_candidate_universe_is_fixed_and_outcome_free():
    module = load_script("run_holdout_c_rescue_v3")
    assert module.CANDIDATES == ("AMD", "AMZN", "GOOGL", "META", "TSLA")
    source = (SCRIPTS / "run_holdout_c_rescue_v3.py").read_text(encoding="utf-8")
    assert "all_candidates_must_be_evaluated" in source
    assert "candidate_order_has_no_stopping_rule" in source
    assert '"forward_return_scoring"' in source
    assert '"ranking_outcomes"' in source
    assert '"vendor_mixing": False' in source
    assert '"gate_relaxation": False' in source


def test_rescue_v3_merges_only_price_eligible_candidate_days():
    module = load_script("run_holdout_c_rescue_v3")
    base = {
        "daily_results": [{"date": "2025-01-02", "ticker": "SPY"}],
        "common_eligible_by_day": [
            {"date": "2025-01-02", "count": 7, "tickers": ["A", "B", "C", "D", "E", "F", "G"]},
            {"date": "2025-01-03", "count": 7, "tickers": ["A", "B", "C", "D", "E", "F", "G"]},
        ],
        "gate": {"name": "price_only"},
    }
    rows = [
        {"date": "2025-01-02", "ticker": "AMD", "price_only_eligible": True},
        {"date": "2025-01-02", "ticker": "META", "price_only_eligible": False},
        {"date": "2025-01-03", "ticker": "AMD", "price_only_eligible": False},
    ]
    merged = module.merge_scope(base, rows)
    by_date = {row["date"]: row for row in merged["common_eligible_by_day"]}
    assert by_date["2025-01-02"]["count"] == 8
    assert "AMD" in by_date["2025-01-02"]["tickers"]
    assert "META" not in by_date["2025-01-02"]["tickers"]
    assert by_date["2025-01-03"]["count"] == 7
    assert merged["ranking_or_model_outcomes_evaluated"] is False
    assert merged["gate_relaxed"] is False
