from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.research_platform.news import NewsDocument
from core.research_platform.repair_actions import RepairExecutor
from core.research_platform.repair_boundary import RepairBoundaryViolation, RepairRequest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
NOW = datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    module_name = f"cipher_test_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_holdout_windows_require_one_ticker_set_across_all_52_sessions():
    module = load_script("construct_alpaca_holdout_c_cohort")
    days = [f"d{index:03d}" for index in range(52)]
    stable_seven = {f"S{index}" for index in range(7)}
    # Every day has eight eligible symbols, including the origin day, but the
    # eighth name alternates. The complete-window intersection is only seven.
    eligible = {
        day: sorted(stable_seven | ({"ROTATE_A"} if index % 2 == 0 else {"ROTATE_B"}))
        for index, day in enumerate(days)
    }
    assert len(eligible[days[31]]) == 8
    assert module.construct_candidate_blocks(days, eligible) == []


def test_holdout_windows_are_strictly_non_overlapping_and_deterministic():
    module = load_script("construct_alpaca_holdout_c_cohort")
    days = [f"d{index:03d}" for index in range(104)]
    tickers = [f"S{index}" for index in range(8)]
    eligible = {day: tickers for day in days}
    findings = module.construct_candidate_blocks(days, eligible)
    assert len(findings) == 1
    assert findings[0]["strict_independent_origins"] == 2
    first, second = findings[0]["origin_windows"]
    assert first["context_start"] == "d000"
    assert first["origin"] == "d031"
    assert first["outcome_end"] == "d051"
    assert second["context_start"] == "d052"
    assert second["origin"] == "d083"
    assert second["outcome_end"] == "d103"
    assert first["tickers"] == tickers


def test_alpaca_ingest_resolves_only_accepted_credential_aliases(monkeypatch):
    module = load_script("ingest_alpaca_holdout_c_panel")
    aliases = (
        "ALPACA_ALGO_PLUS_KEY",
        "ALPACA_ALGO_KEY",
        "ALPACA_API_KEY",
        "ALPACA_ALGO_PLUS_SECRET",
        "ALPACA_ALGO_SECRET",
        "ALPACA_API_SECRET",
        "ALPACA_SECRET_KEY",
    )
    for name in aliases:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALPACA_ALGO_PLUS_KEY", "test-key")
    monkeypatch.setenv("ALPACA_ALGO_PLUS_SECRET", "test-secret")
    monkeypatch.setattr(module, "load_local_env", lambda: {})
    assert module.headers() == {
        "APCA-API-KEY-ID": "test-key",
        "APCA-API-SECRET-KEY": "test-secret",
    }


def test_public_event_partition_reuses_provider_identity_despite_later_receipt():
    module = load_script("ingest_public_events")
    document = NewsDocument(
        source="public_feed",
        external_id="story-1",
        title="Issuer files an update",
        text="Issuer files an update — publisher metadata",
        publication_time=NOW,
        received_at=NOW + timedelta(minutes=10),
        available_at=NOW + timedelta(minutes=10),
        symbols=("AAPL",),
    )
    prior = {
        "source": "public_feed",
        "external_id": "story-1",
        "title": "Issuer files an update",
        "publication_time": NOW.isoformat(),
        "symbols": ["AAPL"],
    }
    reused, new_documents = module.partition_documents(
        [document],
        {("public_feed", "story-1"): prior},
    )
    assert new_documents == []
    assert reused[0]["record"] == prior
    assert reused[0]["ingestion_action"] == "reused_existing_event"


def test_bounded_repairs_never_modify_source_or_accept_protected_changes(tmp_path: Path):
    source = tmp_path / "source.json"
    source.write_text('{"b":2,"a":1}\n', encoding="utf-8")
    original = source.read_bytes()
    executor = RepairExecutor(tmp_path / "incidents")
    checksum = executor.recompute_checksum(
        RepairRequest(
            action="recompute_checksum",
            target=str(source),
            changes={"checksum_algorithm": "sha256", "content_modified": False},
        ),
        target=source,
        expected_sha256="0" * 64,
    )
    assert checksum["status"] == "escalated_blocked"
    assert source.read_bytes() == original
    assert checksum["protected_research_fields_changed"] is False
    assert checksum["execution_authority"] is False

    cache = tmp_path / "cache.json"
    rebuilt = executor.rebuild_derived_cache(
        RepairRequest(
            action="rebuild_derived_cache",
            target="derived_cache",
            changes={"derived_cache_only": True},
        ),
        source=source,
        destination=cache,
        transform=lambda payload: json.dumps(json.loads(payload), sort_keys=True).encode("utf-8"),
    )
    assert rebuilt["status"] == "repaired"
    assert source.read_bytes() == original
    assert json.loads(cache.read_text(encoding="utf-8")) == {"a": 1, "b": 2}

    with pytest.raises(RepairBoundaryViolation):
        executor.recompute_checksum(
            RepairRequest(
                action="recompute_checksum",
                target=str(source),
                changes={"gate_threshold": 0.10},
            ),
            target=source,
        )


def test_safe_scheduler_has_only_the_four_operational_jobs():
    module = load_script("run_safe_scheduled_jobs")
    scheduled = module.jobs()
    assert [job.job_id for job in scheduled] == [
        "public_event_ingestion",
        "bounded_repair_audit",
        "research_infrastructure_audit",
        "master_end_state_refresh",
    ]
    command_text = " ".join(part for job in scheduled for part in job.command).lower()
    for forbidden in ("factor", "model_study", "backtest", "paper_trade", "live_trade", "order"):
        assert forbidden not in command_text


def test_research_status_api_and_ui_are_read_only_surfaces():
    app_source = (ROOT / "core" / "app.py").read_text(encoding="utf-8")
    html_source = (ROOT / "app" / "public" / "index.html").read_text(encoding="utf-8")
    js_source = (ROOT / "app" / "public" / "app.js").read_text(encoding="utf-8")
    assert 'parsed.path == "/api/research-status"' in app_source
    assert 'data-view="researchStatus"' in html_source
    assert "function renderResearchStatus()" in js_source
    assert "EXECUTION AUTHORITY" in js_source
    assert "NONE" in js_source
    assert "/v2/orders" not in app_source
