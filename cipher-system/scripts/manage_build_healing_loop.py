#!/usr/bin/env python3
"""Start, stop, or inspect Cipher's source-safe build healing watcher."""
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
GOV = ROOT / "data" / "governance" / "build_healing"
LOG_DIR = ROOT / "logs"
PID_PATH = GOV / "build_healing_loop.pid"
STATUS_PATH = GOV / "build_healing_loop_status.json"
RUNNER = ROOT / "scripts" / "run_build_healing_loop.py"


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
        "source_change_triggered": True,
        "bounded_mechanical_healing_only": True,
        "source_code_auto_edit": False,
        "commit_or_push": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, STATUS_PATH)
    return payload


def start(interval_seconds: int, *, run_on_start: bool) -> dict:
    pid = active_pid()
    if pid is not None:
        return write_status("start", pid=pid, state="already_running")
    GOV.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "build_healing_loop.log"
    command = [
        str(Path(sys.executable).absolute()),
        str(RUNNER),
        "--loop",
        "--interval-seconds",
        str(max(30, interval_seconds)),
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
        env={**os.environ, "CIPHER_BUILD_HEALING": "1"},
    )
    PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
    time.sleep(1)
    if process.poll() is not None:
        PID_PATH.unlink(missing_ok=True)
        return write_status(
            "start",
            pid=None,
            state="failed",
            detail=f"watcher exited with {process.returncode}; inspect {log_path}",
        )
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
    for _ in range(50):
        if not Path(f"/proc/{pid}").exists():
            PID_PATH.unlink(missing_ok=True)
            return write_status("stop", pid=None, state="stopped")
        time.sleep(0.1)
    return write_status("stop", pid=pid, state="stopping")


def status() -> dict:
    pid = active_pid()
    latest = GOV / "latest_build_healing_run.json"
    detail = str(latest) if latest.is_file() else None
    return write_status("status", pid=pid, state="running" if pid is not None else "not_running", detail=detail)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--run-on-start", action="store_true")
    args = parser.parse_args()
    if args.action == "start":
        payload = start(args.interval_seconds, run_on_start=args.run_on_start)
    elif args.action == "stop":
        payload = stop()
    else:
        payload = status()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if payload["state"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
