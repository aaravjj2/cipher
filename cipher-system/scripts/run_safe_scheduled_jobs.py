#!/usr/bin/env python3
"""Run only individually eligible, read-only operational jobs on bounded cadences."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
GOV = ROOT / "data" / "governance"
STATE = GOV / "safe_scheduled_jobs_state.json"
LOCK = GOV / "safe_scheduled_jobs.lock"


@dataclass(frozen=True)
class Job:
    job_id: str
    cadence: timedelta
    command: tuple[str, ...]
    prerequisite: Callable[[], str | None]


def no_blocker() -> str | None:
    return None


def event_artifact_blocker() -> str | None:
    return None if any((ROOT / "data" / "events").glob("public_event_ingestion_*.json")) else "public_event_artifact_missing"


def jobs() -> tuple[Job, ...]:
    python = str(Path(sys.executable).resolve())
    return (
        Job(
            "public_event_ingestion",
            timedelta(days=1),
            (python, str(ROOT / "scripts" / "ingest_public_events.py"), "--days", "7", "--max-per-symbol", "3"),
            no_blocker,
        ),
        Job(
            "bounded_repair_audit",
            timedelta(days=1),
            (python, str(ROOT / "scripts" / "run_bounded_repairs.py")),
            event_artifact_blocker,
        ),
        Job(
            "research_infrastructure_audit",
            timedelta(days=1),
            (python, str(ROOT / "scripts" / "audit_research_infrastructure.py"), "--offline"),
            no_blocker,
        ),
        Job(
            "master_end_state_refresh",
            timedelta(hours=1),
            (python, str(ROOT / "scripts" / "update_master_end_state_status.py")),
            no_blocker,
        ),
    )


def bootstrap_state() -> dict:
    jobs_state: dict[str, dict] = {}
    artifact_patterns = {
        "public_event_ingestion": ROOT / "data" / "events" / "public_event_ingestion_*.json",
        "bounded_repair_audit": ROOT / "data" / "governance" / "bounded_repair_run_*.json",
        "research_infrastructure_audit": ROOT / "data" / "governance" / "research_infrastructure_audit_*.json",
        "master_end_state_refresh": ROOT / "data" / "governance" / "master_end_state_status_*.json",
    }
    for job_id, pattern in artifact_patterns.items():
        candidates = sorted(pattern.parent.glob(pattern.name))
        if not candidates:
            continue
        latest = candidates[-1]
        completed = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).isoformat()
        jobs_state[job_id] = {
            "job_id": job_id,
            "status": "seeded_from_existing_artifact",
            "last_completed_at": completed,
            "artifact": str(latest),
            "live_execution": False,
        }
    return {"schema_version": 1, "jobs": jobs_state, "execution_authority": False, "seeded_from_existing_artifacts": True}


def read_state() -> dict:
    if not STATE.is_file():
        return bootstrap_state()
    try:
        payload = json.loads(STATE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"schema_version": 1, "jobs": {}}
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "jobs": {}, "state_recovered_from_invalid_file": True}


def write_state(payload: dict) -> None:
    GOV.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, STATE)


def due(last_completed: str | None, cadence: timedelta, now: datetime) -> bool:
    if not last_completed:
        return True
    try:
        previous = datetime.fromisoformat(last_completed)
    except ValueError:
        return True
    if previous.tzinfo is None:
        return True
    return now - previous.astimezone(timezone.utc) >= cadence


def run_once() -> dict:
    GOV.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "skipped_locked", "jobs_run": 0}
        state = read_state()
        records = state.setdefault("jobs", {})
        now = datetime.now(timezone.utc)
        events = []
        for job in jobs():
            current = records.get(job.job_id, {})
            blocker = job.prerequisite()
            if blocker:
                current.update({
                    "job_id": job.job_id,
                    "status": "blocked",
                    "blocker": blocker,
                    "checked_at": now.isoformat(),
                    "live_execution": False,
                })
                records[job.job_id] = current
                events.append(dict(current))
                continue
            if not due(current.get("last_completed_at"), job.cadence, now):
                current.update({"job_id": job.job_id, "status": "not_due", "checked_at": now.isoformat(), "live_execution": False})
                records[job.job_id] = current
                continue
            started = datetime.now(timezone.utc)
            try:
                completed = subprocess.run(
                    job.command,
                    cwd=ROOT.parent,
                    capture_output=True,
                    text=True,
                    timeout=900,
                    check=False,
                    env={**os.environ, "HF_HOME": os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))},
                )
                status = "completed" if completed.returncode == 0 else "failed"
                record = {
                    "job_id": job.job_id,
                    "status": status,
                    "blocker": None,
                    "started_at": started.isoformat(),
                    "last_completed_at": datetime.now(timezone.utc).isoformat() if completed.returncode == 0 else current.get("last_completed_at"),
                    "returncode": completed.returncode,
                    "stdout_tail": completed.stdout[-4000:],
                    "stderr_tail": completed.stderr[-4000:],
                    "command": list(job.command),
                    "cadence_seconds": int(job.cadence.total_seconds()),
                    "live_execution": False,
                }
            except Exception as exc:
                record = {
                    "job_id": job.job_id,
                    "status": "failed",
                    "blocker": None,
                    "started_at": started.isoformat(),
                    "last_completed_at": current.get("last_completed_at"),
                    "error": f"{type(exc).__name__}: {exc}",
                    "command": list(job.command),
                    "cadence_seconds": int(job.cadence.total_seconds()),
                    "live_execution": False,
                }
            records[job.job_id] = record
            events.append(record)
            write_state({
                **state,
                "schema_version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "jobs": records,
                "allowed_job_ids": [item.job_id for item in jobs()],
                "excluded_job_classes": ["factor_research", "model_study", "backtesting", "paper_trading", "live_execution"],
                "execution_authority": False,
            })
        result = {
            **state,
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "jobs": records,
            "last_run": events,
            "allowed_job_ids": [item.job_id for item in jobs()],
            "excluded_job_classes": ["factor_research", "model_study", "backtesting", "paper_trading", "live_execution"],
            "execution_authority": False,
        }
        write_state(result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()
    if not args.loop:
        result = run_once()
        print(json.dumps(result.get("last_run", result), indent=2))
        return 0
    interval = max(300, int(args.interval_seconds))
    while True:
        result = run_once()
        print(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "status": result.get("status", "complete"), "jobs_run": len(result.get("last_run", []))}), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
