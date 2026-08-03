from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_research_infrastructure.py"
PREFETCH_SCRIPT = ROOT / "scripts" / "prefetch_research_models.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_specs_are_revision_pinned_and_research_only():
    module = _load(PREFETCH_SCRIPT, "prefetch_research_models_test")
    assert len(module.MODEL_SPECS) == 3
    for item in module.MODEL_SPECS:
        assert len(item["revision"]) == 40
        assert "reproducibility" in item["research_status"]


def test_snapshot_evidence_hashes_files(tmp_path):
    module = _load(PREFETCH_SCRIPT, "prefetch_research_models_hash_test")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    rows = module.snapshot_evidence(tmp_path)
    assert rows == [
        {
            "path": "config.json",
            "bytes": 2,
            "sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        }
    ]


def test_infrastructure_report_separates_runtime_from_host_blockers(monkeypatch, tmp_path):
    module = _load(AUDIT_SCRIPT, "research_infrastructure_audit_test")
    monkeypatch.setattr(
        module,
        "package_status",
        lambda: {"all_available": True, "python_312_or_newer": True, "modules": {}},
    )
    monkeypatch.setattr(
        module,
        "docker_status",
        lambda: {
            "local_container_execution_ready": False,
            "rootless_prerequisites": {"blockers": ["missing_setuid_prerequisite:newuidmap"]},
        },
    )
    monkeypatch.setattr(
        module,
        "build_manifest",
        lambda **_: {
            "models": [{"cached": True}],
            "synthetic_smoke": {
                "timesfm": {"passed": True},
                "kronos": {"passed": True},
            },
        },
    )
    monkeypatch.setattr(module, "lean_status", lambda _: {"local_engine_ready": False})
    monkeypatch.setattr(module, "github_status", lambda: {"push_ready": False})
    monkeypatch.setattr(module, "network_status", lambda: {})
    monkeypatch.setattr(module.kronos_research, "status", lambda: {"ready_for_inference": True})
    monkeypatch.setattr(
        module,
        "build_local_capability_report",
        lambda _: {
            "external_integrations": {
                "available_count": 5,
                "total_count": 5,
                "boundary_violations": [],
            },
            "execution_boundary": {"maximum_promotion_state": "LIVE_REVIEW_REQUIRED"},
        },
    )
    report = module.build_report(run_smoke=True, offline=True, cache_dir=tmp_path)
    assert report["runtime_complete"] is True
    assert report["host_and_delivery_complete"] is False
    assert "docker_daemon_unavailable_for_local_lean" in report["blockers"]
    assert "github_push_authentication_unavailable" in report["blockers"]
    assert report["research_boundaries"]["execution_authority"] is False
    assert report["research_boundaries"]["outcomes_evaluated"] is False


def test_rootless_audit_fails_closed_when_setuid_tools_missing(monkeypatch):
    module = _load(AUDIT_SCRIPT, "research_infrastructure_rootless_test")
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    result = module.rootless_prerequisite_status()
    assert result["eligible_for_rootless_install"] is False
    assert "missing_setuid_prerequisite:newuidmap" in result["blockers"]
    assert "missing_setuid_prerequisite:newgidmap" in result["blockers"]
