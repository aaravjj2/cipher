"""Guarded refresh for the 2025/2026 recent-regime research branch.

After 5 p.m. New York time on weekdays, the branch checks whether the rolling
broad daily panel is stale. At most one data-refresh attempt is made per local
calendar day. Research reruns only when its canonical dataset, candidate matrix,
or relevant code changes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from .hashing import sha256_file, stable_id
from .recent_regime import RECENT_CANDIDATE_IDS

NY = ZoneInfo("America/New_York")
PREFIX = "alpaca_broad_daily_recent_2024_"
FALLBACK_NAME = "alpaca_broad_daily_2024_2026_ytd_holdout_v1"


def refresh_recent_regime(
    *,
    system_root: str | Path,
    state_path: str | Path,
    status_path: str | Path,
    force: bool = False,
    force_data: bool = False,
    now: datetime | None = None,
    timeout_seconds: int = 1200,
) -> dict[str, Any]:
    root = Path(system_root).resolve()
    state_path = Path(state_path)
    status_path = Path(status_path)
    registry = root / "data" / "governance" / "research_registry.sqlite"
    matrix = root / "data" / "governance" / "cross_period_strategy_matrix.json"
    report = root / "data" / "governance" / "recent_regime_research.json"
    data_script = root / "scripts" / "refresh_recent_equity_panel.py"
    research_script = root / "scripts" / "run_recent_regime_research.py"
    module = root / "core" / "research_platform" / "recent_regime.py"
    prospective_module = root / "core" / "research_platform" / "recent_regime_prospective.py"
    prospective_evaluation_module = root / "core" / "research_platform" / "recent_regime_prospective_evaluation.py"
    prospective_evaluation_script = root / "scripts" / "evaluate_recent_regime_prospective.py"
    prospective_evaluation_summary = root / "data" / "governance" / "recent_regime_prospective" / "latest_evaluation_summary.json"
    robustness_script = root / "scripts" / "run_recent_component_robustness.py"
    robustness_report = root / "data" / "governance" / "recent_component_robustness.json"
    signal_overlay_module = root / "core" / "research_platform" / "cipher_signal_overlay.py"
    signal_overlay_script = root / "scripts" / "run_cipher_signal_overlay_research.py"
    signal_overlay_report = root / "data" / "governance" / "cipher_signal_overlay_research.json"
    signal_capture_root = root / "data" / "browser_ingest"
    state = _read_json(state_path)
    current = now or datetime.now(timezone.utc)
    current_et = current.astimezone(NY)

    required = (
        registry,
        matrix,
        data_script,
        research_script,
        module,
        prospective_module,
        prospective_evaluation_module,
        prospective_evaluation_script,
        robustness_script,
        signal_overlay_module,
        signal_overlay_script,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        payload = _base_payload("blocked_missing_inputs")
        payload["missing_inputs"] = missing
        _write_json(status_path, payload)
        return payload

    dataset = _resolve_latest_dataset(registry)
    if dataset is None:
        payload = _base_payload("blocked_dataset_unavailable")
        _write_json(status_path, payload)
        return payload

    data_action = "not_due"
    data_process: dict[str, Any] | None = None
    local_date = current_et.date().isoformat()
    due = data_refresh_due(
        latest_session=dataset["latest_session"],
        now=current,
        last_attempt_date=state.get("last_data_refresh_attempt_date"),
    )
    if force_data or due:
        command = [
            str(Path(sys.executable).absolute()),
            str(data_script),
            "--end-date",
            local_date,
        ]
        started = datetime.now(timezone.utc)
        completed = subprocess.run(
            command,
            cwd=root.parent,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "CIPHER_RECENT_DATA_REFRESH": "1"},
        )
        data_process = {
            "started_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-5000:],
            "stderr_tail": completed.stderr[-5000:],
            "command": command,
        }
        data_action = "failed"
        if completed.returncode == 0:
            try:
                data_payload = json.loads(completed.stdout)
            except (json.JSONDecodeError, TypeError):
                data_payload = {}
            data_action = str(data_payload.get("status") or "completed")
        state["last_data_refresh_attempt_date"] = local_date
        if completed.returncode == 0:
            refreshed = _resolve_latest_dataset(registry)
            if refreshed is not None:
                dataset = refreshed

    fingerprint = stable_id(
        "recent_regime_inputs",
        {
            "dataset_id": dataset["dataset_id"],
            "dataset_checksum": dataset["checksum"],
            "latest_session": dataset["latest_session"],
            "candidate_pool": _candidate_pool_fingerprint(matrix),
            "research_script": _file_fingerprint(research_script),
            "recent_module": _file_fingerprint(module),
            "prospective_module": _file_fingerprint(prospective_module),
        },
        length=64,
    )
    should_run = bool(force or not report.is_file() or state.get("operational_fingerprint") != fingerprint)
    research_action = "not_due_inputs_unchanged"
    research_process: dict[str, Any] | None = None
    if should_run:
        command = [str(Path(sys.executable).absolute()), str(research_script)]
        started = datetime.now(timezone.utc)
        completed = subprocess.run(
            command,
            cwd=root.parent,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "CIPHER_RECENT_REGIME_REFRESH": "1"},
        )
        research_process = {
            "started_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-5000:],
            "stderr_tail": completed.stderr[-5000:],
            "command": command,
        }
        if completed.returncode != 0 or not report.is_file():
            research_action = "failed"
            payload = _base_payload("failed")
            payload.update(
                {
                    "dataset": dataset,
                    "data_refresh_action": data_action,
                    "research_action": research_action,
                    "data_process": data_process,
                    "research_process": research_process,
                    "operational_fingerprint": fingerprint,
                }
            )
            state.update(
                {
                    "schema_version": 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "last_refresh_status": "failed",
                    "last_failed_fingerprint": fingerprint,
                    "latest_dataset_id": dataset["dataset_id"],
                    "execution_authority": False,
                }
            )
            _write_json(state_path, state)
            _write_json(status_path, payload)
            return payload
        research_action = "completed"

    research = _read_json(report)
    if not research:
        payload = _base_payload("failed_invalid_report")
        payload.update({"dataset": dataset, "operational_fingerprint": fingerprint})
        _write_json(status_path, payload)
        return payload

    prospective_root = root / "data" / "governance" / "recent_regime_prospective"
    snapshot_fingerprint = _snapshot_fingerprint(prospective_root / "snapshots")
    prospective_evaluation_fingerprint = stable_id(
        "recent_prospective_evaluation_inputs",
        {
            "dataset_id": dataset["dataset_id"],
            "dataset_checksum": dataset["checksum"],
            "snapshots": snapshot_fingerprint,
            "evaluation_module": _file_fingerprint(prospective_evaluation_module),
            "evaluation_script": _file_fingerprint(prospective_evaluation_script),
        },
        length=64,
    )
    prospective_evaluation_action = "not_due_inputs_unchanged"
    prospective_evaluation_process: dict[str, Any] | None = None
    if (
        force
        or not prospective_evaluation_summary.is_file()
        or state.get("prospective_evaluation_fingerprint") != prospective_evaluation_fingerprint
    ):
        command = [str(Path(sys.executable).absolute()), str(prospective_evaluation_script)]
        started = datetime.now(timezone.utc)
        completed = subprocess.run(
            command,
            cwd=root.parent,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "CIPHER_RECENT_PROSPECTIVE_EVALUATION": "1"},
        )
        prospective_evaluation_process = {
            "started_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-5000:],
            "stderr_tail": completed.stderr[-5000:],
            "command": command,
        }
        prospective_evaluation_action = "completed" if completed.returncode == 0 else "failed"

    robustness_fingerprint = stable_id(
        "recent_component_robustness_inputs",
        {
            "dataset_id": dataset["dataset_id"],
            "dataset_checksum": dataset["checksum"],
            "candidate_pool": _candidate_pool_fingerprint(matrix),
            "robustness_script": _file_fingerprint(robustness_script),
            "research_script": _file_fingerprint(research_script),
            "recent_module": _file_fingerprint(module),
        },
        length=64,
    )
    robustness_action = "not_due_inputs_unchanged"
    robustness_process: dict[str, Any] | None = None
    if force or not robustness_report.is_file() or state.get("robustness_fingerprint") != robustness_fingerprint:
        command = [str(Path(sys.executable).absolute()), str(robustness_script)]
        started = datetime.now(timezone.utc)
        completed = subprocess.run(
            command,
            cwd=root.parent,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "CIPHER_RECENT_COMPONENT_ROBUSTNESS": "1"},
        )
        robustness_process = {
            "started_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-5000:],
            "stderr_tail": completed.stderr[-5000:],
            "command": command,
        }
        robustness_action = "completed" if completed.returncode == 0 else "failed"

    recent_snapshot_path = prospective_root / "snapshots" / f"{dataset['latest_session']}.json"
    signal_overlay_fingerprint = stable_id(
        "cipher_signal_overlay_inputs",
        {
            "dataset_id": dataset["dataset_id"],
            "dataset_checksum": dataset["checksum"],
            "recent_snapshot": _file_fingerprint(recent_snapshot_path) if recent_snapshot_path.is_file() else None,
            "session_signal_files": _session_signal_fingerprint(signal_capture_root, dataset["latest_session"]),
            "overlay_module": _file_fingerprint(signal_overlay_module),
            "overlay_script": _file_fingerprint(signal_overlay_script),
            "evaluation_module": _file_fingerprint(prospective_evaluation_module),
        },
        length=64,
    )
    signal_overlay_action = "not_due_inputs_unchanged"
    signal_overlay_process: dict[str, Any] | None = None
    signal_overlay_ready = bool(
        recent_snapshot_path.is_file()
        and _session_signal_fingerprint(signal_capture_root, dataset["latest_session"])
    )
    if not signal_overlay_ready:
        signal_overlay_action = "blocked_inputs_unavailable"
    elif force or not signal_overlay_report.is_file() or state.get("signal_overlay_fingerprint") != signal_overlay_fingerprint:
        command = [str(Path(sys.executable).absolute()), str(signal_overlay_script)]
        started = datetime.now(timezone.utc)
        completed = subprocess.run(
            command,
            cwd=root.parent,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "CIPHER_SIGNAL_OVERLAY_RESEARCH": "1"},
        )
        signal_overlay_process = {
            "started_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-5000:],
            "stderr_tail": completed.stderr[-5000:],
            "command": command,
        }
        signal_overlay_action = "completed" if completed.returncode == 0 else "failed"

    auxiliary_failed = "failed" in {prospective_evaluation_action, robustness_action, signal_overlay_action}
    any_completed = (
        research_action == "completed"
        or prospective_evaluation_action == "completed"
        or robustness_action == "completed"
        or signal_overlay_action == "completed"
        or data_action not in {"not_due", "not_advanced"}
    )
    status = "completed_with_auxiliary_failure" if auxiliary_failed else ("completed" if any_completed else "not_due_inputs_unchanged")
    prospective_evaluation = _read_json(prospective_evaluation_summary)
    robustness = _read_json(robustness_report)
    signal_overlay = _read_json(signal_overlay_report)
    summary = dict(research.get("summary") or {})
    summary.update(
        {
            "latest_session": dataset["latest_session"],
            "data_stale_calendar_days": max(
                0,
                (current_et.date() - pd.Timestamp(dataset["latest_session"]).date()).days,
            ),
            "promotion_eligible": False,
        }
    )
    payload = {
        **_base_payload(status),
        "dataset": dataset,
        "data_refresh_action": data_action,
        "research_action": research_action,
        "operational_fingerprint": fingerprint,
        "source_hashes": {
            "matrix_sha256": sha256_file(matrix),
            "research_script_sha256": sha256_file(research_script),
            "recent_module_sha256": sha256_file(module),
            "prospective_module_sha256": sha256_file(prospective_module),
            "prospective_evaluation_module_sha256": sha256_file(prospective_evaluation_module),
            "prospective_evaluation_script_sha256": sha256_file(prospective_evaluation_script),
            "robustness_script_sha256": sha256_file(robustness_script),
            "signal_overlay_module_sha256": sha256_file(signal_overlay_module),
            "signal_overlay_script_sha256": sha256_file(signal_overlay_script),
            "dataset_sha256": dataset["checksum"],
            "report_sha256": sha256_file(report),
        },
        "report_path": str(report),
        "summary": summary,
        "prospective_snapshot": research.get("prospective_snapshot"),
        "prospective_evaluation": prospective_evaluation,
        "prospective_evaluation_action": prospective_evaluation_action,
        "prospective_evaluation_fingerprint": prospective_evaluation_fingerprint,
        "component_robustness": robustness,
        "component_robustness_action": robustness_action,
        "component_robustness_fingerprint": robustness_fingerprint,
        "signal_overlay": signal_overlay,
        "signal_overlay_action": signal_overlay_action,
        "signal_overlay_fingerprint": signal_overlay_fingerprint,
        "data_process": data_process,
        "research_process": research_process,
        "prospective_evaluation_process": prospective_evaluation_process,
        "component_robustness_process": robustness_process,
        "signal_overlay_process": signal_overlay_process,
        "research_grade": False,
        "research_grade_reason": (
            "The candidate pool and selector family are outcome-informed by prior 2023-2026 research; "
            "the rolling monthly test is point-in-time but not an untouched independent holdout."
        ),
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    state.update(
        {
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_refresh_status": status,
            "operational_fingerprint": fingerprint,
            "latest_dataset_id": dataset["dataset_id"],
            "latest_session": dataset["latest_session"],
            "report_path": str(report),
            "report_sha256": sha256_file(report),
            "prospective_evaluation_fingerprint": (
                prospective_evaluation_fingerprint
                if prospective_evaluation_action != "failed"
                else state.get("prospective_evaluation_fingerprint")
            ),
            "robustness_fingerprint": (
                robustness_fingerprint if robustness_action != "failed" else state.get("robustness_fingerprint")
            ),
            "signal_overlay_fingerprint": (
                signal_overlay_fingerprint
                if signal_overlay_action not in {"failed", "blocked_inputs_unavailable"}
                else state.get("signal_overlay_fingerprint")
            ),
            "execution_authority": False,
        }
    )
    _write_json(state_path, state)
    _write_json(status_path, payload)
    return payload


def data_refresh_due(
    *,
    latest_session: str,
    now: datetime,
    last_attempt_date: str | None,
) -> bool:
    local = now.astimezone(NY)
    today = local.date()
    if local.weekday() >= 5 or local.hour < 17:
        return False
    if last_attempt_date == today.isoformat():
        return False
    return pd.Timestamp(latest_session).date() < today


def _resolve_latest_dataset(registry: Path) -> dict[str, Any] | None:
    with sqlite3.connect(f"file:{registry.as_posix()}?mode=ro", uri=True, timeout=30) as db:
        row = db.execute(
            """
            select d.dataset_id, d.name, d.payload_json, r.uri, r.checksum
            from datasets d
            join dataset_raw_objects l on l.dataset_id=d.dataset_id
            join raw_objects r on r.raw_object_id=l.raw_object_id
            where d.frozen=1 and d.quality_passed=1
              and (d.name like ? or d.name = ?)
            order by case when d.name like ? then 0 else 1 end, d.created_at desc
            limit 1
            """,
            (f"{PREFIX}%", FALLBACK_NAME, f"{PREFIX}%"),
        ).fetchone()
    if not row:
        return None
    payload = json.loads(row[2]) if row[2] else {}
    uri = str(row[3])
    path = Path(uri.removeprefix("file://")) if uri.startswith("file://") else None
    latest_session = ((payload.get("quality_checks") or {}).get("observed_end"))
    if not latest_session and path and path.is_file():
        frame = pd.read_parquet(path, columns=["timestamp"])
        latest_session = str(pd.to_datetime(frame["timestamp"], utc=True).max().date())
    return {
        "dataset_id": str(row[0]),
        "dataset_name": str(row[1]),
        "path": str(path) if path else None,
        "checksum": str(row[4]),
        "latest_session": str(latest_session),
    }


def _candidate_pool_fingerprint(matrix: Path) -> str:
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    by_id = {str(row["candidate_id"]): row for row in payload.get("matrix", [])}
    missing = [candidate_id for candidate_id in RECENT_CANDIDATE_IDS if candidate_id not in by_id]
    if missing:
        raise RuntimeError(f"recent candidate fingerprint is missing frozen IDs: {missing}")
    selected = [
        {
            "candidate_id": candidate_id,
            "family": by_id[candidate_id].get("family"),
            "parameters": by_id[candidate_id].get("parameters"),
            "parent_candidate_id": by_id[candidate_id].get("parent_candidate_id"),
        }
        for candidate_id in RECENT_CANDIDATE_IDS
    ]
    return stable_id("recent_regime_candidate_pool", selected, length=64)


def _file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha256_file(path)}


def _snapshot_fingerprint(path: Path) -> list[dict[str, Any]]:
    if not path.is_dir():
        return []
    return [
        {"name": item.name, "sha256": sha256_file(item), "size": item.stat().st_size}
        for item in sorted(path.glob("*.json"))
    ]


def _session_signal_fingerprint(path: Path, market_session: str) -> list[dict[str, Any]]:
    if not path.is_dir():
        return []
    files: list[Path] = []
    for scan_type in ("flash", "flash_agentic", "cluster"):
        files.extend(path.glob(f"{scan_type}-signals-v2-{market_session}.csv"))
    return [
        {"name": item.name, "sha256": sha256_file(item), "size": item.stat().st_size}
        for item in sorted(set(files))
    ]


def _base_payload(status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "branch": "recent_2025_2026_monthly_rolling_research",
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)
