"""Guarded, branch-specific refresh for auxiliary equity research.

The factor-rotation and fixed-component regime-allocation studies are expensive.
Each branch therefore has its own fingerprint over its canonical datasets and
relevant source files. A factor-only change cannot trigger an unrelated regime
rerun. Failure attribution has a separate fingerprint over the completed branch
reports. The refresh never edits source, promotes a strategy, or invokes an
execution path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

from .hashing import sha256_file, stable_id

REGIME_DATASETS = (
    "ds_fb1e8d9aeb51f12407b08123",
    "ds_532bf7c42462c24a7c1a0a1f",
    "ds_3e9b83d533c645ea23e1abf8",
    "ds_f20f2e15e7d1041ce6a1858d",
)
FACTOR_DATASET = "ds_796df562a29d2b01d2e1ca24"


def refresh_auxiliary_research(
    *,
    system_root: str | Path,
    state_path: str | Path,
    status_path: str | Path,
    force: bool = False,
    timeout_seconds: int = 1200,
) -> dict[str, Any]:
    root = Path(system_root).resolve()
    state_path = Path(state_path)
    status_path = Path(status_path)
    registry = root / "data" / "governance" / "research_registry.sqlite"
    diagnostic_script = root / "scripts" / "build_research_failure_attribution.py"
    diagnostic_report = root / "data" / "governance" / "research_failure_attribution.json"
    required = (
        registry,
        diagnostic_script,
        root / "core" / "research_platform" / "regime_allocator.py",
        root / "core" / "research_platform" / "factor_rotation.py",
        root / "scripts" / "run_regime_allocator_research.py",
        root / "scripts" / "run_factor_rotation_research.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        payload = _base_payload("blocked_missing_inputs")
        payload["missing_inputs"] = missing
        _write_json(status_path, payload)
        return payload

    dataset_ids = (*REGIME_DATASETS, FACTOR_DATASET)
    dataset_files = _registered_dataset_files(registry, dataset_ids)
    if len(dataset_files) < len(dataset_ids):
        payload = _base_payload("blocked_missing_registered_datasets")
        payload["registered_dataset_files"] = {key: str(value) for key, value in dataset_files.items()}
        _write_json(status_path, payload)
        return payload
    missing_files = [str(path) for path in dataset_files.values() if not path.is_file()]
    if missing_files:
        payload = _base_payload("blocked_missing_dataset_files")
        payload["missing_dataset_files"] = missing_files
        _write_json(status_path, payload)
        return payload

    branches = {
        "regime_allocator": {
            "script": root / "scripts" / "run_regime_allocator_research.py",
            "report": root / "data" / "governance" / "regime_allocator_research.json",
            "inputs": tuple(dataset_files[dataset_id] for dataset_id in REGIME_DATASETS)
            + (
                root / "core" / "research_platform" / "regime_allocator.py",
                root / "scripts" / "run_regime_allocator_research.py",
            ),
            "dataset_ids": REGIME_DATASETS,
        },
        "factor_rotation": {
            "script": root / "scripts" / "run_factor_rotation_research.py",
            "report": root / "data" / "governance" / "factor_rotation_research.json",
            "inputs": (
                dataset_files[FACTOR_DATASET],
                root / "core" / "research_platform" / "factor_rotation.py",
                root / "scripts" / "run_factor_rotation_research.py",
            ),
            "dataset_ids": (FACTOR_DATASET,),
        },
    }
    state = _read_json(state_path)
    previous_branch_fingerprints = (
        state.get("branch_fingerprints")
        if isinstance(state.get("branch_fingerprints"), Mapping)
        else {}
    )
    branch_fingerprints: dict[str, str] = {}
    branch_actions: dict[str, str] = {}
    process_records: list[dict[str, Any]] = []

    for branch, spec in branches.items():
        inputs = tuple(Path(path) for path in spec["inputs"])
        report = Path(spec["report"])
        fingerprint = _files_fingerprint(
            f"auxiliary_{branch}_inputs",
            inputs,
            extra={"dataset_ids": list(spec["dataset_ids"])},
        )
        branch_fingerprints[branch] = fingerprint
        previous = previous_branch_fingerprints.get(branch)
        report_is_current = bool(
            report.is_file()
            and report.stat().st_mtime_ns >= max(path.stat().st_mtime_ns for path in inputs)
        )
        should_run = bool(
            force
            or not report.is_file()
            or (previous is not None and previous != fingerprint)
            or (previous is None and not report_is_current)
        )
        if should_run:
            record = _run_script(Path(spec["script"]), root.parent, timeout_seconds, branch)
            process_records.append(record)
            if record["returncode"] != 0:
                payload = _base_payload("failed")
                payload.update(
                    {
                        "failed_branch": branch,
                        "branch_fingerprints": branch_fingerprints,
                        "branch_actions": branch_actions,
                        "processes": process_records,
                    }
                )
                _write_json(status_path, payload)
                return payload
            branch_actions[branch] = "completed"
        elif previous is None:
            branch_actions[branch] = "seeded_existing_report"
        else:
            branch_actions[branch] = "not_due_inputs_unchanged"

    regime_report = Path(branches["regime_allocator"]["report"])
    factor_report = Path(branches["factor_rotation"]["report"])
    if not regime_report.is_file() or not factor_report.is_file():
        payload = _base_payload("failed_missing_branch_report")
        payload.update({"branch_actions": branch_actions, "processes": process_records})
        _write_json(status_path, payload)
        return payload

    diagnostic_inputs = (regime_report, factor_report, diagnostic_script)
    diagnostic_fingerprint = _files_fingerprint(
        "auxiliary_failure_attribution_inputs",
        diagnostic_inputs,
    )
    previous_diagnostic = state.get("diagnostic_fingerprint")
    diagnostic_is_current = bool(
        diagnostic_report.is_file()
        and diagnostic_report.stat().st_mtime_ns >= max(path.stat().st_mtime_ns for path in diagnostic_inputs)
    )
    should_run_diagnostics = bool(
        force
        or not diagnostic_report.is_file()
        or (previous_diagnostic is not None and previous_diagnostic != diagnostic_fingerprint)
        or (previous_diagnostic is None and not diagnostic_is_current)
    )
    if should_run_diagnostics:
        diagnostic_record = _run_script(
            diagnostic_script,
            root.parent,
            min(timeout_seconds, 300),
            "failure_attribution",
        )
        process_records.append(diagnostic_record)
        if diagnostic_record["returncode"] != 0:
            payload = _base_payload("failed_diagnostics")
            payload.update(
                {
                    "branch_fingerprints": branch_fingerprints,
                    "branch_actions": branch_actions,
                    "diagnostic_fingerprint": diagnostic_fingerprint,
                    "processes": process_records,
                }
            )
            _write_json(status_path, payload)
            return payload
        diagnostic_action = "completed"
    elif previous_diagnostic is None:
        diagnostic_action = "seeded_existing_report"
    else:
        diagnostic_action = "not_due_inputs_unchanged"

    regime = _read_json(regime_report)
    factor = _read_json(factor_report)
    diagnostics = _read_json(diagnostic_report)
    if not regime or not factor or not diagnostics:
        payload = _base_payload("failed_invalid_report")
        payload.update({"branch_actions": branch_actions, "processes": process_records})
        _write_json(status_path, payload)
        return payload

    all_actions = [*branch_actions.values(), diagnostic_action]
    if "completed" in all_actions:
        status = "completed"
    elif "seeded_existing_report" in all_actions:
        status = "seeded_existing_reports"
    else:
        status = "not_due_inputs_unchanged"
    operational_fingerprint = stable_id(
        "auxiliary_research_inputs",
        {
            "branches": branch_fingerprints,
            "diagnostic": diagnostic_fingerprint,
        },
        length=64,
    )
    payload = {
        **_base_payload(status),
        "operational_fingerprint": operational_fingerprint,
        "branch_fingerprints": branch_fingerprints,
        "branch_actions": branch_actions,
        "diagnostic_fingerprint": diagnostic_fingerprint,
        "diagnostic_action": diagnostic_action,
        "dataset_files": {key: str(value) for key, value in dataset_files.items()},
        "reports": {
            "regime_allocator": str(regime_report),
            "factor_rotation": str(factor_report),
            "failure_attribution": str(diagnostic_report),
        },
        "summary": summarize_auxiliary_reports(regime, factor, diagnostics),
        "processes": process_records or None,
        "research_grade": False,
        "research_grade_reason": "Both branches use development periods and adaptive hypotheses; no untouched final holdout is claimed.",
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
        "source_code_auto_edit": False,
    }
    _write_json(
        state_path,
        {
            "schema_version": 2,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "operational_fingerprint": operational_fingerprint,
            "branch_fingerprints": branch_fingerprints,
            "diagnostic_fingerprint": diagnostic_fingerprint,
            "last_refresh_status": status,
            "report_hashes": {
                "regime_allocator": sha256_file(regime_report),
                "factor_rotation": sha256_file(factor_report),
                "failure_attribution": sha256_file(diagnostic_report),
            },
            "execution_authority": False,
        },
    )
    _write_json(status_path, payload)
    return payload


def summarize_auxiliary_reports(
    regime: Mapping[str, Any],
    factor: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    regime_summary = regime.get("summary") if isinstance(regime.get("summary"), Mapping) else {}
    factor_summary = factor.get("summary") if isinstance(factor.get("summary"), Mapping) else {}
    return {
        "regime_allocator_specs": int(regime_summary.get("allocator_specs") or 0),
        "regime_allocator_effective_hypotheses": int(regime.get("effective_hypothesis_count") or 0),
        "regime_allocator_passes": int(regime_summary.get("screening_passes") or 0),
        "regime_allocator_leader": regime_summary.get("leader_name"),
        "factor_rotation_specs": int(factor_summary.get("strategies") or 0),
        "factor_rotation_effective_hypotheses": int(factor.get("effective_hypothesis_count") or 0),
        "factor_rotation_passes": int(factor_summary.get("screening_passes") or 0),
        "factor_rotation_leader": factor_summary.get("leader_name"),
        "factor_rotation_raw_lineage_freeze_verified": bool(
            factor.get("canonical_raw_lineage_freeze_verified", False)
        ),
        "dominant_failure_category": diagnostics.get("dominant_failure_category"),
        "aggregate_failure_category_counts": diagnostics.get("aggregate_failure_category_counts"),
        "allowed_claim": "no_auxiliary_strategy_clears_complete_contract",
        "promotion_eligible": False,
    }


def _registered_dataset_files(registry: Path, dataset_ids: Sequence[str]) -> dict[str, Path]:
    placeholders = ",".join("?" for _ in dataset_ids)
    query = f"""
        select l.dataset_id,r.uri
        from dataset_raw_objects l
        join raw_objects r on r.raw_object_id=l.raw_object_id
        where l.dataset_id in ({placeholders})
        order by l.dataset_id,r.uri
    """
    output: dict[str, Path] = {}
    with sqlite3.connect(registry) as db:
        for dataset_id, uri in db.execute(query, tuple(dataset_ids)).fetchall():
            parsed = urlparse(str(uri))
            if parsed.scheme != "file":
                continue
            path = Path(unquote(parsed.path))
            if path.suffix == ".parquet":
                output[str(dataset_id)] = path
    return output


def _files_fingerprint(
    namespace: str,
    paths: Sequence[Path],
    *,
    extra: Mapping[str, Any] | None = None,
) -> str:
    return stable_id(
        namespace,
        {
            "files": {
                str(path): {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
                for path in paths
            },
            "extra": dict(extra or {}),
        },
        length=64,
    )


def _run_script(script: Path, cwd: Path, timeout_seconds: int, branch: str) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    completed = subprocess.run(
        [str(Path(sys.executable).resolve()), str(script)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
        env={**os.environ, "CIPHER_AUXILIARY_RESEARCH_REFRESH": "1"},
    )
    return {
        "branch": branch,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "command": [str(Path(sys.executable).resolve()), str(script)],
    }


def _base_payload(status: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "branch": "auxiliary_equity_research",
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
        "source_code_auto_edit": False,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)
