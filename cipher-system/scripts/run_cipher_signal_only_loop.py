#!/usr/bin/env python3
"""Fingerprint-driven loop for Flash, Agentic, and Cluster research only."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.cipher_signal_overlay import signal_file_manifest  # noqa: E402
from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402
from run_cipher_signal_only_research import latest_dataset  # noqa: E402

GOV = ROOT / "data" / "governance" / "cipher_signal_only"
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
REPORT = GOV / "latest_signal_research.json"
SPECIFICS_REPORT = GOV / "latest_ticker_strategy_specifics.json"
COMPLETE_REPORT = GOV / "latest_complete_observations.json"
CYCLE = GOV / "latest_signal_research_cycle.json"
STATE = GOV / "signal_research_loop_state.json"
LOCK = GOV / "signal_research_loop.lock"
RESEARCH_SCRIPT = ROOT / "scripts" / "run_cipher_signal_only_research.py"
SPECIFICS_SCRIPT = ROOT / "scripts" / "run_cipher_signal_specifics.py"
COMPLETE_SCRIPT = ROOT / "scripts" / "run_cipher_complete_observations.py"
MODULE = ROOT / "core" / "research_platform" / "cipher_signal_overlay.py"
STOP_REQUESTED = False


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def operational_fingerprint() -> tuple[str, dict[str, Any]]:
    dataset = latest_dataset()
    inputs = {
        "dataset_id": dataset["dataset_id"],
        "dataset_checksum": dataset["checksum"],
        "dataset_latest_session": dataset["latest_session"],
        "capture_manifest": signal_file_manifest(CAPTURE_ROOT),
        "research_script_sha256": sha256_file(RESEARCH_SCRIPT),
        "specifics_script_sha256": sha256_file(SPECIFICS_SCRIPT),
        "complete_observations_script_sha256": sha256_file(COMPLETE_SCRIPT),
        "provider_mark_hour_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"),
        "overlay_module_sha256": sha256_file(MODULE),
    }
    return stable_id("cipher_signal_only_loop_inputs", inputs, length=64), inputs


def compact_report(
    report: dict[str, Any],
    specifics: dict[str, Any],
    complete: dict[str, Any],
) -> dict[str, Any]:
    scoring = report.get("forward_scoring") if isinstance(report.get("forward_scoring"), dict) else {}
    inventory = report.get("capture_inventory") if isinstance(report.get("capture_inventory"), dict) else {}
    populations = complete.get("population_counts") if isinstance(complete.get("population_counts"), dict) else {}
    cluster_research = complete.get("cluster_expiry_research") if isinstance(complete.get("cluster_expiry_research"), dict) else {}
    cluster_summary = cluster_research.get("summary") if isinstance(cluster_research.get("summary"), dict) else {}
    fixed_complete = complete.get("fixed_horizon_flash_agentic") if isinstance(complete.get("fixed_horizon_flash_agentic"), dict) else {}
    return {
        "mode": report.get("mode"),
        "active_sources": report.get("active_sources"),
        "capture_sessions": inventory.get("sessions"),
        "capture_episodes": inventory.get("episodes"),
        "daily_latest_states": ((report.get("daily_latest_states") or {}).get("count")),
        "matured_observations": scoring.get("matured_observations"),
        "pending_observations": scoring.get("pending_observations"),
        "unscorable_observations": scoring.get("unscorable_observations"),
        "by_source_horizon": scoring.get("by_source_horizon"),
        "freshness": report.get("freshness"),
        "ticker_groups": len(((specifics.get("ticker_analysis") or {}).get("by_source_ticker_horizon") or [])),
        "candidate_rules": ((specifics.get("candidate_rule_analysis") or {}).get("rules")),
        "latest_session_snapshot": specifics.get("latest_session_snapshot"),
        "complete_unique_episodes": populations.get("all_unique_episodes"),
        "complete_daily_terminal_states": populations.get("all_daily_terminal_source_ticker_states"),
        "cluster_expiry_records": populations.get("cluster_expiry_records"),
        "cluster_latest_completed_market_session": cluster_summary.get("latest_completed_market_session"),
        "cluster_matured_at_expiry": cluster_summary.get("matured_at_expiry"),
        "cluster_pending_expiry": cluster_summary.get("pending_expiry"),
        "cluster_finalized_at_expiry": cluster_summary.get("finalized_at_expiry"),
        "cluster_pending_mark_to_latest": cluster_summary.get("pending_mark_to_latest"),
        "cluster_completed_session_target_distance_analysis": cluster_summary.get("completed_sessions_by_target_distance_bucket"),
        "cluster_completed_session_time_analysis": cluster_summary.get("completed_sessions_by_signal_time_bucket"),
        "cluster_completed_session_option_path_diagnostics": cluster_summary.get("option_path_diagnostics_completed_sessions"),
        "cluster_completed_session_candidate_hypotheses": cluster_summary.get("candidate_hypotheses_completed_sessions"),
        "cluster_current_partial_candidate_hypotheses": cluster_summary.get("candidate_hypotheses_current_partial"),
        "complete_flash_agentic_summary": fixed_complete.get("summary"),
    }


def run_once(*, force: bool = False) -> dict[str, Any]:
    GOV.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "skipped_locked",
                "mode": "flash_agentic_cluster_only",
                "execution_authority": False,
            }
        fingerprint, inputs = operational_fingerprint()
        state = read_json(STATE)
        should_run = bool(
            force
            or not REPORT.is_file()
            or not SPECIFICS_REPORT.is_file()
            or not COMPLETE_REPORT.is_file()
            or state.get("operational_fingerprint") != fingerprint
        )
        process: dict[str, Any] | None = None
        if should_run:
            started = datetime.now(timezone.utc)
            completed = subprocess.run(
                [str(Path(sys.executable).absolute()), str(RESEARCH_SCRIPT)],
                cwd=ROOT.parent,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                env={**os.environ, "CIPHER_SIGNAL_ONLY_RESEARCH": "1"},
            )
            specifics_completed = None
            complete_completed = None
            if completed.returncode == 0 and REPORT.is_file():
                specifics_completed = subprocess.run(
                    [str(Path(sys.executable).absolute()), str(SPECIFICS_SCRIPT)],
                    cwd=ROOT.parent,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                    env={**os.environ, "CIPHER_SIGNAL_ONLY_RESEARCH": "1"},
                )
            if specifics_completed is not None and specifics_completed.returncode == 0 and SPECIFICS_REPORT.is_file():
                complete_completed = subprocess.run(
                    [str(Path(sys.executable).absolute()), str(COMPLETE_SCRIPT), "--workers", "4"],
                    cwd=ROOT.parent,
                    capture_output=True,
                    text=True,
                    timeout=1800,
                    check=False,
                    env={**os.environ, "CIPHER_SIGNAL_ONLY_RESEARCH": "1"},
                )
            process = {
                "started_at": started.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-8000:],
                "stderr_tail": completed.stderr[-8000:],
                "specifics_returncode": specifics_completed.returncode if specifics_completed else None,
                "specifics_stdout_tail": specifics_completed.stdout[-8000:] if specifics_completed else None,
                "specifics_stderr_tail": specifics_completed.stderr[-8000:] if specifics_completed else None,
                "complete_returncode": complete_completed.returncode if complete_completed else None,
                "complete_stdout_tail": complete_completed.stdout[-8000:] if complete_completed else None,
                "complete_stderr_tail": complete_completed.stderr[-8000:] if complete_completed else None,
            }
            status = (
                "completed"
                if completed.returncode == 0
                and specifics_completed is not None
                and specifics_completed.returncode == 0
                and complete_completed is not None
                and complete_completed.returncode == 0
                and REPORT.is_file()
                and SPECIFICS_REPORT.is_file()
                and COMPLETE_REPORT.is_file()
                else "failed"
            )
        else:
            status = "not_due_inputs_unchanged"
        report = read_json(REPORT)
        specifics = read_json(SPECIFICS_REPORT)
        complete = read_json(COMPLETE_REPORT)
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "mode": "flash_agentic_cluster_only",
            "operational_fingerprint": fingerprint,
            "inputs": inputs,
            "process": process,
            "report_path": str(REPORT),
            "specifics_report_path": str(SPECIFICS_REPORT),
            "complete_observations_report_path": str(COMPLETE_REPORT),
            "summary": compact_report(report, specifics, complete),
            "other_research_branches": "paused_frozen_reference_only",
            "automatic_promotion": False,
            "paper_or_live_execution": False,
            "execution_authority": False,
        }
        atomic_json(CYCLE, payload)
        if status != "failed":
            state.update(
                {
                    "schema_version": 1,
                    "updated_at": payload["created_at"],
                    "operational_fingerprint": fingerprint,
                    "latest_status": status,
                    "report_path": str(REPORT),
                    "specifics_report_path": str(SPECIFICS_REPORT),
                    "complete_observations_report_path": str(COMPLETE_REPORT),
                    "execution_authority": False,
                }
            )
            atomic_json(STATE, state)
        return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--run-on-start", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    os.environ.setdefault("CIPHER_SIGNAL_ONLY_LOOP", "1")

    if not args.loop:
        payload = run_once(force=args.force)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if payload.get("status") == "failed" else 0

    interval = max(3600, int(args.interval_seconds))
    first = True
    while not STOP_REQUESTED:
        if first and not args.run_on_start:
            first = False
        else:
            payload = run_once(force=args.force and first)
            print(json.dumps(payload, sort_keys=True), flush=True)
            first = False
        for _ in range(interval):
            if STOP_REQUESTED:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
