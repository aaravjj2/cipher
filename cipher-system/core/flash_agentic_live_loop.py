"""Continuous Flash Agentic live simulation loop.

Runs the visible Flash Agentic browser capture repeatedly, records newly spotted
signals, and marks simulated entries for target/profit/stop alerts. This is
research-only: no broker account, preview, order, or trading endpoints.
"""
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
DATA_DIR = ROOT / "data" / "flash_agentic"
STATUS_PATH = DATA_DIR / "live_status.json"
PID_PATH = DATA_DIR / "live_loop.pid"
LOCK_PATH = DATA_DIR / "live_loop.lock"
LOG_PATH = DATA_DIR / "live_loop.log"
CAPTURE_SCRIPT = ROOT / "scripts" / "capture_accessobsidian_scans.py"
SIM_SCRIPT = ROOT / "core" / "flash_agentic_sim.py"


RUNNING = True


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def append_log(message: str, payload: dict[str, Any] | None = None) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": utcnow(), "message": message, "payload": payload or {}}, default=str) + "\n")


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if PID_PATH.is_file():
        try:
            pid = int(PID_PATH.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if pid and is_pid_running(pid):
            raise SystemExit(f"Flash live loop already running with pid {pid}")
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    LOCK_PATH.write_text(f"pid={os.getpid()} started_at={utcnow()}\n", encoding="utf-8")


def release_lock() -> None:
    for path in (PID_PATH, LOCK_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def handle_stop(signum, frame) -> None:  # noqa: ANN001
    global RUNNING
    RUNNING = False
    append_log("stop_signal", {"signum": signum})


def run_cmd(cmd: list[str], timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT.parents[0]),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": proc.stdout[-6000:],
        "stderr_tail": proc.stderr[-3000:],
    }


def cycle(args: argparse.Namespace, cycle_n: int) -> dict[str, Any]:
    capture = run_cmd(
        [
            sys.executable,
            str(CAPTURE_SCRIPT),
            "--modes",
            "flash_agentic",
            "--timeout-seconds",
            str(args.capture_timeout_seconds),
            "--serial",
        ],
        timeout=args.capture_timeout_seconds + 90,
    )
    opened = run_cmd(
        [
            sys.executable,
            str(SIM_SCRIPT),
            "open-latest",
            "--take-profit-pct",
            str(args.take_profit_pct),
            "--stop-loss-pct",
            str(args.stop_loss_pct),
            "--max-new",
            str(args.max_new),
        ],
        timeout=90,
    )
    marked = run_cmd([sys.executable, str(SIM_SCRIPT), "mark"], timeout=90)
    status = {
        "mode": "flash_agentic_live_sim",
        "cycle": cycle_n,
        "updated_at": utcnow(),
        "pid": os.getpid(),
        "interval_seconds": args.interval_seconds,
        "take_profit_pct": args.take_profit_pct,
        "stop_loss_pct": args.stop_loss_pct,
        "capture": capture,
        "open_latest": opened,
        "mark": marked,
        "read_only": True,
        "caveat": "Live simulation/alerting only. No real buys, sells, account calls, or order endpoints.",
    }
    write_json(STATUS_PATH, status)
    append_log("cycle", {"cycle": cycle_n, "capture_rc": capture["returncode"], "open_rc": opened["returncode"], "mark_rc": marked["returncode"]})
    return status


def failed_cycle(exc: BaseException, cycle_n: int, args: argparse.Namespace) -> dict[str, Any]:
    status = {
        "mode": "flash_agentic_live_sim",
        "cycle": cycle_n,
        "updated_at": utcnow(),
        "pid": os.getpid(),
        "interval_seconds": args.interval_seconds,
        "error": repr(exc),
        "read_only": True,
        "caveat": "Cycle failed but loop remains alive unless stopped.",
    }
    write_json(STATUS_PATH, status)
    append_log("cycle_error", {"cycle": cycle_n, "error": repr(exc)})
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--capture-timeout-seconds", type=int, default=180)
    parser.add_argument("--take-profit-pct", type=float, default=20.0)
    parser.add_argument("--stop-loss-pct", type=float, default=12.0)
    parser.add_argument("--max-new", type=int, default=5)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means run until stopped.")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    acquire_lock()
    append_log("started", vars(args))
    write_json(STATUS_PATH, {"mode": "flash_agentic_live_sim", "status": "starting", "pid": os.getpid(), "started_at": utcnow()})
    cycle_n = 0
    try:
        while RUNNING:
            cycle_n += 1
            try:
                cycle(args, cycle_n)
            except BaseException as exc:  # noqa: BLE001 - live loop must report and continue.
                failed_cycle(exc, cycle_n, args)
            if args.max_cycles and cycle_n >= args.max_cycles:
                break
            deadline = time.monotonic() + max(5, args.interval_seconds)
            while RUNNING and time.monotonic() < deadline:
                time.sleep(1)
        write_json(STATUS_PATH, {"mode": "flash_agentic_live_sim", "status": "stopped", "pid": os.getpid(), "stopped_at": utcnow(), "cycles": cycle_n})
        append_log("stopped", {"cycles": cycle_n})
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
