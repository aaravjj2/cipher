#!/usr/bin/env python3
"""Run Cipher's bounded autonomous strategy-discovery loop.

This is research automation, not the build/test healer.  Each cycle selects a
small outcome-independent candidate batch, runs canonical price-only
walk-forward backtests, records Holm-adjusted results, and adds only bounded
parameter neighbours for later exploration.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_cross_period_strategy_matrix import build_matrix as build_cross_period_matrix  # noqa: E402
from core.research_platform.auxiliary_research_refresh import refresh_auxiliary_research  # noqa: E402
from core.research_platform.option_research_refresh import refresh_option_research  # noqa: E402
from core.research_platform.recent_regime_refresh import refresh_recent_regime  # noqa: E402
from core.research_platform.strategy_research_loop import (  # noqa: E402
    StrategyResearchPolicy,
    run_research_cycle,
)

GOV = ROOT / "data" / "governance" / "strategy_research"
REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
ARTIFACTS = ROOT / "data" / "artifacts" / "strategy_research"
CACHE = ROOT / "data" / "cache" / "strategy_research"
STATE = GOV / "strategy_research_loop_state.json"
PHASE3_GOV = ROOT / "data" / "governance" / "strategy_research_phase3"
PHASE3_ARTIFACTS = ROOT / "data" / "artifacts" / "strategy_research_phase3"
PHASE3_CACHE = ROOT / "data" / "cache" / "strategy_research_phase3"
PHASE3_STATE = PHASE3_GOV / "strategy_research_phase3_state.json"
PHASE3_DATASET_NAME = "alpaca_broad_daily_2020_2022_development_v1"
OPTION_STATE = GOV / "option_research_refresh_state.json"
OPTION_STATUS = GOV / "latest_option_research_status.json"
AUXILIARY_STATE = GOV / "auxiliary_research_refresh_state.json"
AUXILIARY_STATUS = GOV / "latest_auxiliary_research_status.json"
RECENT_STATE = GOV / "recent_regime_refresh_state.json"
RECENT_STATUS = GOV / "latest_recent_regime_status.json"
LOCK = GOV / "strategy_research_loop.lock"
STOP_REQUESTED = False


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def resolve_dataset_id(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if not REGISTRY.is_file():
        raise RuntimeError("canonical research registry is unavailable")
    with sqlite3.connect(f"file:{REGISTRY.as_posix()}?mode=ro", uri=True, timeout=30) as db:
        row = db.execute(
            """
            select dataset_id from datasets
            where name = 'holdout_c_price_only_original_nine_2023_2025'
              and frozen = 1 and quality_passed = 1
            order by created_at desc
            limit 1
            """
        ).fetchone()
    if not row:
        raise RuntimeError("registered canonical Holdout C price-only dataset is unavailable")
    return str(row[0])


def resolve_phase3_dataset_id() -> str | None:
    if not REGISTRY.is_file():
        return None
    with sqlite3.connect(f"file:{REGISTRY.as_posix()}?mode=ro", uri=True, timeout=30) as db:
        row = db.execute(
            """
            select dataset_id from datasets
            where name=? and frozen=1 and quality_passed=1
            order by created_at desc limit 1
            """,
            (PHASE3_DATASET_NAME,),
        ).fetchone()
    return str(row[0]) if row else None


def run_phase3_cycle(args: argparse.Namespace) -> dict:
    dataset_id = resolve_phase3_dataset_id()
    if dataset_id is None:
        return {
            "status": "skipped_dataset_unavailable",
            "dataset_name": PHASE3_DATASET_NAME,
            "execution_authority": False,
        }
    policy = StrategyResearchPolicy(
        batch_size=args.phase3_batch_size,
        maximum_total_candidates=args.maximum_total_candidates,
        maximum_generation=args.maximum_generation,
        maximum_adaptive_children_per_cycle=args.maximum_adaptive_children,
        slippage_bps_per_side=args.slippage_bps,
        minimum_sessions=max(700, args.minimum_sessions),
        minimum_trades=args.minimum_trades,
        minimum_profit_factor=args.minimum_profit_factor,
        maximum_drawdown_pct=args.maximum_drawdown_pct,
        maximum_holm_adjusted_p_value=args.maximum_adjusted_p_value,
        walk_forward_folds=max(4, args.walk_forward_folds),
        random_seed=args.random_seed + 100_003,
    )
    return run_research_cycle(
        registry_path=REGISTRY,
        artifact_root=PHASE3_ARTIFACTS,
        state_path=PHASE3_STATE,
        output_root=PHASE3_GOV,
        cache_root=PHASE3_CACHE,
        dataset_id=dataset_id,
        policy=policy,
    )


def run_once(args: argparse.Namespace) -> dict:
    GOV.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "skipped_locked",
                "execution_authority": False,
            }
        policy = StrategyResearchPolicy(
            batch_size=args.batch_size,
            maximum_total_candidates=args.maximum_total_candidates,
            maximum_generation=args.maximum_generation,
            maximum_adaptive_children_per_cycle=args.maximum_adaptive_children,
            slippage_bps_per_side=args.slippage_bps,
            minimum_sessions=args.minimum_sessions,
            minimum_trades=args.minimum_trades,
            minimum_profit_factor=args.minimum_profit_factor,
            maximum_drawdown_pct=args.maximum_drawdown_pct,
            maximum_holm_adjusted_p_value=args.maximum_adjusted_p_value,
            walk_forward_folds=args.walk_forward_folds,
            random_seed=args.random_seed,
        )
        payload = run_research_cycle(
            registry_path=REGISTRY,
            artifact_root=ARTIFACTS,
            state_path=STATE,
            output_root=GOV,
            cache_root=CACHE,
            dataset_id=resolve_dataset_id(args.dataset_id),
            policy=policy,
        )
        recent_status = refresh_recent_regime(
            system_root=ROOT,
            state_path=RECENT_STATE,
            status_path=RECENT_STATUS,
            force=False,
        )
        try:
            phase3 = run_phase3_cycle(args)
        except Exception as exc:
            phase3 = {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "automatic_promotion": False,
                "execution_authority": False,
            }
        option_status = refresh_option_research(
            system_root=ROOT,
            state_path=OPTION_STATE,
            status_path=OPTION_STATUS,
            force=False,
        )
        auxiliary_status = refresh_auxiliary_research(
            system_root=ROOT,
            state_path=AUXILIARY_STATE,
            status_path=AUXILIARY_STATUS,
            force=False,
        )
        try:
            cross_period = build_cross_period_matrix()
        except Exception as exc:
            cross_period = {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "automatic_promotion": False,
                "execution_authority": False,
            }
        return {
            **payload,
            "recent_regime_research": {
                "status": recent_status.get("status"),
                "dataset": recent_status.get("dataset"),
                "data_refresh_action": recent_status.get("data_refresh_action"),
                "research_action": recent_status.get("research_action"),
                "summary": recent_status.get("summary"),
                "prospective_snapshot": recent_status.get("prospective_snapshot"),
                "prospective_evaluation": recent_status.get("prospective_evaluation"),
                "prospective_evaluation_action": recent_status.get("prospective_evaluation_action"),
                "component_robustness": recent_status.get("component_robustness"),
                "component_robustness_action": recent_status.get("component_robustness_action"),
                "signal_overlay": recent_status.get("signal_overlay"),
                "signal_overlay_action": recent_status.get("signal_overlay_action"),
                "research_grade": recent_status.get("research_grade"),
                "error_type": recent_status.get("error_type"),
                "error": recent_status.get("error"),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            "phase3_broad_research": {
                "status": phase3.get("status"),
                "dataset_id": (phase3.get("dataset") or {}).get("dataset_id") or phase3.get("dataset_id"),
                "dataset_name": (phase3.get("dataset") or {}).get("dataset_name") or phase3.get("dataset_name"),
                "candidates_tested": len(phase3.get("results") or []),
                "tested_candidate_count_total": phase3.get("tested_candidate_count_total", phase3.get("tested_candidate_count")),
                "adaptive_children_added": len(phase3.get("adaptive_children_added") or []),
                "error_type": phase3.get("error_type"),
                "error": phase3.get("error"),
                "research_role": "broad_phase3_development_only",
                "automatic_promotion": False,
                "execution_authority": False,
            },
            "cross_period_consensus": {
                "status": cross_period.get("status"),
                "summary": cross_period.get("summary"),
                "multi_period_leader_count": len(cross_period.get("multi_period_leaders") or []),
                "error_type": cross_period.get("error_type"),
                "error": cross_period.get("error"),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            "options_research": {
                "status": option_status.get("status"),
                "configuration": option_status.get("configuration"),
                "summary": option_status.get("summary"),
                "research_grade": option_status.get("research_grade"),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            "auxiliary_research": {
                "status": auxiliary_status.get("status"),
                "summary": auxiliary_status.get("summary"),
                "research_grade": auxiliary_status.get("research_grade"),
                "error_type": auxiliary_status.get("error_type"),
                "error": auxiliary_status.get("error"),
                "automatic_promotion": False,
                "execution_authority": False,
            },
        }


def compact(payload: dict) -> dict:
    ranking = payload.get("ranking") or []
    leader = ranking[0] if ranking else {}
    return {
        "time": datetime.now(timezone.utc).isoformat(),
        "status": payload.get("status"),
        "dataset_id": (payload.get("dataset") or {}).get("dataset_id") or payload.get("dataset_id"),
        "candidates_tested": len(payload.get("results") or []),
        "tested_candidate_count_total": payload.get("tested_candidate_count_total", payload.get("tested_candidate_count")),
        "adaptive_children_added": len(payload.get("adaptive_children_added") or []),
        "leader_candidate_id": (leader.get("candidate") or {}).get("candidate_id"),
        "leader_verdict": leader.get("verdict"),
        "leader_composite_score": leader.get("composite_score"),
        "recent_regime_status": (payload.get("recent_regime_research") or {}).get("status"),
        "recent_regime_latest_session": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("latest_session"),
        "recent_regime_selector_passes": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("selector_passes"),
        "recent_regime_gate_passes": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("gate_passes"),
        "recent_regime_leader": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("leader_selector_name"),
        "recent_regime_leader_2025_return_pct": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("leader_2025_return_pct"),
        "recent_regime_leader_2026_return_pct": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("leader_2026_return_pct"),
        "recent_regime_gate_leader": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("gate_leader_name"),
        "recent_regime_gate_current_decision": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("gate_current_decision"),
        "recent_regime_current_selection": ((payload.get("recent_regime_research") or {}).get("summary") or {}).get("current_selection"),
        "recent_regime_snapshot_status": ((payload.get("recent_regime_research") or {}).get("prospective_snapshot") or {}).get("status"),
        "recent_prospective_matured_observations": ((payload.get("recent_regime_research") or {}).get("prospective_evaluation") or {}).get("matured_observations"),
        "recent_prospective_pending_observations": ((payload.get("recent_regime_research") or {}).get("prospective_evaluation") or {}).get("pending_observations"),
        "recent_component_leave_one_out_passed": ((((payload.get("recent_regime_research") or {}).get("component_robustness") or {}).get("summary") or {}).get("leave_one_symbol_out_passed")),
        "recent_component_worst_2025_return_pct": ((((payload.get("recent_regime_research") or {}).get("component_robustness") or {}).get("summary") or {}).get("worst_2025_return_pct")),
        "recent_component_worst_2026_return_pct": ((((payload.get("recent_regime_research") or {}).get("component_robustness") or {}).get("summary") or {}).get("worst_2026_ytd_return_pct")),
        "cipher_signal_overlay_action": (payload.get("recent_regime_research") or {}).get("signal_overlay_action"),
        "cipher_signal_overlay_policies": ((((payload.get("recent_regime_research") or {}).get("signal_overlay") or {}).get("policy_family") or {}).get("count")),
        "cipher_signal_overlay_capture_sessions": ((((payload.get("recent_regime_research") or {}).get("signal_overlay") or {}).get("capture_inventory") or {}).get("sessions")),
        "cipher_signal_overlay_baseline_symbols": (((payload.get("recent_regime_research") or {}).get("signal_overlay") or {}).get("baseline_symbols")),
        "cipher_signal_overlay_current_baskets": (((payload.get("recent_regime_research") or {}).get("signal_overlay") or {}).get("current_policy_baskets")),
        "cipher_signal_overlay_matured_observations": (((((payload.get("recent_regime_research") or {}).get("signal_overlay") or {}).get("prospective_evaluation") or {}).get("matured_observations"))),
        "cipher_signal_overlay_pending_observations": (((((payload.get("recent_regime_research") or {}).get("signal_overlay") or {}).get("prospective_evaluation") or {}).get("pending_observations"))),
        "phase3_status": (payload.get("phase3_broad_research") or {}).get("status"),
        "phase3_candidates_tested": (payload.get("phase3_broad_research") or {}).get("candidates_tested"),
        "phase3_tested_candidate_count_total": (payload.get("phase3_broad_research") or {}).get("tested_candidate_count_total"),
        "cross_period_passed_at_least_two": ((payload.get("cross_period_consensus") or {}).get("summary") or {}).get("passed_at_least_two"),
        "cross_period_passed_all_three": ((payload.get("cross_period_consensus") or {}).get("summary") or {}).get("passed_all_three"),
        "cross_period_passed_all_four": ((payload.get("cross_period_consensus") or {}).get("summary") or {}).get("passed_all_four"),
        "cross_period_tested_all_four": ((payload.get("cross_period_consensus") or {}).get("summary") or {}).get("tested_all_four"),
        "options_research_status": (payload.get("options_research") or {}).get("status"),
        "auxiliary_research_status": (payload.get("auxiliary_research") or {}).get("status"),
        "factor_rotation_passes": ((payload.get("auxiliary_research") or {}).get("summary") or {}).get("factor_rotation_passes"),
        "regime_allocator_passes": ((payload.get("auxiliary_research") or {}).get("summary") or {}).get("regime_allocator_passes"),
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }


def write_failure(exc: Exception) -> dict:
    GOV.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "error_type": type(exc).__name__,
        "error": str(exc),
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    path = GOV / "latest_strategy_research_cycle.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--run-on-start", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--dataset-id")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--phase3-batch-size", type=int, default=2)
    parser.add_argument("--maximum-total-candidates", type=int, default=240)
    parser.add_argument("--maximum-generation", type=int, default=3)
    parser.add_argument("--maximum-adaptive-children", type=int, default=8)
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    parser.add_argument("--minimum-sessions", type=int, default=500)
    parser.add_argument("--minimum-trades", type=int, default=30)
    parser.add_argument("--minimum-profit-factor", type=float, default=1.10)
    parser.add_argument("--maximum-drawdown-pct", type=float, default=25.0)
    parser.add_argument("--maximum-adjusted-p-value", type=float, default=0.10)
    parser.add_argument("--walk-forward-folds", type=int, default=3)
    parser.add_argument("--random-seed", type=int, default=1729)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    os.environ.setdefault("CIPHER_STRATEGY_RESEARCH_LOOP", "1")

    if not args.loop:
        try:
            payload = run_once(args)
        except Exception as exc:
            payload = write_failure(exc)
        print(json.dumps(compact(payload), indent=2, sort_keys=True))
        return 0 if payload.get("status") in {"completed", "catalog_exhausted_or_candidate_cap_reached", "skipped_locked"} else 1

    interval = max(3600, int(args.interval_seconds))
    first = True
    while not STOP_REQUESTED:
        if first and not args.run_on_start:
            first = False
        else:
            try:
                payload = run_once(args)
            except Exception as exc:
                payload = write_failure(exc)
            print(json.dumps(compact(payload), sort_keys=True), flush=True)
            first = False
        slept = 0
        while slept < interval and not STOP_REQUESTED:
            time.sleep(min(1, interval - slept))
            slept += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
