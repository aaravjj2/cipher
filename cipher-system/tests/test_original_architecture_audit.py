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


def test_original_thesis_has_eight_layers_but_stack_spec_has_seven():
    module = load_script("audit_original_architecture")
    evidence = module.stack_evidence()
    assert evidence["thesis_target_layer_count"] == 8
    assert evidence["implemented_layer_count"] == 7
    assert evidence["paper_execution_is_distinct_layer_in_spec"] is False
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
