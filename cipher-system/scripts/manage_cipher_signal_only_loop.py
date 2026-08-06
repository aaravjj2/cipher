#!/usr/bin/env python3
"""Start, stop, or inspect the Flash/Agentic/Cluster-only research loop."""
from __future__ import annotations

import argparse
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
GOV = ROOT / "data" / "governance" / "cipher_signal_only"
LOG_DIR = ROOT / "logs"
PID_PATH = GOV / "signal_research_loop.pid"
STATUS_PATH = GOV / "signal_research_loop_status.json"
RUNNER = ROOT / "scripts" / "run_cipher_signal_only_loop.py"
REPORT = GOV / "latest_signal_research.json"
SPECIFICS = GOV / "latest_ticker_strategy_specifics.json"
CYCLE = GOV / "latest_signal_research_cycle.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def active_pid() -> int | None:
    if not PID_PATH.is_file():
        return None
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        return None
    try:
        command = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
    except OSError:
        return None
    return pid if str(RUNNER) in command else None


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def summary() -> dict[str, Any]:
    report = read_json(REPORT)
    cycle = read_json(CYCLE)
    specifics = read_json(SPECIFICS)
    inventory = report.get("capture_inventory") if isinstance(report.get("capture_inventory"), dict) else {}
    scoring = report.get("forward_scoring") if isinstance(report.get("forward_scoring"), dict) else {}
    return {
        "latest_cycle_status": cycle.get("status"),
        "latest_cycle_created_at": cycle.get("created_at"),
        "mode": report.get("mode") or cycle.get("mode"),
        "active_sources": report.get("active_sources"),
        "capture_sessions": inventory.get("sessions"),
        "capture_episodes": inventory.get("episodes"),
        "daily_latest_states": ((report.get("daily_latest_states") or {}).get("count")),
        "matured_observations": scoring.get("matured_observations"),
        "pending_observations": scoring.get("pending_observations"),
        "unscorable_observations": scoring.get("unscorable_observations"),
        "by_source_horizon": scoring.get("by_source_horizon"),
        "pair_agreement": ((report.get("cross_source") or {}).get("pair_agreement")),
        "freshness": report.get("freshness"),
        "ticker_analysis": specifics.get("ticker_analysis"),
        "direction_analysis": specifics.get("direction_analysis"),
        "timing_analysis": specifics.get("timing_analysis"),
        "candidate_rule_analysis": specifics.get("candidate_rule_analysis"),
        "latest_session_snapshot": specifics.get("latest_session_snapshot"),
        "other_research_branches": "paused_frozen_reference_only",
    }


def write_status(action: str, *, pid: int | None, state: str, detail: str | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "state": state,
        "pid": pid,
        "detail": detail,
        "runner": str(RUNNER),
        "focus": [
            "flash_source_quality",
            "agentic_source_quality",
            "cluster_source_quality",
            "source_freshness_and_capture_gaps",
            "daily_latest_directional_states",
            "cross_source_agreement_and_conflict",
            "cross_source_lead_lag",
            "setup_family_performance",
            "score_bucket_calibration",
            "one_five_twenty_one_session_future_open_scoring",
            "symbol_and_dataset_coverage",
            "ticker_level_performance",
            "source_ticker_setup_interactions",
            "deduplicated_candidate_rule_comparison",
            "leave_one_ticker_out_rule_stress",
        ],
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
        **summary(),
    }
    atomic_json(STATUS_PATH, payload)
    return payload


def start(interval_seconds: int, *, run_on_start: bool) -> dict[str, Any]:
    pid = active_pid()
    if pid is not None:
        return write_status("start", pid=pid, state="already_running", detail=str(CYCLE))
    GOV.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "cipher_signal_only_loop.log"
    command = [
        str(Path(sys.executable).absolute()),
        str(RUNNER),
        "--loop",
        "--interval-seconds",
        str(max(3600, interval_seconds)),
    ]
    if run_on_start:
        command.append("--run-on-start")
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=ROOT.parent,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env={**os.environ, "CIPHER_SIGNAL_ONLY_LOOP": "1"},
    )
    PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
    time.sleep(1)
    if process.poll() is not None:
        PID_PATH.unlink(missing_ok=True)
        return write_status("start", pid=None, state="failed", detail=f"loop exited with {process.returncode}; inspect {log_path}")
    return write_status("start", pid=process.pid, state="running", detail=str(log_path))


def stop() -> dict[str, Any]:
    pid = active_pid()
    if pid is None:
        PID_PATH.unlink(missing_ok=True)
        return write_status("stop", pid=None, state="not_running")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        PID_PATH.unlink(missing_ok=True)
        return write_status("stop", pid=None, state="not_running")
    for _ in range(100):
        if not Path(f"/proc/{pid}").exists():
            PID_PATH.unlink(missing_ok=True)
            return write_status("stop", pid=None, state="stopped")
        time.sleep(0.1)
    return write_status("stop", pid=pid, state="stopping")


def status() -> dict[str, Any]:
    pid = active_pid()
    return write_status(
        "status",
        pid=pid,
        state="running" if pid is not None else "not_running",
        detail=str(CYCLE) if CYCLE.is_file() else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--run-on-start", action="store_true")
    args = parser.parse_args()
    if args.action == "start":
        payload = start(args.interval_seconds, run_on_start=args.run_on_start)
    elif args.action == "stop":
        payload = stop()
    else:
        payload = status()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if payload.get("state") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
