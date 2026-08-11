"""Serialized, allow-listed background jobs for historical option research labs."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOLS = {
    "weekly_bullish_debit": "run_weekly_bullish_debit_option_lab.py",
    "weekly_bearish_debit": "run_weekly_bearish_debit_option_lab.py",
    "weekly_low_capital": "run_weekly_low_capital_option_lab.py",
    "fixed_width": "run_fixed_width_option_strategy_lab.py",
    "capital_efficient": "run_capital_efficient_multi_stock_option_lab.py",
}
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def protocols() -> list[dict]:
    return [{"id": key, "script": value, "research_only": True} for key, value in PROTOCOLS.items()]


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        return deepcopy(_JOBS.get(job_id))


def list_jobs() -> list[dict]:
    with _LOCK:
        return [deepcopy(job) for job in sorted(_JOBS.values(), key=lambda row: row["created_at"], reverse=True)[:20]]


def _update(job_id: str, **values) -> None:
    with _LOCK:
        _JOBS[job_id].update(values)
        _JOBS[job_id]["updated_at"] = _now()


def _run(job_id: str, *, run=subprocess.run) -> None:
    job = get_job(job_id)
    if not job:
        return
    with _RUN_LOCK:
        _update(job_id, status="running", message="Historical options lab is running", pct=10)
        script = ROOT / "scripts" / PROTOCOLS[job["protocol"]]
        try:
            completed = run(
                [sys.executable, str(script)], cwd=ROOT, capture_output=True, text=True,
                timeout=6 * 60 * 60, check=False,
            )
            stdout = completed.stdout[-200_000:]
            stderr = completed.stderr[-20_000:]
            if completed.returncode:
                _update(job_id, status="error", pct=100, message="Lab failed", error=stderr or stdout)
                return
            try:
                result = json.loads(stdout)
            except json.JSONDecodeError:
                result = {"output": stdout}
            _update(job_id, status="done", pct=100, message="Lab complete", result=result, error=None)
        except Exception as exc:  # noqa: BLE001 - surfaced in bounded job payload
            _update(job_id, status="error", pct=100, message="Lab failed", error=f"{type(exc).__name__}: {exc}")


def start_job(protocol: str) -> str:
    if protocol not in PROTOCOLS:
        raise ValueError("unknown options backtest protocol")
    job_id = uuid.uuid4().hex[:12]
    now = _now()
    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "protocol": protocol, "status": "queued", "pct": 0,
                         "message": "Queued", "result": None, "error": None,
                         "created_at": now, "updated_at": now, "research_only": True,
                         "execution_capability": False}
    threading.Thread(target=_run, args=(job_id,), daemon=True).start()
    return job_id
