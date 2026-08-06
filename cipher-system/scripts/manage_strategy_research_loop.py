#!/usr/bin/env python3
"""Start, stop, or inspect Cipher's autonomous strategy research loop."""
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
GOV = ROOT / "data" / "governance" / "strategy_research"
LOG_DIR = ROOT / "logs"
PID_PATH = GOV / "strategy_research_loop.pid"
STATUS_PATH = GOV / "strategy_research_loop_status.json"
RUNNER = ROOT / "scripts" / "run_strategy_research_loop.py"
LATEST = GOV / "latest_strategy_research_cycle.json"
PHASE3_LATEST = ROOT / "data" / "governance" / "strategy_research_phase3" / "latest_strategy_research_cycle.json"
LOCKED_VALIDATION = ROOT / "data" / "governance" / "strategy_research_validation" / "latest_locked_broad_validation.json"
YTD_VALIDATION = ROOT / "data" / "governance" / "strategy_research_2026_ytd" / "latest_2026_ytd_locked_validation.json"
YTD_ROBUSTNESS = ROOT / "data" / "governance" / "strategy_research_2026_ytd" / "latest_2026_ytd_robustness.json"
ANNUAL_STABILITY = ROOT / "data" / "governance" / "annual_regime_stability.json"
CROSS_PERIOD_MATRIX = ROOT / "data" / "governance" / "cross_period_strategy_matrix.json"
OPTION_STATUS = GOV / "latest_option_research_status.json"
AUXILIARY_STATUS = GOV / "latest_auxiliary_research_status.json"
RECENT_STATUS = GOV / "latest_recent_regime_status.json"


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


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def latest_summary() -> dict:
    payload = read_json(LATEST)
    if not payload:
        return {}
    ranking = payload.get("ranking") or []
    leader = ranking[0] if ranking else {}
    phase3_payload = read_json(PHASE3_LATEST)
    phase3_ranking = phase3_payload.get("ranking") or []
    phase3_leader = phase3_ranking[0] if phase3_ranking else {}
    validation_payload = read_json(LOCKED_VALIDATION)
    validation_summary = validation_payload.get("summary") if isinstance(validation_payload.get("summary"), dict) else {}
    ytd_payload = read_json(YTD_VALIDATION)
    ytd_summary = ytd_payload.get("summary") if isinstance(ytd_payload.get("summary"), dict) else {}
    ytd_robustness = read_json(YTD_ROBUSTNESS)
    annual_stability = read_json(ANNUAL_STABILITY)
    matrix_payload = read_json(CROSS_PERIOD_MATRIX)
    matrix_summary = matrix_payload.get("summary") if isinstance(matrix_payload.get("summary"), dict) else {}
    option_payload = read_json(OPTION_STATUS)
    option_summary = option_payload.get("summary") if isinstance(option_payload.get("summary"), dict) else {}
    auxiliary_payload = read_json(AUXILIARY_STATUS)
    auxiliary_summary = auxiliary_payload.get("summary") if isinstance(auxiliary_payload.get("summary"), dict) else {}
    recent_payload = read_json(RECENT_STATUS)
    recent_summary = recent_payload.get("summary") if isinstance(recent_payload.get("summary"), dict) else {}
    recent_prospective = recent_payload.get("prospective_evaluation") if isinstance(recent_payload.get("prospective_evaluation"), dict) else {}
    recent_robustness = recent_payload.get("component_robustness") if isinstance(recent_payload.get("component_robustness"), dict) else {}
    recent_robustness_summary = recent_robustness.get("summary") if isinstance(recent_robustness.get("summary"), dict) else {}
    signal_overlay = recent_payload.get("signal_overlay") if isinstance(recent_payload.get("signal_overlay"), dict) else {}
    signal_overlay_inventory = signal_overlay.get("capture_inventory") if isinstance(signal_overlay.get("capture_inventory"), dict) else {}
    signal_overlay_policy_family = signal_overlay.get("policy_family") if isinstance(signal_overlay.get("policy_family"), dict) else {}
    signal_overlay_evaluation = signal_overlay.get("prospective_evaluation") if isinstance(signal_overlay.get("prospective_evaluation"), dict) else {}
    return {
        "latest_cycle_status": payload.get("status"),
        "latest_cycle_created_at": payload.get("created_at"),
        "tested_candidate_count_total": payload.get("tested_candidate_count_total", payload.get("tested_candidate_count")),
        "latest_batch_size": len(payload.get("results") or []),
        "latest_leader_candidate_id": (leader.get("candidate") or {}).get("candidate_id"),
        "latest_leader_verdict": leader.get("verdict"),
        "phase3_cycle_status": phase3_payload.get("status"),
        "phase3_cycle_created_at": phase3_payload.get("created_at"),
        "phase3_tested_candidate_count_total": phase3_payload.get(
            "tested_candidate_count_total", phase3_payload.get("tested_candidate_count")
        ),
        "phase3_latest_batch_size": len(phase3_payload.get("results") or []),
        "phase3_latest_leader_candidate_id": (phase3_leader.get("candidate") or {}).get("candidate_id"),
        "phase3_latest_leader_verdict": phase3_leader.get("verdict"),
        "locked_validation_candidates": validation_summary.get("candidates"),
        "locked_validation_passes": validation_summary.get("passes"),
        "locked_validation_failures": validation_summary.get("failures"),
        "locked_validation_errors": validation_summary.get("errors"),
        "ytd_2026_candidates": ytd_summary.get("candidates"),
        "ytd_2026_passes": ytd_summary.get("passes"),
        "ytd_2026_failures": ytd_summary.get("failures"),
        "ytd_2026_errors": ytd_summary.get("errors"),
        "ytd_2026_robust_candidates": ytd_robustness.get("robust_candidate_count"),
        "annual_stability_candidates": annual_stability.get("candidate_count"),
        "annual_stability_passes": annual_stability.get("stable_candidate_count"),
        "cross_period_tested_all_three": matrix_summary.get("tested_all_three"),
        "cross_period_tested_all_four": matrix_summary.get("tested_all_four"),
        "cross_period_passed_at_least_two": matrix_summary.get("passed_at_least_two"),
        "cross_period_passed_all_three": matrix_summary.get("passed_all_three"),
        "cross_period_passed_all_four": matrix_summary.get("passed_all_four"),
        "options_research_status": option_payload.get("status"),
        "options_candidate_variants": option_summary.get("candidate_variants"),
        "options_degradation_survivors": option_summary.get("degradation_survivor_count"),
        "options_severe_survivors": option_summary.get("severe_survivor_count"),
        "auxiliary_research_status": auxiliary_payload.get("status"),
        "regime_allocator_specs": auxiliary_summary.get("regime_allocator_specs"),
        "regime_allocator_effective_hypotheses": auxiliary_summary.get("regime_allocator_effective_hypotheses"),
        "regime_allocator_passes": auxiliary_summary.get("regime_allocator_passes"),
        "factor_rotation_specs": auxiliary_summary.get("factor_rotation_specs"),
        "factor_rotation_effective_hypotheses": auxiliary_summary.get("factor_rotation_effective_hypotheses"),
        "factor_rotation_passes": auxiliary_summary.get("factor_rotation_passes"),
        "dominant_auxiliary_failure_category": auxiliary_summary.get("dominant_failure_category"),
        "recent_regime_status": recent_payload.get("status"),
        "recent_regime_latest_session": recent_summary.get("latest_session"),
        "recent_regime_data_stale_calendar_days": recent_summary.get("data_stale_calendar_days"),
        "recent_regime_components": recent_summary.get("components"),
        "recent_regime_selectors": recent_summary.get("selectors"),
        "recent_regime_selector_passes": recent_summary.get("selector_passes"),
        "recent_regime_leader": recent_summary.get("leader_selector_name"),
        "recent_regime_leader_2025_return_pct": recent_summary.get("leader_2025_return_pct"),
        "recent_regime_leader_2025_spy_excess_pct": recent_summary.get("leader_2025_spy_excess_pct"),
        "recent_regime_leader_2026_return_pct": recent_summary.get("leader_2026_return_pct"),
        "recent_regime_leader_spy_excess_pct": recent_summary.get("leader_spy_excess_pct"),
        "recent_regime_leader_combined_return_pct": recent_summary.get("leader_combined_return_pct"),
        "recent_regime_leader_combined_spy_excess_pct": recent_summary.get("leader_combined_spy_excess_pct"),
        "recent_regime_gate_variants": recent_summary.get("gate_variants"),
        "recent_regime_gate_passes": recent_summary.get("gate_passes"),
        "recent_regime_gate_leader": recent_summary.get("gate_leader_name"),
        "recent_regime_gate_leader_2025_return_pct": recent_summary.get("gate_leader_2025_return_pct"),
        "recent_regime_gate_leader_2026_return_pct": recent_summary.get("gate_leader_2026_return_pct"),
        "recent_regime_gate_current_decision": recent_summary.get("gate_current_decision"),
        "recent_regime_gate_current_effective_selection": recent_summary.get("gate_current_effective_selection"),
        "recent_regime_current_selection": recent_summary.get("current_selection"),
        "recent_regime_current_selected_components": recent_summary.get("current_selected_components"),
        "recent_regime_prospective_snapshot": recent_payload.get("prospective_snapshot"),
        "recent_prospective_evaluation_action": recent_payload.get("prospective_evaluation_action"),
        "recent_prospective_matured_observations": recent_prospective.get("matured_observations"),
        "recent_prospective_pending_observations": recent_prospective.get("pending_observations"),
        "recent_prospective_selector_one_session": recent_prospective.get("leader_selector_one_session"),
        "recent_prospective_gate_one_session": recent_prospective.get("leader_gate_one_session"),
        "recent_component_robustness_action": recent_payload.get("component_robustness_action"),
        "recent_component_leave_one_out_passed": recent_robustness_summary.get("leave_one_symbol_out_passed"),
        "recent_component_worst_2025_return_pct": recent_robustness_summary.get("worst_2025_return_pct"),
        "recent_component_worst_2026_ytd_return_pct": recent_robustness_summary.get("worst_2026_ytd_return_pct"),
        "recent_component_top_symbol": recent_robustness_summary.get("top_symbol"),
        "recent_component_top_symbol_positive_share": recent_robustness_summary.get("top_symbol_positive_share"),
        "cipher_signal_overlay_action": recent_payload.get("signal_overlay_action"),
        "cipher_signal_overlay_policies": signal_overlay_policy_family.get("count"),
        "cipher_signal_overlay_capture_sessions": signal_overlay_inventory.get("sessions"),
        "cipher_signal_overlay_episodes": signal_overlay_inventory.get("episodes"),
        "cipher_signal_overlay_baseline_symbols": signal_overlay.get("baseline_symbols"),
        "cipher_signal_overlay_current_baskets": signal_overlay.get("current_policy_baskets"),
        "cipher_signal_overlay_matured_observations": signal_overlay_evaluation.get("matured_observations"),
        "cipher_signal_overlay_pending_observations": signal_overlay_evaluation.get("pending_observations"),
    }


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
        "focus": [
            "new_strategy_candidates",
            "canonical_backtesting",
            "walk_forward_evaluation",
            "multiple_testing_control",
            "bounded_parameter_feedback",
            "cross_sectional_and_market_neutral_research",
            "regime_and_ensemble_research",
            "historical_options_walk_forward",
            "locked_temporal_validation",
            "recent_2025_development",
            "rolling_monthly_2025_2026_selection",
            "prior_month_market_regime_gates",
            "immutable_recent_prospective_snapshots",
            "future_open_prospective_scoring",
            "exact_recent_component_concentration_audit",
            "cipher_flash_agentic_cluster_overlay",
            "after_close_recent_data_refresh",
            "broad_2020_2022_phase3_research",
            "cross_period_consensus",
            "locked_2026_ytd_validation",
            "transaction_cost_and_symbol_concentration_stress",
            "calendar_year_regime_stability",
            "factor_macro_etf_rotation",
            "trailing_only_regime_allocator",
            "gate_failure_attribution",
            "input_fingerprint_refresh",
        ],
        "source_code_auto_edit": False,
        "automatic_promotion": False,
        "lean_replication": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
        **latest_summary(),
    }
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(STATUS_PATH)
    return payload


def start(interval_seconds: int, *, run_on_start: bool, batch_size: int) -> dict:
    pid = active_pid()
    if pid is not None:
        return write_status("start", pid=pid, state="already_running", detail=str(LATEST) if LATEST.is_file() else None)
    GOV.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "strategy_research_loop.log"
    command = [
        str(Path(sys.executable).absolute()),
        str(RUNNER),
        "--loop",
        "--interval-seconds",
        str(max(3600, interval_seconds)),
        "--batch-size",
        str(max(1, batch_size)),
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
        env={**os.environ, "CIPHER_STRATEGY_RESEARCH_LOOP": "1"},
    )
    PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
    time.sleep(1)
    if process.poll() is not None:
        PID_PATH.unlink(missing_ok=True)
        return write_status("start", pid=None, state="failed", detail=f"loop exited with {process.returncode}; inspect {log_path}")
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
    for _ in range(100):
        if not Path(f"/proc/{pid}").exists():
            PID_PATH.unlink(missing_ok=True)
            return write_status("stop", pid=None, state="stopped")
        time.sleep(0.1)
    return write_status("stop", pid=pid, state="stopping")


def status() -> dict:
    pid = active_pid()
    return write_status(
        "status",
        pid=pid,
        state="running" if pid is not None else "not_running",
        detail=str(LATEST) if LATEST.is_file() else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--run-on-start", action="store_true")
    args = parser.parse_args()
    if args.action == "start":
        payload = start(args.interval_seconds, run_on_start=args.run_on_start, batch_size=args.batch_size)
    elif args.action == "stop":
        payload = stop()
    else:
        payload = status()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if payload["state"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
