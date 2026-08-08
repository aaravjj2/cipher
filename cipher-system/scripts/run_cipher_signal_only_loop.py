#!/usr/bin/env python3
"""Fingerprint-driven loop for independent Cipher source research.

Active products:
- Cluster: every unique episode scored independently through its own expiry.
- Flash: standalone next-open fixed-horizon analysis.
- Agentic: standalone next-open fixed-horizon analysis.

Combined votes, confirmations, vetoes, lead-lag rankings, and cross-source
candidate rules are not run by this loop. Older combined artifacts remain
frozen references only.
"""
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

from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402

GOV = ROOT / "data" / "governance" / "cipher_signal_only"
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
INDEPENDENT_REPORT = GOV / "latest_independent_signal_analysis.json"
CLUSTER_REPORT = GOV / "latest_cluster_individual_analysis.json"
TRADE_REPORT = GOV / "latest_cluster_trade_candidates.json"
STRATEGY_REPORT = ROOT / "data" / "governance" / "cipher_strategy" / "latest_strategy_decision.json"
POSTMORTEM_REPORT = ROOT / "data" / "governance" / "cipher_strategy" / "latest_trade_postmortems.json"
EVENT_REPORT = ROOT / "data" / "events" / "latest_public_event_ingestion.json"
CYCLE = GOV / "latest_signal_research_cycle.json"
STATE = GOV / "signal_research_loop_state.json"
LOCK = GOV / "signal_research_loop.lock"
INDEPENDENT_SCRIPT = ROOT / "scripts" / "run_cipher_independent_signal_analysis.py"
CLUSTER_SCRIPT = ROOT / "scripts" / "run_cipher_cluster_individual_analysis.py"
TRADE_BUILDER_SCRIPT = ROOT / "scripts" / "run_cipher_cluster_trade_builder.py"
STRATEGY_SCRIPT = ROOT / "scripts" / "run_cipher_cluster_strategy.py"
POSTMORTEM_SCRIPT = ROOT / "scripts" / "run_cipher_trade_postmortems.py"
EVENT_SCRIPT = ROOT / "scripts" / "ingest_public_events.py"
HELPER_SCRIPT = ROOT / "scripts" / "run_cipher_complete_observations.py"
OVERLAY_MODULE = ROOT / "core" / "research_platform" / "cipher_signal_overlay.py"
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


def nested(row: dict[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def cluster_capture_manifest() -> list[dict[str, Any]]:
    """Fingerprint only persisted Cluster scan files.

    Flash and Agentic may update more frequently than the 45-minute Cluster
    schedule. They are read as context when a new Cluster scan arrives, but they
    do not independently trigger the expensive strategy cycle.
    """

    manifest: list[dict[str, Any]] = []
    for path in sorted(CAPTURE_ROOT.glob("cluster-scans-v2-*.jsonl")):
        stat = path.stat()
        manifest.append(
            {
                "path": str(path.relative_to(ROOT)),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256_file(path),
            }
        )
    return manifest


def operational_fingerprint() -> tuple[str, dict[str, Any]]:
    inputs = {
        "cluster_capture_manifest": cluster_capture_manifest(),
        "independent_signal_script_sha256": sha256_file(INDEPENDENT_SCRIPT),
        "cluster_individual_script_sha256": sha256_file(CLUSTER_SCRIPT),
        "cluster_trade_builder_script_sha256": sha256_file(TRADE_BUILDER_SCRIPT),
        "cluster_strategy_script_sha256": sha256_file(STRATEGY_SCRIPT),
        "trade_postmortem_script_sha256": sha256_file(POSTMORTEM_SCRIPT),
        "public_event_script_sha256": sha256_file(EVENT_SCRIPT),
        "expiry_helper_script_sha256": sha256_file(HELPER_SCRIPT),
        "overlay_module_sha256": sha256_file(OVERLAY_MODULE),
        "source_mode": "cluster_triggered_independent_analysis_plus_strategy_context",
    }
    return stable_id("cipher_cluster_triggered_strategy_loop_inputs", inputs, length=64), inputs


def compact_report(
    independent: dict[str, Any],
    cluster: dict[str, Any],
    trade_board: dict[str, Any],
    strategy: dict[str, Any],
    postmortems: dict[str, Any],
) -> dict[str, Any]:
    sources = independent.get("sources") if isinstance(independent.get("sources"), dict) else {}
    cluster_summary = cluster.get("summary") if isinstance(cluster.get("summary"), dict) else {}
    current_trade_board = (
        trade_board.get("current_trade_board")
        if isinstance(trade_board.get("current_trade_board"), dict)
        else {}
    )
    delayed_research = (
        trade_board.get("delayed_entry_research")
        if isinstance(trade_board.get("delayed_entry_research"), dict)
        else {}
    )

    def source_summary(name: str) -> dict[str, Any]:
        payload = sources.get(name) if isinstance(sources.get(name), dict) else {}
        episodes = payload.get("episodes") if isinstance(payload.get("episodes"), dict) else {}
        states = payload.get("terminal_states") if isinstance(payload.get("terminal_states"), dict) else {}
        scoring = payload.get("forward_scoring") if isinstance(payload.get("forward_scoring"), dict) else {}
        return {
            "episodes": episodes.get("total"),
            "eligible_regular_session_episodes": episodes.get("eligible_regular_session"),
            "terminal_states": states.get("count"),
            "matured": scoring.get("matured"),
            "pending": scoring.get("pending"),
            "unscorable": scoring.get("unscorable"),
            "by_direction_horizon": scoring.get("by_direction_horizon"),
            "by_setup_horizon": scoring.get("by_setup_horizon"),
            "by_score_bucket_horizon": scoring.get("by_score_bucket_horizon"),
            "by_ticker_horizon": scoring.get("by_ticker_horizon"),
        }

    return {
        "mode": "independent_source_analysis_only",
        "cross_source_logic_active": False,
        "flash": source_summary("flash"),
        "agentic": source_summary("flash_agentic"),
        "cluster": {
            "analysis_unit": cluster_summary.get("analysis_unit"),
            "total_episodes": cluster_summary.get("total_cluster_episodes"),
            "eligible_regular_session_episodes": cluster_summary.get("eligible_regular_session_episodes"),
            "excluded_episodes": cluster_summary.get("excluded_episodes"),
            "matured_at_expiry": cluster_summary.get("matured_at_expiry"),
            "pending_expiry": cluster_summary.get("pending_expiry"),
            "unscorable": cluster_summary.get("unscorable"),
            "latest_completed_market_session": cluster_summary.get("latest_completed_market_session"),
            "by_direction": cluster_summary.get("by_direction"),
            "by_rank_bucket": cluster_summary.get("by_rank_bucket"),
            "by_strength_bucket": cluster_summary.get("by_strength_bucket"),
            "by_target_distance_bucket": cluster_summary.get("by_target_distance_bucket"),
            "by_signal_time_bucket": cluster_summary.get("by_signal_time_bucket"),
            "by_research_tier": cluster_summary.get("by_research_tier"),
            "by_appearance_bucket": cluster_summary.get("by_appearance_bucket"),
            "by_ticker": cluster_summary.get("by_ticker"),
            "finalized_at_expiry": cluster_summary.get("finalized_at_expiry"),
            "pending_mark_to_latest": cluster_summary.get("pending_mark_to_latest"),
            "option_path_diagnostics": cluster_summary.get("option_path_diagnostics"),
        },
        "cluster_trade_board": {
            "report_id": trade_board.get("report_id"),
            "latest_signal_session": trade_board.get("latest_signal_session"),
            "latest_completed_market_session": trade_board.get("latest_completed_market_session"),
            "status_counts": current_trade_board.get("status_counts"),
            "candidate_count": len(current_trade_board.get("candidates") or []),
            "top_candidates": (current_trade_board.get("candidates") or [])[:8],
            "delayed_entry_all": delayed_research.get("all_first_qualifying_tier_a"),
            "delayed_entry_persisted": delayed_research.get("persisted_tier_a_to_last_same_day_capture"),
        },
        "cluster_strategy": {
            "strategy_id": strategy.get("strategy_id"),
            "latest_signal_session": strategy.get("latest_signal_session"),
            "eligible_candidates": strategy.get("eligible_candidates"),
            "overlay_coverage": strategy.get("overlay_coverage"),
            "news_context_status": strategy.get("news_context_status"),
            "input_snapshot": strategy.get("input_snapshot"),
            "execution_authority": False,
        },
        "trade_postmortems": {
            "report_id": postmortems.get("report_id"),
            "created_at": postmortems.get("created_at"),
            "trades": nested(postmortems, "summary", "trades"),
            "unique_tickers": nested(postmortems, "summary", "unique_tickers"),
            "matured_at_expiry": nested(postmortems, "summary", "matured_at_expiry"),
            "pending_expiry": nested(postmortems, "summary", "pending_expiry"),
            "attribution_counts": nested(postmortems, "summary", "attribution_counts"),
            "outcome_counts": nested(postmortems, "summary", "outcome_counts"),
            "uncertainty_counts": nested(postmortems, "summary", "uncertainty_counts"),
            "flash_available_at_signal": nested(postmortems, "summary", "flash_available_at_signal"),
            "agentic_available_at_signal": nested(postmortems, "summary", "agentic_available_at_signal"),
            "execution_authority": False,
        },
    }


def current_candidate_symbols(limit: int = 14) -> tuple[str, ...]:
    payload = read_json(TRADE_REPORT)
    board = payload.get("current_trade_board") if isinstance(payload.get("current_trade_board"), dict) else {}
    candidates = board.get("candidates") if isinstance(board.get("candidates"), list) else []
    symbols: list[str] = []
    for row in candidates:
        ticker = str(row.get("ticker") or "").strip().upper() if isinstance(row, dict) else ""
        if ticker and ticker not in symbols:
            symbols.append(ticker)
        if len(symbols) >= limit:
            break
    return tuple(symbols)


def run_process(
    script: Path,
    *,
    timeout: int,
    args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(Path(sys.executable).absolute()), str(script), *args],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "CIPHER_INDEPENDENT_SOURCE_RESEARCH": "1"},
    )


def run_once(*, force: bool = False) -> dict[str, Any]:
    GOV.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "schema_version": 2,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "skipped_locked",
                "mode": "independent_source_analysis_only",
                "execution_authority": False,
            }

        fingerprint, inputs = operational_fingerprint()
        state = read_json(STATE)
        should_run = bool(
            force
            or not INDEPENDENT_REPORT.is_file()
            or not CLUSTER_REPORT.is_file()
            or not TRADE_REPORT.is_file()
            or not STRATEGY_REPORT.is_file()
            or not POSTMORTEM_REPORT.is_file()
            or state.get("operational_fingerprint") != fingerprint
        )
        process: dict[str, Any] | None = None
        if should_run:
            started = datetime.now(timezone.utc)
            frozen_completed = run_process(STRATEGY_SCRIPT, timeout=60, args=("--freeze-only",))
            independent_completed = None
            cluster_completed = None
            trade_builder_completed = None
            event_completed = None
            strategy_completed = None
            postmortem_completed = None
            if frozen_completed.returncode == 0:
                independent_completed = run_process(
                    INDEPENDENT_SCRIPT,
                    timeout=900,
                    args=("--workers", "4"),
                )
            if independent_completed is not None and independent_completed.returncode == 0 and INDEPENDENT_REPORT.is_file():
                cluster_completed = run_process(
                    CLUSTER_SCRIPT,
                    timeout=1800,
                    args=("--workers", "4"),
                )
            if cluster_completed is not None and cluster_completed.returncode == 0 and CLUSTER_REPORT.is_file():
                trade_builder_completed = run_process(
                    TRADE_BUILDER_SCRIPT,
                    timeout=1800,
                    args=("--workers", "4"),
                )
            if trade_builder_completed is not None and trade_builder_completed.returncode == 0 and TRADE_REPORT.is_file():
                symbols = current_candidate_symbols()
                if symbols:
                    event_completed = run_process(
                        EVENT_SCRIPT,
                        timeout=1200,
                        args=(
                            "--days",
                            "7",
                            "--max-per-symbol",
                            "4",
                            "--symbols",
                            ",".join(symbols),
                        ),
                    )
                strategy_completed = run_process(STRATEGY_SCRIPT, timeout=300)
            if strategy_completed is not None and strategy_completed.returncode == 0 and STRATEGY_REPORT.is_file():
                postmortem_completed = run_process(
                    POSTMORTEM_SCRIPT,
                    timeout=1800,
                    args=("--workers", "4"),
                )
            process = {
                "started_at": started.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "freeze_returncode": frozen_completed.returncode,
                "freeze_stdout_tail": frozen_completed.stdout[-8000:],
                "freeze_stderr_tail": frozen_completed.stderr[-8000:],
                "independent_returncode": independent_completed.returncode if independent_completed else None,
                "independent_stdout_tail": independent_completed.stdout[-8000:] if independent_completed else None,
                "independent_stderr_tail": independent_completed.stderr[-8000:] if independent_completed else None,
                "cluster_returncode": cluster_completed.returncode if cluster_completed else None,
                "cluster_stdout_tail": cluster_completed.stdout[-8000:] if cluster_completed else None,
                "cluster_stderr_tail": cluster_completed.stderr[-8000:] if cluster_completed else None,
                "trade_builder_returncode": trade_builder_completed.returncode if trade_builder_completed else None,
                "trade_builder_stdout_tail": trade_builder_completed.stdout[-8000:] if trade_builder_completed else None,
                "trade_builder_stderr_tail": trade_builder_completed.stderr[-8000:] if trade_builder_completed else None,
                "event_returncode": event_completed.returncode if event_completed else None,
                "event_stdout_tail": event_completed.stdout[-8000:] if event_completed else None,
                "event_stderr_tail": event_completed.stderr[-8000:] if event_completed else None,
                "event_context_degraded": bool(event_completed is None or event_completed.returncode != 0),
                "strategy_returncode": strategy_completed.returncode if strategy_completed else None,
                "strategy_stdout_tail": strategy_completed.stdout[-8000:] if strategy_completed else None,
                "strategy_stderr_tail": strategy_completed.stderr[-8000:] if strategy_completed else None,
                "postmortem_returncode": postmortem_completed.returncode if postmortem_completed else None,
                "postmortem_stdout_tail": postmortem_completed.stdout[-8000:] if postmortem_completed else None,
                "postmortem_stderr_tail": postmortem_completed.stderr[-8000:] if postmortem_completed else None,
            }
            status = (
                "completed"
                if frozen_completed.returncode == 0
                and independent_completed is not None
                and independent_completed.returncode == 0
                and cluster_completed is not None
                and cluster_completed.returncode == 0
                and trade_builder_completed is not None
                and trade_builder_completed.returncode == 0
                and strategy_completed is not None
                and strategy_completed.returncode == 0
                and postmortem_completed is not None
                and postmortem_completed.returncode == 0
                and INDEPENDENT_REPORT.is_file()
                and CLUSTER_REPORT.is_file()
                and TRADE_REPORT.is_file()
                and STRATEGY_REPORT.is_file()
                and POSTMORTEM_REPORT.is_file()
                else "failed"
            )
        else:
            status = "not_due_inputs_unchanged"

        independent = read_json(INDEPENDENT_REPORT)
        cluster = read_json(CLUSTER_REPORT)
        trade_board = read_json(TRADE_REPORT)
        strategy = read_json(STRATEGY_REPORT)
        postmortems = read_json(POSTMORTEM_REPORT)
        payload = {
            "schema_version": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "mode": "independent_source_analysis_only",
            "operational_fingerprint": fingerprint,
            "inputs": inputs,
            "process": process,
            "independent_signal_report_path": str(INDEPENDENT_REPORT),
            "cluster_individual_report_path": str(CLUSTER_REPORT),
            "cluster_trade_report_path": str(TRADE_REPORT),
            "cluster_strategy_report_path": str(STRATEGY_REPORT),
            "trade_postmortem_report_path": str(POSTMORTEM_REPORT),
            "public_event_report_path": str(EVENT_REPORT),
            "summary": compact_report(independent, cluster, trade_board, strategy, postmortems),
            "active_source_boundaries": {
                "cluster": "individual episodes only",
                "flash": "standalone source only",
                "agentic": "standalone source only",
                "combined_votes": False,
                "confirmations": False,
                "vetoes": False,
                "lead_lag": False,
                "auxiliary_strategy_context": True,
                "flash_agentic_trade_gate": False,
                "news_directional_trade_gate": False,
            },
            "frozen_reference_artifacts_not_run": [
                str(GOV / "latest_signal_research.json"),
                str(GOV / "latest_ticker_strategy_specifics.json"),
                str(GOV / "latest_complete_observations.json"),
            ],
            "automatic_promotion": False,
            "paper_or_live_execution": False,
            "execution_authority": False,
        }
        atomic_json(CYCLE, payload)
        if status != "failed":
            atomic_json(
                STATE,
                {
                    "schema_version": 2,
                    "updated_at": payload["created_at"],
                    "operational_fingerprint": fingerprint,
                    "latest_status": status,
                    "mode": payload["mode"],
                    "independent_signal_report_path": str(INDEPENDENT_REPORT),
                    "cluster_individual_report_path": str(CLUSTER_REPORT),
                    "cluster_trade_report_path": str(TRADE_REPORT),
                    "cluster_strategy_report_path": str(STRATEGY_REPORT),
                    "trade_postmortem_report_path": str(POSTMORTEM_REPORT),
                    "public_event_report_path": str(EVENT_REPORT),
                    "cross_source_logic_active": False,
                    "auxiliary_strategy_context_active": True,
                    "execution_authority": False,
                },
            )
        return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--run-on-start", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=30)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    os.environ.setdefault("CIPHER_INDEPENDENT_SOURCE_LOOP", "1")

    if not args.loop:
        payload = run_once(force=args.force)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if payload.get("status") == "failed" else 0

    interval = max(30, int(args.interval_seconds))
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
