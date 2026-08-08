"""Background job wrapper so backtests can be launched from the UI.

`core/backtest_engine.py` produced every result in docs/backtest-findings.md —
three strategies rejected, one partition flagged promising — and none of it was
reachable from the product. It ran only from the command line, so the tool's most
valuable capability lived in terminal scrollback.

A backtest is minutes of CPU (bar fetch, detector pass over ~16k bars per symbol,
then a matched random control repeated 20 times), which is far too long for a
blocking request. This mirrors `scanner.start_scan_job` exactly rather than
inventing a second job idiom: same registry shape, same status vocabulary, same
polling contract, so the frontend's existing job-polling pattern applies unchanged.

Research-only. Simulated fills over historical bars; places no orders.
"""
from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

# Backtests are heavy and a user can click twice. One at a time keeps the box
# responsive and stops two runs fighting over the bar cache.
_RUN_LOCK = threading.Lock()

MAX_JOBS = 40
DEFAULT_UNIVERSE = [
    "NVDA", "AAPL", "SPY", "QQQ", "TSLA", "AMD", "META", "MSFT", "AMZN", "GOOGL",
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _update(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.update(fields)
            job["updated_at"] = _utcnow()


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return deepcopy(job) if job else None


def list_jobs(limit: int = 20) -> list[dict]:
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j.get("started_at") or "", reverse=True)
        return [
            {k: v for k, v in deepcopy(job).items() if k != "result"}
            for job in jobs[:limit]
        ]


def _prune() -> None:
    with _LOCK:
        if len(_JOBS) <= MAX_JOBS:
            return
        for job_id in sorted(
            _JOBS, key=lambda k: _JOBS[k].get("started_at") or ""
        )[: len(_JOBS) - MAX_JOBS]:
            _JOBS.pop(job_id, None)


def start_backtest_job(
    *,
    mode: str = "filter",
    symbols: list[str] | None = None,
    timeframe: str = "15Min",
    years: float = 1.0,
    detector_mode: str = "EOD Focus",
    lookback_bars: int = 6,
    entry_every: int = 12,
    control_repeats: int = 20,
    stop_atr: float | None = None,
    target_atr: float | None = None,
    max_hold_bars: int | None = None,
    cost_bps: float | None = None,
) -> str:
    """Queue a backtest. `mode` is 'filter' or 'standalone'."""
    symbols = [s.strip().upper() for s in (symbols or DEFAULT_UNIVERSE) if s.strip()]
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "mode": mode,
            "symbols": symbols,
            "timeframe": timeframe,
            "years": years,
            "detector_mode": detector_mode,
            "pct": 0,
            "message": "Queued…",
            "result": None,
            "error": None,
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
        }
    _prune()

    def _worker() -> None:
        # Serialised: a second run would double the memory and halve the speed of
        # both, and the bar cache is shared.
        if not _RUN_LOCK.acquire(blocking=False):
            _update(job_id, status="running", message="Waiting for the running backtest to finish…")
            _RUN_LOCK.acquire()
        try:
            started = time.time()
            _update(job_id, status="running", pct=5,
                    message=f"Loading {len(symbols)} symbols of {timeframe} bars…")

            import sys
            from pathlib import Path

            root = Path(__file__).resolve().parents[1]
            for path in (str(root), str(root.parent)):
                if path not in sys.path:
                    sys.path.insert(0, path)

            import backtest_engine as be
            from scripts.run_obsidian_backtest import load_bars

            bars = load_bars(symbols, timeframe, years)
            if not bars:
                _update(job_id, status="error", error="no bars returned for those symbols",
                        message="No data")
                return

            _update(job_id, pct=45,
                    message=f"Loaded {len(bars)} symbols; running the {mode} evaluation…")

            kw = {"cost_bps": cost_bps} if cost_bps is not None else {}
            for name, value in (("stop_atr", stop_atr), ("target_atr", target_atr),
                                ("max_hold_bars", max_hold_bars)):
                if value is not None:
                    kw[name] = value

            if mode == "standalone":
                result = be.run_backtest(
                    bars, detector_params={"mode": detector_mode}, **kw
                )
                _update(job_id, pct=75, message="Running the matched random control…")
                control = be.run_control(result, bars, repeats=control_repeats, **kw)
                payload = {
                    "mode": "standalone",
                    "stats": result.stats,
                    "params": result.params,
                    "control": control,
                    "caveat": result.caveat,
                }
            else:
                payload = be.run_filter(
                    bars,
                    detector_params={"mode": detector_mode},
                    lookback_bars=lookback_bars,
                    entry_every=entry_every,
                    control_repeats=control_repeats,
                    **kw,
                )
                payload["mode"] = "filter"

            payload["symbols"] = sorted(bars)
            payload["timeframe"] = timeframe
            payload["years"] = years
            payload["detector_mode"] = detector_mode
            payload["elapsed_ms"] = int((time.time() - started) * 1000)
            _update(job_id, status="done", pct=100, result=payload, message="Backtest complete")
        except Exception as exc:  # noqa: BLE001 - surface the failure to the UI
            _update(job_id, status="error", error=f"{type(exc).__name__}: {exc}",
                    message=str(exc)[:200])
        finally:
            _RUN_LOCK.release()

    threading.Thread(target=_worker, daemon=True).start()
    return job_id
