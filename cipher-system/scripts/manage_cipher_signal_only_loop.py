#!/usr/bin/env python3
"""Start, stop, or inspect the independent Cipher source research loop."""
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
INDEPENDENT_REPORT = GOV / "latest_independent_signal_analysis.json"
CLUSTER_REPORT = GOV / "latest_cluster_individual_analysis.json"
TRADE_REPORT = GOV / "latest_cluster_trade_candidates.json"
STRATEGY_REPORT = ROOT / "data" / "governance" / "cipher_strategy" / "latest_strategy_decision.json"
POSTMORTEM_REPORT = ROOT / "data" / "governance" / "cipher_strategy" / "latest_trade_postmortems.json"
CYCLE = GOV / "latest_signal_research_cycle.json"


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


def source_summary(payload: dict[str, Any], name: str) -> dict[str, Any]:
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    source = sources.get(name) if isinstance(sources.get(name), dict) else {}
    episodes = source.get("episodes") if isinstance(source.get("episodes"), dict) else {}
    states = source.get("terminal_states") if isinstance(source.get("terminal_states"), dict) else {}
    scoring = source.get("forward_scoring") if isinstance(source.get("forward_scoring"), dict) else {}
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


def summary() -> dict[str, Any]:
    cycle = read_json(CYCLE)
    independent = read_json(INDEPENDENT_REPORT)
    cluster = read_json(CLUSTER_REPORT)
    trade = read_json(TRADE_REPORT)
    strategy = read_json(STRATEGY_REPORT)
    postmortems = read_json(POSTMORTEM_REPORT)
    cluster_summary = cluster.get("summary") if isinstance(cluster.get("summary"), dict) else {}
    trade_board = trade.get("current_trade_board") if isinstance(trade.get("current_trade_board"), dict) else {}
    delayed = trade.get("delayed_entry_research") if isinstance(trade.get("delayed_entry_research"), dict) else {}
    return {
        "latest_cycle_status": cycle.get("status"),
        "latest_cycle_created_at": cycle.get("created_at"),
        "mode": cycle.get("mode") or "independent_source_analysis_only",
        "cross_source_logic_active": False,
        "flash": source_summary(independent, "flash"),
        "agentic": source_summary(independent, "flash_agentic"),
        "cluster": {
            "analysis_unit": cluster_summary.get("analysis_unit"),
            "total_episodes": cluster_summary.get("total_cluster_episodes"),
            "eligible_regular_session_episodes": cluster_summary.get("eligible_regular_session_episodes"),
            "excluded_episodes": cluster_summary.get("excluded_episodes"),
            "matured_at_expiry": cluster_summary.get("matured_at_expiry"),
            "pending_expiry": cluster_summary.get("pending_expiry"),
            "unscorable": cluster_summary.get("unscorable"),
            "latest_completed_market_session": cluster_summary.get("latest_completed_market_session"),
            "tier_counts": cluster_summary.get("tier_counts"),
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
            "report_id": trade.get("report_id"),
            "latest_signal_session": trade.get("latest_signal_session"),
            "candidate_count": len(trade_board.get("candidates") or []),
            "status_counts": trade_board.get("status_counts"),
            "top_candidates": (trade_board.get("candidates") or [])[:8],
            "delayed_entry_all": delayed.get("all_first_qualifying_tier_a"),
            "delayed_entry_persisted": delayed.get("persisted_tier_a_to_last_same_day_capture"),
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
        "other_research_branches": "combined_source_artifacts_frozen_reference_only",
    }


def write_status(action: str, *, pid: int | None, state: str, detail: str | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": 2,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "state": state,
        "pid": pid,
        "detail": detail,
        "runner": str(RUNNER),
        "focus": [
            "cluster_every_unique_episode",
            "cluster_second_listed_expiration_reconstruction",
            "cluster_individual_rank_strength_target_and_time_context",
            "cluster_same_ticker_episode_sequence_without_other_sources",
            "cluster_underlying_move_through_expiration",
            "cluster_atm_and_target_option_moves_through_expiration",
            "cluster_debit_spread_move_through_expiration",
            "cluster_finalized_vs_pending_expiry_separation",
            "cluster_next_session_entry_research",
            "cluster_liquidity_gated_trade_board_without_execution",
            "raw_cluster_scan_immutable_snapshot_before_analysis",
            "deterministic_cluster_spread_strategy_without_execution",
            "flash_agentic_non_gating_confidence_context",
            "catalyst_and_relative_move_attribution_context",
            "every_modeled_trade_postmortem",
            "entry_time_flash_agentic_context_separate_from_post_entry_context",
            "company_specific_vs_market_headline_attribution",
            "spread_fill_and_leg_timing_uncertainty",
            "flash_standalone_episode_and_terminal_state_analysis",
            "flash_standalone_one_five_twenty_one_session_scoring",
            "agentic_standalone_episode_and_terminal_state_analysis",
            "agentic_standalone_one_five_twenty_one_session_scoring",
        ],
        "disabled_active_logic": [
            "cross_source_confirmation",
            "cross_source_veto",
            "cross_source_majority_vote",
            "cross_source_lead_lag",
            "combined_candidate_rules",
            "flash_agentic_hard_trade_gate",
            "news_directional_trade_gate",
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
        env={**os.environ, "CIPHER_INDEPENDENT_SOURCE_LOOP": "1"},
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
    parser.add_argument("--interval-seconds", type=int, default=30)
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
