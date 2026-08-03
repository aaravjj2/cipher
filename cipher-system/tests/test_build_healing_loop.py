from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.build_healing import (
    HealingPolicy,
    ValidationStep,
    default_validation_steps,
    run_healing_cycle,
    source_changes,
    source_fingerprint,
    source_snapshot,
)
from core.research_platform.repair_actions import RepairExecutor
from core.research_platform.repair_boundary import RepairBoundaryViolation, RepairRequest


def _minimal_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    system = repo / "cipher-system"
    for relative in ("core", "scripts", "tests", "app/public"):
        (system / relative).mkdir(parents=True, exist_ok=True)
    (system / "core" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (system / "scripts" / "sample.py").write_text("print('ok')\n", encoding="utf-8")
    (system / "tests" / "test_sample.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (system / "app" / "server.mjs").write_text("export const ok = true;\n", encoding="utf-8")
    (system / "app" / "launcher.mjs").write_text("export const ok = true;\n", encoding="utf-8")
    (system / "app" / "public" / "app.js").write_text("const ok = true;\n", encoding="utf-8")
    (system / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (system / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("safe\n", encoding="utf-8")
    return repo, system


def test_source_snapshot_excludes_data_caches_and_env(tmp_path: Path):
    repo, system = _minimal_repo(tmp_path)
    (system / "data").mkdir()
    (system / "data" / "market.json").write_text('{"secret":"not-source"}\n', encoding="utf-8")
    (system / "app" / ".env").write_text("SAMPLE_SETTING=hidden\n", encoding="utf-8")
    cache = system / "core" / "__pycache__"
    cache.mkdir()
    (cache / "sample.pyc").write_bytes(b"cache")

    before = source_snapshot(repo)
    assert "cipher-system/core/sample.py" in before
    assert "cipher-system/requirements.txt" in before
    assert all("market.json" not in path for path in before)
    assert all(".env" not in path for path in before)
    assert all("__pycache__" not in path for path in before)

    fingerprint = source_fingerprint(before)
    (system / "data" / "market.json").write_text('{"changed":true}\n', encoding="utf-8")
    assert source_fingerprint(source_snapshot(repo)) == fingerprint

    (system / "core" / "sample.py").write_text("VALUE = 2\n", encoding="utf-8")
    after = source_snapshot(repo)
    assert source_fingerprint(after) != fingerprint
    assert source_changes(before, after)["modified"] == ["cipher-system/core/sample.py"]


def test_generated_cache_repair_only_removes_allowlisted_cache_material(tmp_path: Path):
    root = tmp_path / "system"
    cache = root / "core" / "__pycache__"
    pytest_cache = root / ".pytest_cache"
    cache.mkdir(parents=True)
    pytest_cache.mkdir(parents=True)
    pyc = cache / "module.pyc"
    pyc.write_bytes(b"compiled")
    retained = root / "core" / "module.py"
    retained.write_text("VALUE = 1\n", encoding="utf-8")
    (pytest_cache / "state").write_text("generated", encoding="utf-8")

    executor = RepairExecutor(tmp_path / "incidents")
    result = executor.clear_generated_test_caches(
        RepairRequest(
            action="clear_generated_test_caches",
            target="generated_caches",
            changes={"generated_cache_cleanup": True, "source_files_modified": False},
        ),
        root=root,
    )
    assert result["status"] == "repaired"
    assert not cache.exists()
    assert not pytest_cache.exists()
    assert retained.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert result["result"]["source_files_modified"] is False
    assert result["execution_authority"] is False

    with pytest.raises(RepairBoundaryViolation):
        executor.clear_generated_test_caches(
            RepairRequest(
                action="clear_generated_test_caches",
                target="generated_caches",
                changes={"gate_threshold": 0.01},
            ),
            root=root,
        )


def test_healing_cycle_passes_without_repairs(tmp_path: Path):
    repo, system = _minimal_repo(tmp_path)
    calls: list[str] = []

    def runner(step: ValidationStep, _cwd: Path, _tail: int):
        calls.append(step.name)
        return {"name": step.name, "command": list(step.command), "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    payload = run_healing_cycle(
        repository_root=repo,
        system_root=system,
        steps=(ValidationStep("fake", ("fake",)),),
        policy=HealingPolicy(max_heal_cycles=1, command_retry_attempts=2),
        runner=runner,
        governance_root=tmp_path / "governance",
        incident_root=tmp_path / "incidents",
    )
    assert payload["status"] == "passed"
    assert calls == ["fake"]
    assert payload["repair_actions"] == []
    assert payload["source_changes_during_cycle"] == {"added": [], "removed": [], "modified": []}
    assert payload["capabilities"]["edit_source_code"] is False
    assert payload["execution_authority"] is False
    latest = tmp_path / "governance" / "latest_build_healing_run.json"
    assert json.loads(latest.read_text(encoding="utf-8"))["status"] == "passed"


def test_transient_retry_is_confirmed_without_cache_cleanup(tmp_path: Path):
    repo, system = _minimal_repo(tmp_path)
    calls = 0

    def runner(step: ValidationStep, _cwd: Path, _tail: int):
        nonlocal calls
        calls += 1
        return {
            "name": step.name,
            "command": list(step.command),
            "returncode": 1 if calls == 1 else 0,
            "stdout_tail": "",
            "stderr_tail": "transient" if calls == 1 else "",
        }

    payload = run_healing_cycle(
        repository_root=repo,
        system_root=system,
        steps=(ValidationStep("fake", ("fake",)),),
        policy=HealingPolicy(max_heal_cycles=0, command_retry_attempts=1),
        runner=runner,
        governance_root=tmp_path / "governance",
        incident_root=tmp_path / "incidents",
    )
    assert payload["status"] == "healed_passed"
    assert calls == 3
    assert [item["type"] for item in payload["repair_actions"]] == ["validation_retry"]
    assert len(payload["validation_suites"]) == 2
    assert payload["validation_suites"][-1]["cycle"] == "0.retry_confirmation"


def test_healing_cycle_retries_clears_caches_and_then_passes(tmp_path: Path):
    repo, system = _minimal_repo(tmp_path)
    cache = system / "core" / "__pycache__"
    cache.mkdir()
    (cache / "sample.pyc").write_bytes(b"compiled")
    calls = 0

    def runner(step: ValidationStep, _cwd: Path, _tail: int):
        nonlocal calls
        calls += 1
        return {
            "name": step.name,
            "command": list(step.command),
            "returncode": 0 if calls >= 4 else 1,
            "stdout_tail": "",
            "stderr_tail": "synthetic failure" if calls < 4 else "",
        }

    payload = run_healing_cycle(
        repository_root=repo,
        system_root=system,
        steps=(ValidationStep("fake", ("fake",)),),
        policy=HealingPolicy(max_heal_cycles=1, command_retry_attempts=2),
        runner=runner,
        governance_root=tmp_path / "governance",
        incident_root=tmp_path / "incidents",
    )
    assert payload["status"] == "healed_passed"
    assert calls == 4
    assert [item["type"] for item in payload["repair_actions"]] == [
        "validation_retry",
        "generated_cache_cleanup",
    ]
    assert not cache.exists()
    assert payload["source_changes_during_cycle"] == {"added": [], "removed": [], "modified": []}


def test_persistent_failure_escalates_without_source_edits(tmp_path: Path):
    repo, system = _minimal_repo(tmp_path)
    source = system / "core" / "sample.py"
    original = source.read_text(encoding="utf-8")

    def runner(step: ValidationStep, _cwd: Path, _tail: int):
        return {"name": step.name, "command": list(step.command), "returncode": 1, "stdout_tail": "", "stderr_tail": "still broken"}

    payload = run_healing_cycle(
        repository_root=repo,
        system_root=system,
        steps=(ValidationStep("fake", ("fake",)),),
        policy=HealingPolicy(max_heal_cycles=1, command_retry_attempts=1),
        runner=runner,
        governance_root=tmp_path / "governance",
        incident_root=tmp_path / "incidents",
    )
    assert payload["status"] == "escalated_blocked"
    assert payload["failure_reason"] == "validation_failed_after_bounded_attempts"
    assert source.read_text(encoding="utf-8") == original
    assert payload["capabilities"]["commit_or_push"] is False


def test_source_mutation_during_validation_is_blocked(tmp_path: Path):
    repo, system = _minimal_repo(tmp_path)
    source = system / "core" / "sample.py"

    def runner(step: ValidationStep, _cwd: Path, _tail: int):
        source.write_text("VALUE = 999\n", encoding="utf-8")
        return {"name": step.name, "command": list(step.command), "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    payload = run_healing_cycle(
        repository_root=repo,
        system_root=system,
        steps=(ValidationStep("fake", ("fake",)),),
        policy=HealingPolicy(max_heal_cycles=1, command_retry_attempts=1),
        runner=runner,
        governance_root=tmp_path / "governance",
        incident_root=tmp_path / "incidents",
    )
    assert payload["status"] == "boundary_violation_blocked"
    assert payload["failure_reason"] == "validation_or_repair_mutated_source"
    assert payload["source_changes_during_cycle"]["modified"] == ["cipher-system/core/sample.py"]


def test_default_build_suite_contains_compile_node_and_full_pytest_only():
    steps = default_validation_steps(python_executable=sys.executable, node_executable="node")
    assert [step.name for step in steps] == [
        "git_diff_check",
        "python_compile",
        "node_server_syntax",
        "node_launcher_syntax",
        "node_browser_syntax",
        "pytest_full",
    ]
    command_text = " ".join(part for step in steps for part in step.command).lower()
    assert "compileall" in command_text
    assert "pytest" in command_text
    assert "cipher-system/tests" in command_text
    assert "--check" in command_text
    for forbidden in ("submit_order", "place_order", "create_order", "/v2/orders", "git push", "git commit"):
        assert forbidden not in command_text
