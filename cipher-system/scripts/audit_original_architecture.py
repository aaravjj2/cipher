#!/usr/bin/env python3
"""Audit Cipher against the original hybrid-architecture thesis.

This is a read-only evidence audit. It deliberately distinguishes structural
code availability from operational completion of the thesis exit criteria.
Closing a later work package does not make the original architecture complete.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GOVERNANCE_ROOT = ROOT / "data" / "governance"
REGISTRY_PATH = GOVERNANCE_ROOT / "research_registry.sqlite"
THESIS_PATH = REPOSITORY_ROOT / "CIPHER_CURRENT_STATE_AND_HYBRID_ARCHITECTURE_THESIS.md"

CODE_IDENTITY_KEYS = {
    "code_hash",
    "git_commit",
    "commit_hash",
    "source_commit",
    "normalizer_version",
}
VENDOR_URL_PATTERN = re.compile(
    r"https://(?:data\.alpaca|paper-api\.alpaca|api\.tradier|"
    r"data\.sec|www\.sec|query1\.finance\.yahoo|api\.gdelt)",
    re.IGNORECASE,
)
EXPECTED_UI_SCREENS = (
    "Morning Brief",
    "Strategy Lab",
    "Agent Observatory",
    "Event Log",
    "Portfolio/Risk",
    "Settings",
)


def run(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def latest(pattern: str) -> Path | None:
    candidates = sorted(ROOT.glob(pattern))
    return candidates[-1] if candidates else None


def registry_evidence(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    empty_counts = {
        name: 0
        for name in (
            "raw_objects",
            "datasets",
            "features",
            "feature_snapshots",
            "strategies",
            "experiments",
            "promotion_events",
            "prospective_tests",
            "prospective_observations",
            "news_events",
            "anomaly_events",
            "evidence_reconciliations",
            "audit_events",
        )
    }
    if not path.is_file():
        return {
            "exists": False,
            "path": str(path),
            "counts": empty_counts,
            "test_contamination": [],
            "news_sources": [],
        }
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=15) as db:
        db.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in db.execute("select name from sqlite_master where type='table'").fetchall()
        }
        counts = {
            name: int(db.execute(f'select count(*) from "{name}"').fetchone()[0])
            if name in tables
            else 0
            for name in empty_counts
        }
        contamination: list[dict[str, Any]] = []
        if "raw_objects" in tables:
            rows = db.execute(
                """
                select raw_object_id, source, dataset, uri, received_at, available_at
                from raw_objects
                where uri like '%/tmp/pytest-%' or uri like '%\\pytest-%'
                order by received_at
                """
            ).fetchall()
            contamination = [dict(row) for row in rows]
        news_sources: list[dict[str, Any]] = []
        if "news_events" in tables:
            rows = db.execute(
                """
                select source, count(*) as count,
                       min(publication_time) as first_publication_time,
                       max(publication_time) as last_publication_time
                from news_events
                group by source
                order by source
                """
            ).fetchall()
            news_sources = [dict(row) for row in rows]
    return {
        "exists": True,
        "path": str(path),
        "counts": counts,
        "test_contamination": contamination,
        "news_sources": news_sources,
    }


def file_count(root: Path, suffixes: tuple[str, ...] | None = None) -> int:
    if not root.exists():
        return 0
    return sum(
        1
        for path in root.rglob("*")
        if path.is_file() and (suffixes is None or path.suffix.lower() in suffixes)
    )


def data_evidence() -> dict[str, Any]:
    data_root = ROOT / "data"
    parquet_root = data_root / "normalized" / "alpaca_sip_holdout_c_1m"
    raw_root = data_root / "raw"
    database_files = sorted(
        path
        for path in data_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".sqlite", ".duckdb", ".db"}
    )
    return {
        "raw_files": file_count(raw_root),
        "normalized_parquet_files": file_count(parquet_root, (".parquet",)),
        "database_files": [str(path) for path in database_files],
        "duckdb_database_count": sum(path.suffix.lower() == ".duckdb" for path in database_files),
        "sqlite_database_count": sum(path.suffix.lower() == ".sqlite" for path in database_files),
        "raw_lake_registered_file_count": file_count(data_root / "raw_lake"),
        "research_snapshot_file_count": file_count(data_root / "research_snapshots"),
        "warehouse_export_file_count": file_count(data_root / "warehouse_exports"),
    }


def contains_code_identity(value: Any) -> bool:
    if isinstance(value, dict):
        if CODE_IDENTITY_KEYS.intersection(value):
            return True
        return any(contains_code_identity(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_code_identity(item) for item in value)
    return False


def artifact_identity_evidence() -> dict[str, Any]:
    json_files = sorted((ROOT / "data").rglob("*.json"))
    identified: list[str] = []
    invalid = 0
    for path in json_files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            invalid += 1
            continue
        if contains_code_identity(value):
            identified.append(str(path))
    return {
        "json_artifact_count": len(json_files),
        "json_artifacts_with_code_or_normalizer_identity": len(identified),
        "identified_paths": identified,
        "invalid_json_count": invalid,
        "coverage_ratio": (len(identified) / len(json_files)) if json_files else None,
    }


def external_integration_evidence() -> dict[str, Any]:
    from core.research_platform.external_integrations import (  # noqa: WPS433
        DEFAULT_EXTERNAL_INTEGRATIONS,
        DEFAULT_EXTERNAL_ROOT,
    )

    rows: list[dict[str, Any]] = []
    for integration in DEFAULT_EXTERNAL_INTEGRATIONS:
        path = integration.resolved_path(DEFAULT_EXTERNAL_ROOT)
        git_dir = path / ".git"
        actual_commit: str | None = None
        if git_dir.exists():
            result = run(["git", "-C", str(path), "rev-parse", "HEAD"])
            actual_commit = result.get("stdout") if result.get("ok") else None
        rows.append(
            {
                "name": integration.name,
                "path": str(path),
                "path_exists": path.exists(),
                "git_metadata_exists": git_dir.exists(),
                "declared_commit": integration.commit,
                "actual_commit": actual_commit,
                "commit_verified": bool(
                    actual_commit and actual_commit.startswith(integration.commit)
                ),
                "live_runtime_enabled": integration.live_runtime_enabled,
                "blocked_capabilities": list(integration.blocked_capabilities),
            }
        )
    return {
        "registered_count": len(rows),
        "path_available_count": sum(item["path_exists"] for item in rows),
        "git_verified_count": sum(item["commit_verified"] for item in rows),
        "integrations": rows,
    }


def vendor_access_evidence() -> dict[str, Any]:
    extensions = {".py", ".mjs"}
    paths: list[str] = []
    for base in (ROOT / "core", ROOT / "app", ROOT / "scripts"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            if "previous-work" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if VENDOR_URL_PATTERN.search(text):
                paths.append(str(path.relative_to(REPOSITORY_ROOT)))
    paths.sort()
    core_api = "cipher-system/core/app.py"
    return {
        "active_files_with_direct_vendor_urls": len(paths),
        "paths": paths,
        "core_api_calls_vendor_directly": core_api in paths,
        "target_rule_satisfied": False if core_api in paths else None,
    }


def stack_evidence() -> dict[str, Any]:
    from core.research_platform.seven_layer_stack import SevenLayerStackSpec  # noqa: WPS433

    spec = SevenLayerStackSpec.default()
    return {
        "implemented_layer_count": len(spec.layers),
        "thesis_target_layer_count": 8,
        "layer_names": [item.name for item in spec.layers],
        "boundary_violations": [item.to_dict() for item in spec.validate_boundaries()],
        "paper_execution_is_distinct_layer_in_spec": any(
            "paper" in item.name or "execution" in item.name for item in spec.layers
        ),
        "attribution_layer_name": next(
            (item.name for item in spec.layers if item.layer == 4), None
        ),
    }


def model_and_lean_evidence() -> dict[str, Any]:
    model_path = latest("data/governance/research_model_cache_manifest_*.json")
    lean_path = latest("data/governance/native_lean_build_audit_*.json")
    model = read_json(model_path)
    lean = read_json(lean_path)
    return {
        "model_manifest": str(model_path) if model_path else None,
        "models": [
            {
                "name": item.get("name"),
                "repo_id": item.get("repo_id"),
                "revision": item.get("revision"),
                "file_count": item.get("file_count"),
                "total_bytes": item.get("total_bytes"),
                "research_status": item.get("research_status"),
            }
            for item in model.get("models", [])
        ],
        "synthetic_smoke": model.get("synthetic_smoke"),
        "lean_audit": str(lean_path) if lean_path else None,
        "lean_source_commit": lean.get("source_commit"),
        "lean_native_build_ready": bool(lean.get("native_build_ready")),
        "lean_strategy_or_backtest_run": bool(lean.get("strategy_or_backtest_run")),
        "lean_promotion_eligible": bool(lean.get("promotion_eligible")),
        "lean_vulnerability_counts": (
            lean.get("vulnerability_audit", {}).get("severity_counts", {})
        ),
    }


def scheduler_evidence() -> dict[str, Any]:
    pid_path = GOVERNANCE_ROOT / "safe_scheduler.pid"
    pid: int | None = None
    active = False
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            active = Path(f"/proc/{pid}").exists()
        except (OSError, ValueError):
            pass
    state = read_json(GOVERNANCE_ROOT / "safe_scheduled_jobs_state.json")
    return {
        "active": active,
        "pid": pid,
        "allowed_job_ids": state.get("allowed_job_ids", []),
        "excluded_job_classes": state.get("excluded_job_classes", []),
        "boot_persistent": False,
        "persistence_reason": "detached process only; no usable user-systemd or crontab registration",
    }


def ui_evidence() -> dict[str, Any]:
    index = (ROOT / "app" / "public" / "index.html").read_text(
        encoding="utf-8", errors="ignore"
    )
    app = (ROOT / "app" / "public" / "app.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    combined = f"{index}\n{app}"
    presence = {screen: screen.lower() in combined.lower() for screen in EXPECTED_UI_SCREENS}
    return {
        "expected_six_screen_suite": presence,
        "implemented_count": sum(presence.values()),
        "research_status_view": "renderResearchStatus" in app
        and 'data-view="researchStatus"' in index,
    }


def git_evidence() -> dict[str, Any]:
    commit = run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT)
    status = run(["git", "status", "--short"], cwd=REPOSITORY_ROOT)
    return {
        "commit": commit.get("stdout") if commit.get("ok") else None,
        "working_tree_clean": bool(status.get("ok") and not status.get("stdout")),
        "status": status.get("stdout", ""),
    }


def phase(
    phase_id: int,
    name: str,
    state: str,
    *,
    exit_criteria_met: bool,
    reason: str,
    evidence: Iterable[str],
) -> dict[str, Any]:
    return {
        "phase": phase_id,
        "name": name,
        "state": state,
        "exit_criteria_met": exit_criteria_met,
        "reason": reason,
        "evidence": list(evidence),
    }


def layer(
    layer_id: str,
    name: str,
    state: str,
    *,
    operationally_complete: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "layer": layer_id,
        "name": name,
        "state": state,
        "operationally_complete": operationally_complete,
        "reason": reason,
    }


def build_audit() -> dict[str, Any]:
    registry = registry_evidence()
    data = data_evidence()
    identities = artifact_identity_evidence()
    external = external_integration_evidence()
    vendor = vendor_access_evidence()
    stack = stack_evidence()
    models = model_and_lean_evidence()
    scheduler = scheduler_evidence()
    ui = ui_evidence()
    git = git_evidence()

    counts = registry["counts"]
    canonical_research_entities = sum(
        counts[name]
        for name in (
            "datasets",
            "features",
            "strategies",
            "experiments",
            "promotion_events",
            "prospective_tests",
            "evidence_reconciliations",
        )
    )
    phases = [
        phase(
            0,
            "Establish repository truth",
            "partial",
            exit_criteria_met=False,
            reason=(
                "Active source has a Git identity and the execution boundary is documented, "
                "but most runtime artifacts do not reference a code identity and external "
                "repository commit claims are not empirically verifiable from the copied trees."
            ),
            evidence=[str(THESIS_PATH), git.get("commit") or "git_commit_unavailable"],
        ),
        phase(
            1,
            "Canonical data contracts and raw manifests",
            "not_met",
            exit_criteria_met=False,
            reason=(
                "Schemas and services exist, but the canonical registry contains no real dataset "
                "manifests, normalized-to-raw links, or research snapshots for the active panel."
            ),
            evidence=[str(REGISTRY_PATH), str(ROOT / "core" / "research_platform" / "datasets.py")],
        ),
        phase(
            2,
            "Experiment and strategy registry",
            "not_met",
            exit_criteria_met=False,
            reason=(
                "The registry and common experiment contracts are implemented and tested, but no "
                "real strategy or experiment is registered, so rerun/comparison exit criteria are unmet."
            ),
            evidence=[str(REGISTRY_PATH), str(ROOT / "core" / "research_platform" / "experiments.py")],
        ),
        phase(
            3,
            "Formal two-stage backtesting",
            "not_met",
            exit_criteria_met=False,
            reason=(
                "The native LEAN engine compiles and validators exist, but no real candidate has a "
                "paired fast-engine and LEAN replication or reconciliation artifact."
            ),
            evidence=[models.get("lean_audit") or "lean_audit_missing"],
        ),
        phase(
            4,
            "Generalized prospective validation",
            "not_met",
            exit_criteria_met=False,
            reason=(
                "Prospective services exist in code, but the canonical registry has no prospective "
                "test or observation for a registered strategy."
            ),
            evidence=[str(REGISTRY_PATH), str(ROOT / "core" / "research_platform" / "prospective.py")],
        ),
        phase(
            5,
            "Event attribution and news features",
            "partial",
            exit_criteria_met=False,
            reason=(
                "Revision-pinned FinBERT has produced real headline-metadata features, but full SEC/GDELT "
                "ingestion, historical replay coverage, event taxonomy, and real anomaly/residual records are absent."
            ),
            evidence=[str(REGISTRY_PATH), str(ROOT / "scripts" / "ingest_public_events.py")],
        ),
        phase(
            6,
            "Portfolio risk and advanced research automation",
            "deferred_by_prerequisite",
            exit_criteria_met=False,
            reason=(
                "Portfolio and context-panel controls are implemented only as guarded infrastructure; "
                "no strategies have graduated to provide valid real inputs."
            ),
            evidence=[str(ROOT / "core" / "research_platform" / "portfolio.py")],
        ),
        phase(
            7,
            "Separate live-execution decision",
            "deferred_by_design",
            exit_criteria_met=False,
            reason=(
                "Live execution is intentionally absent. This satisfies the safety boundary but is not "
                "an architecture-completion event or an authorization to add a broker adapter."
            ),
            evidence=[str(REPOSITORY_ROOT / "AGENTS.md")],
        ),
    ]

    layers = [
        layer(
            "governance",
            "Governance plane",
            "structural_partial",
            operationally_complete=False,
            reason=(
                "Strong immutable IDs, schemas, promotion gates, artifacts, and audit code; canonical "
                "runtime adoption is minimal and contains one pytest-contaminated raw-object record."
            ),
        ),
        layer(
            "1",
            "Hybrid data foundation",
            "partial",
            operationally_complete=False,
            reason=(
                "Selective raw and Parquet ingestion works, but active normalized data is outside a "
                "registered canonical dataset and upper application layers still call vendors directly."
            ),
        ),
        layer(
            "2",
            "Feature and forecasting services",
            "partial",
            operationally_complete=False,
            reason=(
                "Model runtimes, factor DSL, and FinBERT exist; the canonical feature registry and "
                "feature-snapshot table remain empty and forecast models are context-only/rejected."
            ),
        ),
        layer(
            "3",
            "Controlled research factory",
            "structural_partial",
            operationally_complete=False,
            reason=(
                "Common result and gate contracts exist, but no real governed experiment uses them."
            ),
        ),
        layer(
            "4",
            "Attribution and anomaly analysis",
            "code_only",
            operationally_complete=False,
            reason="The engine is tested but the anomaly registry contains zero real observations.",
        ),
        layer(
            "5",
            "Strategy graduation",
            "code_only",
            operationally_complete=False,
            reason=(
                "Promotion and LEAN audit gates exist, but no strategy, experiment, promotion, LEAN "
                "replication, or prospective record exists in the canonical registry."
            ),
        ),
        layer(
            "6",
            "Decision synthesis and portfolio risk",
            "deferred_by_prerequisite",
            operationally_complete=False,
            reason=(
                "Risk and portfolio proposal code is guarded and simulation-only, but no real promoted "
                "strategies or LLM context-panel runs exist."
            ),
        ),
        layer(
            "7",
            "Shadow/paper execution",
            "implemented_not_activated_by_promotion",
            operationally_complete=False,
            reason=(
                "The isolated paper executor has strong safety and reconciliation tests, but it is not "
                "represented as a distinct layer in SevenLayerStackSpec and no promoted strategy feeds it."
            ),
        ),
        layer(
            "8",
            "Evidence feedback loop",
            "code_only",
            operationally_complete=False,
            reason=(
                "Reconciliation and feedback classes exist, but the canonical registry has zero evidence "
                "reconciliations and no weekly promoted-strategy cycle can run."
            ),
        ),
    ]

    critical_findings = [
        {
            "id": "work_package_not_architecture_completion",
            "severity": "critical",
            "finding": (
                "The prior all_eight_closed status referred to an operational work package, not the "
                "thesis's eight architectural layers or phased exit criteria."
            ),
        },
        {
            "id": "canonical_registry_not_adopted",
            "severity": "critical",
            "finding": (
                f"Canonical real research entities total {canonical_research_entities}; datasets, "
                "features, strategies, experiments, promotions, prospective tests, and reconciliations are empty."
            ),
        },
        {
            "id": "test_contamination_in_production_registry",
            "severity": "high",
            "finding": (
                f"Production registry contains {len(registry['test_contamination'])} raw object(s) "
                "originating from pytest temporary paths."
            ),
        },
        {
            "id": "normalized_data_not_manifest_linked",
            "severity": "high",
            "finding": (
                f"There are {data['normalized_parquet_files']} normalized Parquet partitions but "
                f"{counts['datasets']} canonical dataset manifests and {counts['raw_objects']} raw-object records."
            ),
        },
        {
            "id": "runtime_code_identity_sparse",
            "severity": "high",
            "finding": (
                f"Only {identities['json_artifacts_with_code_or_normalizer_identity']} of "
                f"{identities['json_artifact_count']} JSON artifacts contain a recognized code or normalizer identity."
            ),
        },
        {
            "id": "target_layer_count_mismatch",
            "severity": "high",
            "finding": (
                f"The thesis defines eight layers plus governance; SevenLayerStackSpec defines "
                f"{stack['implemented_layer_count']} layers and does not model paper execution distinctly."
            ),
        },
        {
            "id": "vendor_access_not_fully_isolated",
            "severity": "medium",
            "finding": (
                f"{vendor['active_files_with_direct_vendor_urls']} active files contain direct vendor URLs, "
                "including the core API, so the target ingestion-only vendor boundary is not complete."
            ),
        },
        {
            "id": "external_repo_commits_unverified",
            "severity": "medium",
            "finding": (
                f"All {external['registered_count']} external integrations are registered by path, but "
                f"only {external['git_verified_count']} declared commits are verifiable from local Git metadata."
            ),
        },
        {
            "id": "news_layer_not_historical_replay_ready",
            "severity": "medium",
            "finding": (
                "Current event evidence is headline metadata from one accessible source. Legacy records "
                "used publication time as receipt time; future ingestion has been corrected, but existing "
                "records must not be treated as complete historical point-in-time replay evidence."
            ),
        },
        {
            "id": "scheduler_not_boot_persistent",
            "severity": "medium",
            "finding": (
                "The safe scheduler is an active detached process but has no systemd/crontab persistence "
                "and will not survive a VM reboot without manual restart."
            ),
        },
        {
            "id": "six_screen_ui_not_complete",
            "severity": "medium",
            "finding": (
                f"The later six-screen suite has {ui['implemented_count']} of {len(EXPECTED_UI_SCREENS)} "
                "named screens; the Research Status page is useful but is not the complete UI architecture."
            ),
        },
    ]

    accepted_deviations = [
        {
            "id": "local_first_storage",
            "status": "superseded_by_later_governance_decision",
            "detail": (
                "The original GCS/BigQuery topology was intentionally replaced by a local-first "
                "DuckDB/SQLite decision. This is not counted as an open cloud task, but the local "
                "canonical-store requirement is still not fully met."
            ),
        },
        {
            "id": "volume_sensitive_research",
            "status": "deferred_data_insufficient",
            "detail": "Independent reference-volume acquisition is intentionally outside the active queue.",
        },
        {
            "id": "live_execution",
            "status": "deferred_by_design",
            "detail": "No broker-order capability exists or is authorized.",
        },
    ]

    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline": {
            "thesis": str(THESIS_PATH),
            "target": "eight layers plus governance plane",
            "later_work_package_status_is_not_architecture_status": True,
        },
        "verdict": "INCOMPLETE",
        "architecture_complete": False,
        "safety_boundary_passed": not stack["boundary_violations"],
        "phase_exit_criteria_met": sum(item["exit_criteria_met"] for item in phases),
        "phase_count": len(phases),
        "operational_layers_complete": sum(item["operationally_complete"] for item in layers),
        "target_layer_count": 8,
        "phases": phases,
        "layers": layers,
        "critical_findings": critical_findings,
        "accepted_deviations": accepted_deviations,
        "evidence": {
            "git": git,
            "registry": registry,
            "data": data,
            "artifact_identity": identities,
            "external_integrations": external,
            "vendor_access": vendor,
            "stack": stack,
            "models_and_lean": models,
            "scheduler": scheduler,
            "ui": ui,
        },
        "execution_authority": False,
        "live_execution": False,
    }


def write_outputs(payload: dict[str, Any], output: Path | None = None) -> tuple[Path, Path]:
    GOVERNANCE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stable = output or GOVERNANCE_ROOT / "original_architecture_self_audit.json"
    timestamped = GOVERNANCE_ROOT / f"original_architecture_self_audit_{stamp}.json"
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    stable.write_text(encoded, encoding="utf-8")
    timestamped.write_text(encoded, encoding="utf-8")
    return stable, timestamped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = build_audit()
    stable, timestamped = write_outputs(payload, args.output)
    print(
        json.dumps(
            {
                "path": str(stable),
                "timestamped_path": str(timestamped),
                "verdict": payload["verdict"],
                "architecture_complete": payload["architecture_complete"],
                "phase_exit_criteria_met": payload["phase_exit_criteria_met"],
                "phase_count": payload["phase_count"],
                "operational_layers_complete": payload["operational_layers_complete"],
                "target_layer_count": payload["target_layer_count"],
                "critical_findings": len(payload["critical_findings"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
