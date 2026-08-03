#!/usr/bin/env python3
"""Start, stop, or inspect Cipher's guarded local scheduler daemon."""
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

ROOT = Path(__file__).resolve().parents[1]
GOV = ROOT / "data" / "governance"
LOG_DIR = ROOT / "logs"
PID_PATH = GOV / "safe_scheduler.pid"
STATUS_PATH = GOV / "safe_scheduler_status.json"
RUNNER = ROOT / "scripts" / "run_safe_scheduled_jobs.py"


def active_pid() -> int | None:
    if not PID_PATH.is_file():
        return None
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if Path(f"/proc/{pid}").exists() else None


def write_status(action: str, *, pid: int | None, state: str, detail: str | None = None) -> dict:
    GOV.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "state": state,
        "pid": pid,
        "detail": detail,
        "runner": str(RUNNER),
        "allowed_jobs_only": True,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def start(interval_seconds: int) -> dict:
    pid = active_pid()
    if pid is not None:
        return write_status("start", pid=pid, state="already_running")
    GOV.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "safe_research_scheduler.log"
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(Path(sys.executable).resolve()),
            str(RUNNER),
            "--loop",
            "--interval-seconds",
            str(max(300, interval_seconds)),
        ],
        cwd=ROOT.parent,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env={**os.environ, "HF_HOME": os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))},
    )
    PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
    time.sleep(1)
    if process.poll() is not None:
        PID_PATH.unlink(missing_ok=True)
        return write_status("start", pid=None, state="failed", detail=f"daemon exited with {process.returncode}; inspect {log_path}")
    return write_status("start", pid=process.pid, state="running", detail=str(log_path))


def stop() -> dict:
    pid = active_pid()
    if pid is None:
        PID_PATH.unlink(missing_ok=True)
        return write_status("stop", pid=None, state="not_running")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        PID_PATH.unlink(missing_ok=True)
        return write_status("stop", pid=None, state="not_running")
    for _ in range(30):
        if not Path(f"/proc/{pid}").exists():
            PID_PATH.unlink(missing_ok=True)
            return write_status("stop", pid=None, state="stopped")
        time.sleep(0.1)
    return write_status("stop", pid=pid, state="stopping")


def status() -> dict:
    pid = active_pid()
    return write_status("status", pid=pid, state="running" if pid is not None else "not_running")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()
    if args.action == "start":
        payload = start(args.interval_seconds)
    elif args.action == "stop":
        payload = stop()
    else:
        payload = status()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["state"] not in {"failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
