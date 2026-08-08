"""Bounded build/test validation with fail-closed mechanical healing.

The loop may retry deterministic validation commands and remove generated
Python/pytest caches. It may not edit tracked source, install packages, alter
research evidence, relax gates, change promotion state, or create execution
authority. Persistent code failures are escalated with an immutable diagnostic
artifact for a human or coding agent to review.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .repair_actions import RepairExecutor, RepairPolicy
from .repair_boundary import RepairRequest


SYSTEM_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = SYSTEM_ROOT.parent
DEFAULT_GOVERNANCE_ROOT = SYSTEM_ROOT / "data" / "governance" / "build_healing"
DEFAULT_INCIDENT_ROOT = SYSTEM_ROOT / "data" / "repair_incidents"

SOURCE_ROOTS = (
    "cipher-system/core",
    "cipher-system/scripts",
    "cipher-system/tests",
    "cipher-system/app",
)
SOURCE_SINGLE_FILES = (
    "cipher-system/pytest.ini",
    "AGENTS.md",
)
EXCLUDED_PARTS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".git",
        ".venv",
        "node_modules",
        "data",
        "logs",
        "previous-work",
        "access-obsidian-complete-audit",
    }
)
EXCLUDED_NAMES = frozenset({".env", ".env.local", ".env.production"})
SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".mjs",
        ".cjs",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".md",
        ".html",
        ".css",
        ".sql",
        ".ps1",
        ".sh",
        ".txt",
        ".lock",
        ".yaml",
        ".yml",
    }
)

_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)(\s*[:=]\s*)([^\s,'\"}]+)"
)


@dataclass(frozen=True)
class ValidationStep:
    name: str
    command: tuple[str, ...]
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.name or not self.command:
            raise ValueError("validation step requires a name and command")
        if not 1 <= self.timeout_seconds <= 1800:
            raise ValueError("timeout_seconds must be between 1 and 1800")


@dataclass(frozen=True)
class HealingPolicy:
    max_heal_cycles: int = 1
    command_retry_attempts: int = 2
    output_tail_chars: int = 6000

    def __post_init__(self) -> None:
        if not 0 <= self.max_heal_cycles <= 2:
            raise ValueError("max_heal_cycles must be between 0 and 2")
        if not 1 <= self.command_retry_attempts <= 3:
            raise ValueError("command_retry_attempts must be between 1 and 3")
        if not 1000 <= self.output_tail_chars <= 20000:
            raise ValueError("output_tail_chars must be between 1000 and 20000")


CommandRunner = Callable[[ValidationStep, Path, int], Mapping[str, Any]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sanitize_output(value: str, *, tail_chars: int) -> str:
    redacted = _SECRET_PATTERN.sub(r"\1\2<redacted>", value or "")
    return redacted[-tail_chars:]


def _is_source_candidate(path: Path) -> bool:
    if path.name in EXCLUDED_NAMES:
        return False
    if EXCLUDED_PARTS.intersection(path.parts):
        return False
    return path.is_file() and (path.suffix.lower() in SOURCE_SUFFIXES or path.name in {"pytest.ini"})


def source_snapshot(repository_root: str | Path = REPOSITORY_ROOT) -> dict[str, str]:
    """Hash source/configuration files while excluding data, caches, and secrets."""

    root = Path(repository_root).resolve()
    snapshot: dict[str, str] = {}
    for relative_root in SOURCE_ROOTS:
        base = root / relative_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not _is_source_candidate(path):
                continue
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    for relative_name in SOURCE_SINGLE_FILES:
        path = root / relative_name
        if path.is_file() and path.name not in EXCLUDED_NAMES:
            snapshot[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    system_root = root / "cipher-system"
    if system_root.is_dir():
        for path in sorted(system_root.iterdir()):
            if _is_source_candidate(path):
                snapshot[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def source_fingerprint(snapshot: Mapping[str, str]) -> str:
    encoded = json.dumps(dict(sorted(snapshot.items())), separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_changes(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    return {
        "added": sorted(after_keys - before_keys),
        "removed": sorted(before_keys - after_keys),
        "modified": sorted(key for key in before_keys & after_keys if before[key] != after[key]),
    }


def has_source_changes(changes: Mapping[str, Sequence[str]]) -> bool:
    return any(changes.get(key) for key in ("added", "removed", "modified"))


def _resolve_node() -> str:
    node = shutil.which("node")
    if node:
        return node
    pinned = Path.home() / ".nvm" / "versions" / "node" / "v22.23.1" / "bin" / "node"
    return str(pinned) if pinned.is_file() else "node"


def default_validation_steps(
    *,
    python_executable: str | None = None,
    node_executable: str | None = None,
) -> tuple[ValidationStep, ...]:
    # Preserve the virtual-environment launcher path. Resolving the symlink can
    # invoke the base interpreter and lose the environment's installed tests.
    python = str(Path(python_executable or sys.executable).absolute())
    node = node_executable or _resolve_node()
    return (
        ValidationStep("git_diff_check", ("git", "diff", "--check"), timeout_seconds=60),
        ValidationStep(
            "python_compile",
            (
                python,
                "-m",
                "compileall",
                "-q",
                "cipher-system/core",
                "cipher-system/scripts",
                "cipher-system/tests",
            ),
            timeout_seconds=300,
        ),
        ValidationStep(
            "node_server_syntax",
            (node, "--check", "cipher-system/app/server.mjs"),
            timeout_seconds=60,
        ),
        ValidationStep(
            "node_launcher_syntax",
            (node, "--check", "cipher-system/app/launcher.mjs"),
            timeout_seconds=60,
        ),
        ValidationStep(
            # The browser bundle moved to cipher-system/web (Next.js) and
            # app/public became regenerable build output — app.js no longer
            # exists, so `node --check` on it failed every run. The equivalent
            # check for the current frontend is its own typecheck, which covers
            # the whole source tree rather than one concatenated bundle.
            "web_typecheck",
            ("npm", "--prefix", "cipher-system/web", "run", "typecheck"),
            timeout_seconds=300,
        ),
        ValidationStep(
            "pytest_full",
            # Scope collection to Cipher's active suite. The checkout also
            # contains copied external repositories with their own incompatible
            # tests, which are audited separately and are not product tests.
            (python, "-m", "pytest", "-q", "cipher-system/tests"),
            timeout_seconds=1200,
        ),
    )


def subprocess_command_runner(step: ValidationStep, cwd: Path, output_tail_chars: int) -> dict[str, Any]:
    started = time.monotonic()
    started_at = _utc_now()
    try:
        completed = subprocess.run(
            step.command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=step.timeout_seconds,
            check=False,
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "CIPHER_BUILD_HEALING": "1",
            },
        )
        return {
            "name": step.name,
            "command": list(step.command),
            "started_at": started_at.isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "returncode": completed.returncode,
            "timed_out": False,
            "stdout_tail": _sanitize_output(completed.stdout, tail_chars=output_tail_chars),
            "stderr_tail": _sanitize_output(completed.stderr, tail_chars=output_tail_chars),
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {
            "name": step.name,
            "command": list(step.command),
            "started_at": started_at.isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "returncode": 124,
            "timed_out": True,
            "stdout_tail": _sanitize_output(stdout, tail_chars=output_tail_chars),
            "stderr_tail": _sanitize_output(stderr, tail_chars=output_tail_chars),
        }
    except OSError as exc:
        return {
            "name": step.name,
            "command": list(step.command),
            "started_at": started_at.isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "returncode": 127,
            "timed_out": False,
            "stdout_tail": "",
            "stderr_tail": f"{type(exc).__name__}: {exc}",
        }


def run_validation_suite(
    steps: Iterable[ValidationStep],
    *,
    repository_root: str | Path = REPOSITORY_ROOT,
    runner: CommandRunner = subprocess_command_runner,
    output_tail_chars: int = 6000,
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    results: list[dict[str, Any]] = []
    for step in steps:
        result = dict(runner(step, root, output_tail_chars))
        results.append(result)
        if int(result.get("returncode", 1)) != 0:
            break
    failed = next((item for item in results if int(item.get("returncode", 1)) != 0), None)
    return {
        "status": "passed" if failed is None else "failed",
        "steps": results,
        "failed_step": failed,
        "completed_step_count": len(results),
    }


def _git_state(repository_root: Path) -> dict[str, Any]:
    def run(command: list[str]) -> tuple[int, str]:
        completed = subprocess.run(command, cwd=repository_root, capture_output=True, text=True, check=False, timeout=30)
        return completed.returncode, completed.stdout.strip()

    commit_rc, commit = run(["git", "rev-parse", "HEAD"])
    status_rc, status = run(["git", "status", "--short"])
    return {
        "commit": commit if commit_rc == 0 else None,
        "working_tree_clean": bool(status_rc == 0 and not status),
        "status": status if status_rc == 0 else "git_status_unavailable",
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_run_artifacts(
    payload: Mapping[str, Any],
    *,
    governance_root: str | Path = DEFAULT_GOVERNANCE_ROOT,
) -> dict[str, str]:
    root = Path(governance_root).resolve()
    stamp = _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    timestamped = root / f"build_healing_run_{stamp}.json"
    latest = root / "latest_build_healing_run.json"
    _atomic_json(timestamped, payload)
    _atomic_json(latest, payload)
    return {"timestamped": str(timestamped), "latest": str(latest)}


def _failed_step_retry(
    failed_step: Mapping[str, Any],
    *,
    steps_by_name: Mapping[str, ValidationStep],
    repository_root: Path,
    runner: CommandRunner,
    repair_executor: RepairExecutor,
    policy: HealingPolicy,
) -> dict[str, Any]:
    name = str(failed_step.get("name", "unknown"))
    step = steps_by_name.get(name)
    if step is None:
        return {"status": "not_attempted", "reason": "failed_step_not_registered"}
    request = RepairRequest(
        action="retry_validation_command",
        target=name,
        changes={
            "validation_retry": True,
            "source_files_modified": False,
            "command_name": name,
        },
    )

    def operation() -> Mapping[str, Any]:
        return runner(step, repository_root, policy.output_tail_chars)

    return repair_executor.retry_validation_command(
        request,
        operation,
        policy=RepairPolicy(
            max_attempts=policy.command_retry_attempts,
            backoff_seconds=0.5,
        ),
    )


def _clear_caches(
    *,
    system_root: Path,
    repair_executor: RepairExecutor,
) -> dict[str, Any]:
    request = RepairRequest(
        action="clear_generated_test_caches",
        target="cipher_system_generated_test_caches",
        changes={
            "generated_cache_cleanup": True,
            "source_files_modified": False,
        },
    )
    return repair_executor.clear_generated_test_caches(request, root=system_root)


def run_healing_cycle(
    *,
    repository_root: str | Path = REPOSITORY_ROOT,
    system_root: str | Path = SYSTEM_ROOT,
    steps: Sequence[ValidationStep] | None = None,
    policy: HealingPolicy = HealingPolicy(),
    runner: CommandRunner = subprocess_command_runner,
    governance_root: str | Path = DEFAULT_GOVERNANCE_ROOT,
    incident_root: str | Path = DEFAULT_INCIDENT_ROOT,
) -> dict[str, Any]:
    """Run validation, apply bounded cache/retry healing, and fail closed."""

    repo = Path(repository_root).resolve()
    system = Path(system_root).resolve()
    selected_steps = tuple(steps or default_validation_steps())
    steps_by_name = {step.name: step for step in selected_steps}
    repair_executor = RepairExecutor(incident_root)
    started_at = _utc_now()
    initial_snapshot = source_snapshot(repo)
    initial_fingerprint = source_fingerprint(initial_snapshot)
    suites: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    status = "escalated_blocked"
    failure_reason: str | None = None

    for cycle in range(policy.max_heal_cycles + 1):
        suite = run_validation_suite(
            selected_steps,
            repository_root=repo,
            runner=runner,
            output_tail_chars=policy.output_tail_chars,
        )
        suite["cycle"] = cycle
        suites.append(suite)

        current_snapshot = source_snapshot(repo)
        changes = source_changes(initial_snapshot, current_snapshot)
        if has_source_changes(changes):
            status = "boundary_violation_blocked"
            failure_reason = "validation_or_repair_mutated_source"
            break

        if suite["status"] == "passed":
            status = "passed" if cycle == 0 else "healed_passed"
            break

        failed_step = suite.get("failed_step") or {}
        retry = _failed_step_retry(
            failed_step,
            steps_by_name=steps_by_name,
            repository_root=repo,
            runner=runner,
            repair_executor=repair_executor,
            policy=policy,
        )
        repairs.append({"type": "validation_retry", "incident": retry})

        after_retry_snapshot = source_snapshot(repo)
        retry_changes = source_changes(initial_snapshot, after_retry_snapshot)
        if has_source_changes(retry_changes):
            status = "boundary_violation_blocked"
            failure_reason = "validation_retry_mutated_source"
            break

        if retry.get("status") == "repaired":
            confirmation = run_validation_suite(
                selected_steps,
                repository_root=repo,
                runner=runner,
                output_tail_chars=policy.output_tail_chars,
            )
            confirmation["cycle"] = f"{cycle}.retry_confirmation"
            suites.append(confirmation)
            confirmation_snapshot = source_snapshot(repo)
            confirmation_changes = source_changes(initial_snapshot, confirmation_snapshot)
            if has_source_changes(confirmation_changes):
                status = "boundary_violation_blocked"
                failure_reason = "retry_confirmation_mutated_source"
                break
            if confirmation["status"] == "passed":
                status = "healed_passed"
                break

        if cycle >= policy.max_heal_cycles:
            failure_reason = "validation_failed_after_bounded_attempts"
            break

        cache_repair = _clear_caches(system_root=system, repair_executor=repair_executor)
        repairs.append({"type": "generated_cache_cleanup", "incident": cache_repair})

    final_snapshot = source_snapshot(repo)
    final_fingerprint = source_fingerprint(final_snapshot)
    final_changes = source_changes(initial_snapshot, final_snapshot)
    if has_source_changes(final_changes) and status not in {"boundary_violation_blocked"}:
        status = "boundary_violation_blocked"
        failure_reason = "source_changed_during_healing_cycle"

    completed_at = _utc_now()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "status": status,
        "failure_reason": failure_reason,
        "source_fingerprint": initial_fingerprint,
        "final_source_fingerprint": final_fingerprint,
        "source_file_count": len(initial_snapshot),
        "source_changes_during_cycle": final_changes,
        "git": _git_state(repo),
        "validation_steps": [
            {"name": step.name, "command": list(step.command), "timeout_seconds": step.timeout_seconds}
            for step in selected_steps
        ],
        "validation_suites": suites,
        "repair_actions": repairs,
        "healing_policy": {
            "max_heal_cycles": policy.max_heal_cycles,
            "command_retry_attempts": policy.command_retry_attempts,
            "output_tail_chars": policy.output_tail_chars,
        },
        "capabilities": {
            "retry_validation_commands": True,
            "clear_generated_test_caches": True,
            "edit_source_code": False,
            "install_packages": False,
            "change_research_data": False,
            "change_gates_or_thresholds": False,
            "change_promotion_state": False,
            "commit_or_push": False,
            "paper_or_live_execution": False,
        },
        "protected_research_fields_changed": False,
        "gate_relaxed": False,
        "promotion_changed": False,
        "execution_authority": False,
    }
    payload["artifacts"] = write_run_artifacts(payload, governance_root=governance_root)
    return payload


def latest_run(governance_root: str | Path = DEFAULT_GOVERNANCE_ROOT) -> dict[str, Any]:
    path = Path(governance_root).resolve() / "latest_build_healing_run.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
