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

import gzip
import json
import os
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()

# Backtests are heavy and a user can click twice. One at a time keeps the box
# responsive and stops two runs fighting over the bar cache.
_RUN_LOCK = threading.Lock()

MAX_JOBS = 40
DEFAULT_UNIVERSE = [
    "NVDA", "AAPL", "SPY", "QQQ", "TSLA", "AMD", "META", "MSFT", "AMZN", "GOOGL",
]


def _persist_run(payload: dict, bars: dict[str, list[dict]]) -> dict:
    """Atomically persist the exact inputs and report for deterministic replay."""
    run_id = payload["manifest"]["run_id"]
    root = Path(__file__).resolve().parents[1] / "data" / "backtest_runs"
    root.mkdir(parents=True, exist_ok=True)
    snapshot = root / f"{run_id}.bars.json.gz"
    report = root / f"{run_id}.report.json"
    snapshot_tmp = root / f".{run_id}.bars.tmp.gz"
    report_tmp = root / f".{run_id}.report.tmp.json"
    with gzip.open(snapshot_tmp, "wt", encoding="utf-8") as handle:
        json.dump(bars, handle, sort_keys=True, separators=(",", ":"))
    os.replace(snapshot_tmp, snapshot)
    artifacts = {
        "input_snapshot": str(snapshot.relative_to(root.parents[1])),
        "report": str(report.relative_to(root.parents[1])),
    }
    payload["manifest"]["artifacts"] = artifacts
    with report_tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2, default=str)
    os.replace(report_tmp, report)
    return artifacts


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
    slippage_bps: float = 2.0,
    commission_bps: float = 0.0,
    holdout_fraction: float = 0.30,
    embargo_bars: int = 1,
    seed: int = 17,
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
            root = Path(__file__).resolve().parents[1]
            for path in (str(root), str(root.parent)):
                if path not in sys.path:
                    sys.path.insert(0, path)

            import backtest_engine as be
            import backtest_protocol as bp
            from scripts.run_obsidian_backtest import load_bars

            bars = load_bars(symbols, timeframe, years)
            if not bars:
                _update(job_id, status="error", error="no bars returned for those symbols",
                        message="No data")
                return

            _update(job_id, pct=45,
                    message=f"Loaded {len(bars)} symbols; running the {mode} evaluation…")

            effective_stop = be.DEFAULT_STOP_ATR if stop_atr is None else stop_atr
            effective_target = be.DEFAULT_TARGET_ATR if target_atr is None else target_atr
            effective_hold = be.DEFAULT_MAX_HOLD_BARS if max_hold_bars is None else max_hold_bars
            # `cost_bps` remains a compatibility alias for old direct callers.
            # Product/API runs name slippage and commission separately.
            effective_slippage = float(cost_bps) if cost_bps is not None else slippage_bps
            total_cost_bps = effective_slippage + commission_bps
            spec = bp.experiment_spec(
                mode=mode, symbols=symbols, timeframe=timeframe, years=years,
                detector_mode=detector_mode, lookback_bars=lookback_bars,
                entry_every=entry_every, control_repeats=control_repeats,
                stop_atr=effective_stop, target_atr=effective_target,
                max_hold_bars=effective_hold,
                slippage_bps_per_side=effective_slippage,
                commission_bps_per_side=commission_bps,
                holdout_fraction=holdout_fraction, embargo_bars=embargo_bars,
                seed=seed,
            )
            kw = {
                "cost_bps": total_cost_bps,
                "stop_atr": effective_stop,
                "target_atr": effective_target,
                "max_hold_bars": effective_hold,
            }

            train_bars, holdout_bars, coverage = bp.split_bars(
                bars, holdout_fraction=holdout_fraction,
                embargo_bars=embargo_bars,
            )
            manifest = bp.build_manifest(spec, coverage)

            def evaluate(dataset: dict[str, list[dict]], *, include_control: bool = True) -> dict:
                if mode == "standalone":
                    evaluated = be.run_backtest(
                        dataset, detector_params={"mode": detector_mode}, **kw
                    )
                    trade_returns = [trade.return_pct for trade in evaluated.trades]
                    block_length = spec["validation"]["uncertainty"]["block_length_trades"]
                    out = {
                        "stats": evaluated.stats,
                        "params": evaluated.params,
                        "portfolio": bp.portfolio_summary(evaluated.trades),
                        "trade_ledger": [trade.__dict__ for trade in evaluated.trades],
                        "uncertainty": bp.bootstrap_mean_interval(
                            trade_returns, seed=seed,
                        ),
                        "serial_uncertainty": bp.moving_block_bootstrap_mean_interval(
                            trade_returns, seed=seed, block_length=block_length,
                        ),
                    }
                    if include_control:
                        out["control"] = be.run_control(
                            evaluated, dataset, repeats=control_repeats, seed=seed, **kw
                        )
                    return out
                filtered = be.run_filter(
                    dataset,
                    detector_params={"mode": detector_mode},
                    lookback_bars=lookback_bars,
                    entry_every=entry_every,
                    control_repeats=control_repeats,
                    seed=seed,
                    **kw,
                )
                return filtered

            if mode == "standalone":
                _update(job_id, pct=75, message="Running the matched random control…")
                full = evaluate(bars)
                payload = {
                    "mode": "standalone",
                    **full,
                    "caveat": be.BacktestResult(strategy="obsidian", symbols=[]).caveat,
                }
            else:
                payload = evaluate(bars)
                payload["mode"] = "filter"

            _update(job_id, pct=86, message="Evaluating locked train and holdout partitions…")
            validation = {"status": manifest["validation_status"]}
            if train_bars and holdout_bars:
                validation["train"] = evaluate(train_bars)
                validation["holdout"] = evaluate(holdout_bars)
            else:
                validation["blocker"] = (
                    "No symbol had at least 120 bars in both chronological partitions. "
                    "Lengthen history or use a finer timeframe."
                )

            payload["symbols"] = sorted(bars)
            payload["timeframe"] = timeframe
            payload["years"] = years
            payload["detector_mode"] = detector_mode
            payload["manifest"] = manifest
            payload["experiment_id"] = manifest["experiment_id"]
            payload["run_id"] = manifest["run_id"]
            payload["validation"] = validation
            payload["elapsed_ms"] = int((time.time() - started) * 1000)
            payload["artifacts"] = _persist_run(payload, bars)
            _update(job_id, status="done", pct=100, result=payload, message="Backtest complete")
        except Exception as exc:  # noqa: BLE001 - surface the failure to the UI
            _update(job_id, status="error", error=f"{type(exc).__name__}: {exc}",
                    message=str(exc)[:200])
        finally:
            _RUN_LOCK.release()

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def start_catalog_job(
    *,
    strategy_ids: list[str] | None = None,
    family: str | None = None,
    symbols: list[str] | None = None,
    timeframe: str = "1Day",
    years: float = 5.0,
    control_repeats: int = 20,
    use_measured_cost: bool = True,
) -> str:
    """Evaluate catalogued strategies against the one standard.

    Shares the registry, status vocabulary and polling contract of
    `start_backtest_job` above, and the same `_RUN_LOCK`, so a catalog sweep and a
    signal backtest cannot fight over the bar cache or double the memory.

    `timeframe` is a filter as well as a fetch parameter: a strategy declares the
    bar size it was written against, and one written for intraday bars is reported
    WRONG_TIMEFRAME rather than being fed daily bars and blamed for finding
    nothing.
    """
    job_id = uuid.uuid4().hex[:12]
    symbols = [s.strip().upper() for s in (symbols or DEFAULT_UNIVERSE) if s.strip()]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "mode": "catalog",
            "symbols": symbols,
            "timeframe": timeframe,
            "years": years,
            "family": family,
            "pct": 0,
            "message": "Queued…",
            "result": None,
            "error": None,
            "events": [],
            "started_at": _utcnow(),
            "updated_at": _utcnow(),
        }
    _prune()

    def _worker() -> None:
        if not _RUN_LOCK.acquire(blocking=False):
            _update(job_id, status="running",
                    message="Waiting for the running backtest to finish…")
            _RUN_LOCK.acquire()
        try:
            started = time.time()
            import sys
            from pathlib import Path

            root = Path(__file__).resolve().parents[1]
            for path in (str(root), str(root / "core")):
                if path not in sys.path:
                    sys.path.insert(0, path)

            import strategy_catalog as catalog
            import strategy_evaluation as evaluation
            from scripts.run_obsidian_backtest import load_bars

            ids = strategy_ids
            if not ids:
                ids = [
                    spec.strategy_id for spec in catalog.CATALOG.values()
                    if (family is None or spec.family == family)
                    and (not spec.evaluable or spec.bar_timeframe == timeframe)
                ]

            _update(job_id, status="running", pct=5,
                    message=f"Loading {len(symbols)} symbols of {timeframe} bars…")
            bars = load_bars(symbols, timeframe, years)
            if not bars:
                _update(job_id, status="error", message="No data",
                        error="no bars returned for those symbols")
                return

            profile = None
            if use_measured_cost:
                try:
                    import execution_cost
                    profile = execution_cost.load_profile()
                except Exception:  # noqa: BLE001 - fall back to the assumed cost
                    profile = None

            def progress(index, total, strategy_id, phase="started", verdict=None):
                if phase == "started":
                    _update(job_id, pct=10 + int(85 * index / max(total, 1)),
                            message=f"{index + 1}/{total}  {strategy_id}")
                _append_event(job_id, {
                    "type": "strategy_done" if phase == "done" else "strategy_started",
                    "strategy_id": strategy_id,
                    "index": index,
                    "total": total,
                    "verdict": verdict,
                })

            payload = evaluation.evaluate_all(
                bars, strategy_ids=ids, control_repeats=control_repeats,
                cost_profile=profile, timeframe=timeframe, progress=progress,
            )
            payload["cost_source"] = "measured" if profile else "assumed 2.0bps/side"
            payload["elapsed_ms"] = int((time.time() - started) * 1000)
            _update(job_id, status="done", pct=100, result=payload,
                    message=f"Evaluated {len(payload.get('results') or [])} strategies")
        except Exception as exc:  # noqa: BLE001 - surface the failure to the UI
            _update(job_id, status="error", error=f"{type(exc).__name__}: {exc}",
                    message=str(exc)[:200])
        finally:
            _RUN_LOCK.release()

    threading.Thread(target=_worker, daemon=True).start()
    return job_id
