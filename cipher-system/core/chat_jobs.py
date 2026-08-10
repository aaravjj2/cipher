"""Background job wrapper for Ask Cipher chat turns, so the browser can poll/
stream progress instead of blocking on a request for as long as Claude takes
to answer. Mirrors core/backtest_jobs.py's registry shape exactly (same
_JOBS/_LOCK, get_job, _append_event, one daemon thread per job) rather than
sharing its dict -- chat jobs carry a different event vocabulary
(tool_call/text_delta/done/error) and don't compete with backtests for the bar
cache, so there's no reason to serialize them against each other.

Research only. No broker/account/order APIs are imported or called.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Callable

import ask_cipher

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

MAX_JOBS = 40


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _update(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.update(fields)
            job["updated_at"] = _utcnow()


def _append_event(job_id: str, event: dict) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.setdefault("events", []).append(event)


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _prune() -> None:
    with _LOCK:
        if len(_JOBS) <= MAX_JOBS:
            return
        for job_id in sorted(_JOBS, key=lambda k: _JOBS[k].get("started_at") or "")[: len(_JOBS) - MAX_JOBS]:
            _JOBS.pop(job_id, None)


def start_chat_job(message: str, history: list[dict], tool_impls: dict[str, Callable]) -> str:
    ask_cipher.check_and_record_usage()  # raises AskCipherError (ValueError) synchronously if over the daily cap
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "message": "Queued…",
            "error": None,
            "events": [],
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
        }
    _prune()

    def _worker() -> None:
        _update(job_id, status="running", message="Asking Cipher…")

        def append_event(event: dict) -> None:
            if event.get("type") == "error":
                _update(job_id, status="error", error=event.get("error"), message="Error")
            _append_event(job_id, event)

        try:
            ask_cipher.run_chat_job(message, history, tool_impls, append_event)
        except Exception as exc:  # noqa: BLE001 - a bug in the worker must still reach a terminal state
            append_event({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
            return
        job = get_job(job_id)
        if job and job.get("status") != "error":
            _update(job_id, status="done", message="Done")

    threading.Thread(target=_worker, daemon=True).start()
    return job_id
