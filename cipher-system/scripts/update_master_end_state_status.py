#!/usr/bin/env python3
"""Consolidate the eight active end-state tracks into one honest status artifact."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GOV = DATA / "governance"


def latest(pattern: str) -> Path | None:
    candidates = sorted(ROOT.glob(pattern))
    return candidates[-1] if candidates else None


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def registry_counts() -> dict[str, int]:
    path = GOV / "research_registry.sqlite"
    if not path.is_file():
        return {
            "news_events": 0,
            "anomaly_events": 0,
            "holdout_c_dataset_manifests": 0,
            "holdout_c_raw_objects": 0,
            "holdout_c_dataset_raw_links": 0,
            "autonomous_strategy_specs": 0,
            "autonomous_experiments": 0,
            "autonomous_passes": 0,
            "autonomous_conditional_passes": 0,
            "autonomous_failures": 0,
            "autonomous_promotion_events": 0,
        }
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as db:
        tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        counts = {
            name: int(db.execute(f"select count(*) from {name}").fetchone()[0]) if name in tables else 0
            for name in ("news_events", "anomaly_events")
        }
        dataset_name = "holdout_c_price_only_original_nine_2023_2025"
        counts.update(
            {
                "holdout_c_dataset_manifests": int(
                    db.execute("select count(*) from datasets where name = ?", (dataset_name,)).fetchone()[0]
                )
                if "datasets" in tables
                else 0,
                "holdout_c_raw_objects": int(
                    db.execute("select count(*) from raw_objects where dataset = ?", (dataset_name,)).fetchone()[0]
                )
                if "raw_objects" in tables
                else 0,
                "holdout_c_dataset_raw_links": int(
                    db.execute(
                        """
                        select count(*) from dataset_raw_objects dr
                        join datasets d on d.dataset_id = dr.dataset_id
                        where d.name = ?
                        """,
                        (dataset_name,),
                    ).fetchone()[0]
                )
                if {"dataset_raw_objects", "datasets"}.issubset(tables)
                else 0,
                "autonomous_strategy_specs": int(
                    db.execute("select count(*) from strategies where name like 'autonomous_price_only_%'").fetchone()[0]
                )
                if "strategies" in tables
                else 0,
                "autonomous_experiments": int(
                    db.execute(
                        """
                        select count(*) from experiments e
                        join strategies s on s.strategy_id = e.strategy_id
                        where s.name like 'autonomous_price_only_%'
                        """
                    ).fetchone()[0]
                )
                if {"experiments", "strategies"}.issubset(tables)
                else 0,
                "autonomous_passes": int(
                    db.execute(
                        """
                        select count(*) from experiments e
                        join strategies s on s.strategy_id = e.strategy_id
                        where s.name like 'autonomous_price_only_%' and e.verdict = 'PASS'
                        """
                    ).fetchone()[0]
                )
                if {"experiments", "strategies"}.issubset(tables)
                else 0,
                "autonomous_conditional_passes": int(
                    db.execute(
                        """
                        select count(*) from experiments e
                        join strategies s on s.strategy_id = e.strategy_id
                        where s.name like 'autonomous_price_only_%' and e.verdict = 'CONDITIONAL_PASS'
                        """
                    ).fetchone()[0]
                )
                if {"experiments", "strategies"}.issubset(tables)
                else 0,
                "autonomous_failures": int(
                    db.execute(
                        """
                        select count(*) from experiments e
                        join strategies s on s.strategy_id = e.strategy_id
                        where s.name like 'autonomous_price_only_%' and e.verdict = 'FAIL'
                        """
                    ).fetchone()[0]
                )
                if {"experiments", "strategies"}.issubset(tables)
                else 0,
                "autonomous_promotion_events": int(
                    db.execute(
                        """
                        select count(*) from promotion_events p
                        join strategies s on s.strategy_id = p.strategy_id
                        where s.name like 'autonomous_price_only_%'
                        """
                    ).fetchone()[0]
                )
                if {"promotion_events", "strategies"}.issubset(tables)
                else 0,
            }
        )
        return counts


def track(track_id: int, name: str, state: str, *, reason: str, evidence: list[str], metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "name": name,
        "state": state,
        "closed": state in {"completed_real", "completed_infrastructure", "closed_data_insufficient"},
        "reason": reason,
        "evidence": evidence,
        "metrics": dict(metrics or {}),
        "live_execution": False,
    }


def build_status() -> dict[str, Any]:
    cohort_path = latest("data/governance/holdout_c_alpaca_cohort_construction_*.json")
    cohort = read_json(cohort_path)
    selected = cohort.get("selected_block") or {}
    observed_origins = int(selected.get("strict_independent_origins") or 0)
    required_origins = int(cohort.get("requirements", {}).get("minimum_strict_independent_origins") or 12)
    cohort_pass = bool(cohort.get("pass"))
    rescue_path = GOV / "holdout_c_alpaca_cohort_rescue_v3.json"
    rescue = read_json(rescue_path)
    rescue_selected = rescue.get("selected_block") or {}
    rescue_origins = int(rescue_selected.get("strict_independent_origins") or 0)
    rescue_pass = bool(rescue.get("pass"))

    factor_path = latest("data/market_quality/price_only_factor_screen_skip_*.json") or latest("data/market_quality/price_only_factor_screen_*.json")
    model_path = latest("data/market_quality/current_era_price_only_model_skip_*.json") or latest("data/market_quality/current_era_price_only_model_results_*.json")
    event_path = latest("data/events/public_event_ingestion_*.json")
    event = read_json(event_path)
    processed_events = len(event.get("processed_events", []))
    event_sources = event.get("sources", {})
    repair_path = latest("data/governance/bounded_repair_run_*.json")
    repair = read_json(repair_path)
    scheduler_state_path = GOV / "safe_scheduled_jobs_state.json"
    scheduler_state = read_json(scheduler_state_path)
    scheduler_pid_path = GOV / "safe_scheduler.pid"
    scheduler_active = False
    if scheduler_pid_path.is_file():
        try:
            pid = int(scheduler_pid_path.read_text().strip())
            Path(f"/proc/{pid}").exists() and (scheduler_active := True)
        except (OSError, ValueError):
            scheduler_active = False

    build_healing_root = GOV / "build_healing"
    build_healing_run = read_json(build_healing_root / "latest_build_healing_run.json")
    build_healing_pid_path = build_healing_root / "build_healing_loop.pid"
    build_healing_active = False
    if build_healing_pid_path.is_file():
        try:
            build_healing_pid = int(build_healing_pid_path.read_text(encoding="utf-8").strip())
            build_healing_active = Path(f"/proc/{build_healing_pid}").exists()
        except (OSError, ValueError):
            build_healing_active = False

    strategy_research_root = GOV / "strategy_research"
    strategy_research_run = read_json(strategy_research_root / "latest_strategy_research_cycle.json")
    strategy_research_state = read_json(strategy_research_root / "strategy_research_loop_state.json")
    option_research_status = read_json(strategy_research_root / "latest_option_research_status.json")
    auxiliary_research_status = read_json(strategy_research_root / "latest_auxiliary_research_status.json")
    recent_regime_status = read_json(strategy_research_root / "latest_recent_regime_status.json")
    phase3_research_root = GOV / "strategy_research_phase3"
    phase3_research_run = read_json(phase3_research_root / "latest_strategy_research_cycle.json")
    phase3_research_state = read_json(phase3_research_root / "strategy_research_phase3_state.json")
    locked_validation = read_json(GOV / "strategy_research_validation" / "latest_locked_broad_validation.json")
    locked_validation_summary = (
        locked_validation.get("summary")
        if isinstance(locked_validation.get("summary"), dict)
        else {}
    )
    ytd_validation = read_json(GOV / "strategy_research_2026_ytd" / "latest_2026_ytd_locked_validation.json")
    ytd_validation_summary = (
        ytd_validation.get("summary")
        if isinstance(ytd_validation.get("summary"), dict)
        else {}
    )
    ytd_robustness = read_json(GOV / "strategy_research_2026_ytd" / "latest_2026_ytd_robustness.json")
    annual_stability = read_json(GOV / "annual_regime_stability.json")
    cross_period_matrix = read_json(GOV / "cross_period_strategy_matrix.json")
    cross_period_summary = (
        cross_period_matrix.get("summary")
        if isinstance(cross_period_matrix.get("summary"), dict)
        else {}
    )
    option_research_summary = (
        option_research_status.get("summary")
        if isinstance(option_research_status.get("summary"), dict)
        else {}
    )
    auxiliary_research_summary = (
        auxiliary_research_status.get("summary")
        if isinstance(auxiliary_research_status.get("summary"), dict)
        else {}
    )
    recent_regime_summary = (
        recent_regime_status.get("summary")
        if isinstance(recent_regime_status.get("summary"), dict)
        else {}
    )
    recent_prospective_evaluation = (
        recent_regime_status.get("prospective_evaluation")
        if isinstance(recent_regime_status.get("prospective_evaluation"), dict)
        else {}
    )
    recent_component_robustness = (
        recent_regime_status.get("component_robustness")
        if isinstance(recent_regime_status.get("component_robustness"), dict)
        else {}
    )
    recent_component_robustness_summary = (
        recent_component_robustness.get("summary")
        if isinstance(recent_component_robustness.get("summary"), dict)
        else {}
    )
    signal_overlay = (
        recent_regime_status.get("signal_overlay")
        if isinstance(recent_regime_status.get("signal_overlay"), dict)
        else {}
    )
    signal_overlay_inventory = (
        signal_overlay.get("capture_inventory")
        if isinstance(signal_overlay.get("capture_inventory"), dict)
        else {}
    )
    signal_overlay_policy_family = (
        signal_overlay.get("policy_family")
        if isinstance(signal_overlay.get("policy_family"), dict)
        else {}
    )
    signal_overlay_evaluation = (
        signal_overlay.get("prospective_evaluation")
        if isinstance(signal_overlay.get("prospective_evaluation"), dict)
        else {}
    )
    strategy_research_pid_path = strategy_research_root / "strategy_research_loop.pid"
    strategy_research_active = False
    if strategy_research_pid_path.is_file():
        try:
            strategy_research_pid = int(strategy_research_pid_path.read_text(encoding="utf-8").strip())
            strategy_research_active = Path(f"/proc/{strategy_research_pid}").exists()
        except (OSError, ValueError):
            strategy_research_active = False
    strategy_ranking = strategy_research_run.get("ranking") or []
    strategy_leader = strategy_ranking[0] if strategy_ranking else {}

    architecture_audit = read_json(GOV / "original_architecture_self_audit.json")
    unified_product_audit = read_json(GOV / "unified_cipher_product_audit.json")
    post_merge_verification = read_json(GOV / "post_merge_verification.json")

    counts = registry_counts()
    app_text = (ROOT / "core" / "app.py").read_text(encoding="utf-8", errors="ignore")
    # The research-status surface moved when the vanilla app/public bundle was replaced
    # by the Next.js frontend in cipher-system/web. Read the frontend SOURCE rather than
    # the built bundle: the build output is regenerable and was previously absent
    # entirely, which crashed this function on a missing app/public/app.js.
    ui_text = ""
    for path in sorted((ROOT / "web" / "src").rglob("*.tsx")):
        try:
            ui_text += path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    ui_ready = (
        "/api/research-status" in app_text
        and "fetchResearchStatus" in ui_text
        and 'data-view="researchStatus"' in ui_text
    )

    if cohort_pass:
        track1 = track(1, "Price-only Qlib/RD-Agent factor discovery", "completed_real", reason="A frozen cohort passed and the governed factor study completed.", evidence=[str(factor_path)] if factor_path else [], metrics={"strict_independent_origins": observed_origins})
        track2 = track(2, "Expanded price-only model study", "completed_real", reason="A frozen cohort passed and the preregistered model study completed.", evidence=[str(model_path)] if model_path else [], metrics={"strict_independent_origins": observed_origins})
        track3 = track(3, "Full-scale price-only backtesting", "pending", reason="The cohort passed but no qualifying full backtest close-out is registered.", evidence=[])
    else:
        common = {
            "original_panel_strict_independent_origins": observed_origins,
            "required_strict_independent_origins": required_origins,
            "original_panel_origin_gap": max(0, required_origins - observed_origins),
            "original_panel_status": "essentially_resolved_not_cleared" if observed_origins == required_origins - 1 else "data_insufficient",
            "rescue_v3_structural_pass": rescue_pass,
            "rescue_v3_strict_independent_origins": rescue_origins,
            "rescue_v3_allowed_claim": rescue.get("allowed_claim"),
            "untouched_holdout_restored": False,
            "gate_relaxed": False,
        }
        track1 = track(1, "Price-only Qlib/RD-Agent factor discovery", "closed_data_insufficient", reason="The original panel remains 11/12. Rescue v3 clears structural availability at 14/12, but prior exploratory use means it does not restore an untouched final holdout; prior pooled screening remains exploratory only.", evidence=[str(path) for path in (cohort_path, rescue_path, factor_path) if path], metrics=common)
        track2 = track(2, "Expanded price-only model study", "closed_data_insufficient", reason="The structural origin gap is closed, but no model is rerun because rescue v3 is not an untouched final holdout for previously explored formulations.", evidence=[str(path) for path in (cohort_path, rescue_path, model_path) if path], metrics=common)
        track3 = track(
            3,
            "Full-scale price-only backtesting",
            "closed_data_insufficient",
            reason=(
                "Confirmatory VectorBT/LEAN graduation remains skipped because the period cannot be reclassified as untouched. "
                "Separate exploratory branches now cover the original 2023-2025 panel and a broad 38-asset 2020-2022 development panel, "
                "with cross-sectional, market-neutral, regime, and ensemble backtests under walk-forward and multiple-testing controls. "
                "A locked 2016-2019 temporal validation evaluates only the candidate family frozen before download, while a separate 2026-YTD holdout scores 194 pre-download candidate identities after excluding warmup bars from every metric. "
                "Cost, leave-one-symbol-out, bootstrap, calendar-year stability, and a four-period consensus matrix expose regime fragility rather than promoting isolated winners. "
                "A guarded historical-options branch reruns nested walk-forward simulations when its inputs change. "
                "Separate factor/macro ETF rotation and trailing-only regime-allocation branches now use fingerprinted refresh, unique-return-path multiple-testing, and gate-level failure attribution. "
                "A recent-regime branch prioritizes 2025 development and monthly no-lookahead 2026 selection, with after-close data freshness checks and immutable daily snapshots. No branch can promote or trade."
            ),
            evidence=[
                str(path)
                for path in (
                    cohort_path,
                    rescue_path,
                    strategy_research_root / "latest_strategy_research_cycle.json",
                    strategy_research_root / "latest_option_research_status.json",
                    phase3_research_root / "latest_strategy_research_cycle.json",
                    GOV / "strategy_research_validation" / "latest_locked_broad_validation.json",
                    GOV / "strategy_research_2026_ytd" / "latest_2026_ytd_locked_validation.json",
                    GOV / "strategy_research_2026_ytd" / "latest_2026_ytd_robustness.json",
                    GOV / "annual_regime_stability.json",
                    GOV / "cross_period_strategy_matrix.json",
                    GOV / "regime_allocator_research.json",
                    GOV / "factor_rotation_research.json",
                    GOV / "research_failure_attribution.json",
                    strategy_research_root / "latest_auxiliary_research_status.json",
                    strategy_research_root / "latest_recent_regime_status.json",
                    GOV / "recent_regime_research.json",
                    GOV / "cipher_signal_overlay_research.json",
                )
                if path and Path(path).exists()
            ],
            metrics={
                **common,
                "exploratory_strategy_loop_active": strategy_research_active,
                "exploratory_candidates_tested": len(strategy_research_state.get("tested_candidate_ids", [])),
                "canonical_exploratory_experiments": counts["autonomous_experiments"],
                "phase_two_search_enabled": True,
                "phase3_broad_candidates_tested": len(phase3_research_state.get("tested_candidate_ids", [])),
                "locked_validation_candidates": locked_validation_summary.get("candidates"),
                "locked_validation_passes": locked_validation_summary.get("passes"),
                "locked_validation_failures": locked_validation_summary.get("failures"),
                "locked_validation_errors": locked_validation_summary.get("errors"),
                "ytd_2026_candidates": ytd_validation_summary.get("candidates"),
                "ytd_2026_passes": ytd_validation_summary.get("passes"),
                "ytd_2026_failures": ytd_validation_summary.get("failures"),
                "ytd_2026_errors": ytd_validation_summary.get("errors"),
                "ytd_2026_robust_candidates": ytd_robustness.get("robust_candidate_count"),
                "annual_stability_candidates": annual_stability.get("candidate_count"),
                "annual_stability_passes": annual_stability.get("stable_candidate_count"),
                "cross_period_tested_all_three": cross_period_summary.get("tested_all_three"),
                "cross_period_tested_all_four": cross_period_summary.get("tested_all_four"),
                "cross_period_passed_at_least_two": cross_period_summary.get("passed_at_least_two"),
                "cross_period_passed_all_three": cross_period_summary.get("passed_all_three"),
                "cross_period_passed_all_four": cross_period_summary.get("passed_all_four"),
                "options_candidate_variants": option_research_summary.get("candidate_variants"),
                "options_degradation_survivors": option_research_summary.get("degradation_survivor_count"),
                "options_severe_survivors": option_research_summary.get("severe_survivor_count"),
                "regime_allocator_specs": auxiliary_research_summary.get("regime_allocator_specs"),
                "regime_allocator_effective_hypotheses": auxiliary_research_summary.get("regime_allocator_effective_hypotheses"),
                "regime_allocator_passes": auxiliary_research_summary.get("regime_allocator_passes"),
                "factor_rotation_specs": auxiliary_research_summary.get("factor_rotation_specs"),
                "factor_rotation_effective_hypotheses": auxiliary_research_summary.get("factor_rotation_effective_hypotheses"),
                "factor_rotation_passes": auxiliary_research_summary.get("factor_rotation_passes"),
                "dominant_auxiliary_failure_category": auxiliary_research_summary.get("dominant_failure_category"),
                "recent_regime_status": recent_regime_status.get("status"),
                "recent_regime_latest_session": recent_regime_summary.get("latest_session"),
                "recent_regime_components": recent_regime_summary.get("components"),
                "recent_regime_selectors": recent_regime_summary.get("selectors"),
                "recent_regime_selector_passes": recent_regime_summary.get("selector_passes"),
                "recent_regime_leader": recent_regime_summary.get("leader_selector_name"),
                "recent_regime_leader_2025_return_pct": recent_regime_summary.get("leader_2025_return_pct"),
                "recent_regime_leader_2025_spy_excess_pct": recent_regime_summary.get("leader_2025_spy_excess_pct"),
                "recent_regime_leader_2026_return_pct": recent_regime_summary.get("leader_2026_return_pct"),
                "recent_regime_leader_spy_excess_pct": recent_regime_summary.get("leader_spy_excess_pct"),
                "recent_regime_leader_combined_return_pct": recent_regime_summary.get("leader_combined_return_pct"),
                "recent_regime_gate_variants": recent_regime_summary.get("gate_variants"),
                "recent_regime_gate_passes": recent_regime_summary.get("gate_passes"),
                "recent_regime_gate_leader": recent_regime_summary.get("gate_leader_name"),
                "recent_prospective_matured_observations": recent_prospective_evaluation.get("matured_observations"),
                "recent_prospective_pending_observations": recent_prospective_evaluation.get("pending_observations"),
                "recent_component_leave_one_out_passed": recent_component_robustness_summary.get("leave_one_symbol_out_passed"),
                "recent_component_worst_2025_return_pct": recent_component_robustness_summary.get("worst_2025_return_pct"),
                "recent_component_worst_2026_ytd_return_pct": recent_component_robustness_summary.get("worst_2026_ytd_return_pct"),
                "cipher_signal_overlay_action": recent_regime_status.get("signal_overlay_action"),
                "cipher_signal_overlay_policies": signal_overlay_policy_family.get("count"),
                "cipher_signal_overlay_capture_sessions": signal_overlay_inventory.get("sessions"),
                "cipher_signal_overlay_episodes": signal_overlay_inventory.get("episodes"),
                "cipher_signal_overlay_matured_observations": signal_overlay_evaluation.get("matured_observations"),
                "cipher_signal_overlay_pending_observations": signal_overlay_evaluation.get("pending_observations"),
                "automatic_promotion": False,
            },
        )

    track4_state = "completed_real" if counts["news_events"] > 0 else "closed_data_insufficient"
    track4 = track(
        4,
        "Public news/event ingestion and FinBERT triage",
        track4_state,
        reason=(
            "Real public headline metadata was ingested and scored by revision-pinned local FinBERT; unavailable providers were recorded separately."
            if track4_state == "completed_real"
            else "No accessible public source produced real documents; the track closed without fabricated events."
        ),
        evidence=[str(event_path)] if event_path else [],
        metrics={
            "processed_events": processed_events,
            "registry_news_events": counts["news_events"],
            "source_statuses": {name: value.get("status") for name, value in event_sources.items() if isinstance(value, dict)},
        },
    )

    track5 = track(
        5,
        "Real forecast anomaly attribution",
        "completed_real" if counts["anomaly_events"] > 0 else "closed_data_insufficient",
        reason=(
            "Real forecast/outcome anomalies were recorded."
            if counts["anomaly_events"] > 0
            else "No validated forecast survived the unchanged cohort gate, so no synthetic or context-only anomaly was manufactured."
        ),
        evidence=[str(GOV / "research_registry.sqlite")],
        metrics={"registry_anomaly_events": counts["anomaly_events"], "validated_forecast_available": False if not cohort_pass else None},
    )

    track6 = track(
        6,
        "Bounded repair actions",
        "completed_infrastructure" if repair.get("status") == "complete" else "pending",
        reason="Allowlisted checksum and derived-cache repairs ran with immutable incidents and protected-field enforcement." if repair.get("status") == "complete" else "No completed bounded repair run is registered.",
        evidence=[str(repair_path)] if repair_path else [],
        metrics={"repairs_completed": repair.get("repairs_completed", 0)},
    )
    track7 = track(
        7,
        "Per-job safe scheduling",
        "completed_infrastructure" if scheduler_active else "pending",
        reason="A guarded local daemon is active and schedules only individually eligible operational jobs." if scheduler_active else "The guarded scheduler daemon is not active.",
        evidence=[str(scheduler_state_path)] if scheduler_state_path.exists() else [],
        metrics={"scheduler_active": scheduler_active, "registered_jobs": len(scheduler_state.get("jobs", {}))},
    )
    track8 = track(
        8,
        "Operator research-status UI",
        "completed_infrastructure" if ui_ready else "pending",
        reason="The local read-only UI and API expose real completed, skipped, and blocked track states." if ui_ready else "The research-status API/UI is not yet wired.",
        evidence=[str(ROOT / "core" / "app.py"), str(ROOT / "web" / "src" / "components" / "panels" / "Settings.tsx")],
        metrics={"ui_ready": ui_ready},
    )
    tracks = [track1, track2, track3, track4, track5, track6, track7, track8]
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "governing_policy": {
            "data_insufficient_action": "skip_and_close_honestly",
            "gate_relaxation_allowed": False,
            "synthetic_evidence_allowed_as_completion": False,
            "volume_sensitive_work_active": False,
            "live_execution": False,
        },
        "tracks": tracks,
        "closed_tracks": sum(item["closed"] for item in tracks),
        "total_tracks": len(tracks),
        "all_eight_closed": all(item["closed"] for item in tracks),
        "work_package_complete": all(item["closed"] for item in tracks),
        "architecture_complete": bool(architecture_audit.get("architecture_complete", False)),
        # This work package must never report an ambiguous architecture state. Preserve the
        # audit's boolean boundary while exposing the stable status vocabulary consumed by
        # operators and tests.
        "architecture_status": "COMPLETE" if architecture_audit.get("architecture_complete") else "INCOMPLETE",
        "architecture_phase_exit_criteria_met": int(architecture_audit.get("phase_exit_criteria_met") or 0),
        "architecture_operational_layers_complete": int(architecture_audit.get("operational_layers_complete") or 0),
        "architecture_audit_artifact": str(GOV / "original_architecture_self_audit.json"),
        "data_lineage": {
            "holdout_c": {
                "dataset_manifests": counts["holdout_c_dataset_manifests"],
                "raw_objects": counts["holdout_c_raw_objects"],
                "dataset_raw_links": counts["holdout_c_dataset_raw_links"],
                "canonical_frozen_lineage_complete": (
                    counts["holdout_c_dataset_manifests"] == 1
                    and counts["holdout_c_raw_objects"] == 744
                    and counts["holdout_c_dataset_raw_links"] == 744
                ),
                "strict_independent_origins": observed_origins,
                "required_strict_independent_origins": required_origins,
                "origin_gap": max(0, required_origins - observed_origins),
                "registration_closes_origin_gap": False,
            }
        },
        "operational_controls": {
            "build_test_healing": {
                "watcher_active": build_healing_active,
                "latest_status": build_healing_run.get("status"),
                "latest_source_fingerprint": build_healing_run.get("source_fingerprint"),
                "bounded_mechanical_healing_only": True,
                "source_code_auto_edit": False,
                "commit_or_push": False,
                "research_gate_changes": False,
                "execution_authority": False,
            },
            "strategy_research_loop": {
                "loop_active": strategy_research_active,
                "latest_status": strategy_research_run.get("status"),
                "latest_cycle_created_at": strategy_research_run.get("created_at"),
                "tested_candidate_count_total": strategy_research_run.get(
                    "tested_candidate_count_total",
                    len(strategy_research_state.get("tested_candidate_ids", [])),
                ),
                "latest_batch_size": len(strategy_research_run.get("results") or []),
                "adaptive_children_added": len(strategy_research_run.get("adaptive_children_added") or []),
                "leader_candidate_id": (
                    strategy_leader.get("candidate", {}).get("candidate_id")
                    if isinstance(strategy_leader, dict)
                    else None
                ),
                "leader_verdict": strategy_leader.get("verdict") if isinstance(strategy_leader, dict) else None,
                "leader_composite_score": strategy_leader.get("composite_score") if isinstance(strategy_leader, dict) else None,
                "canonical_strategy_specs": counts["autonomous_strategy_specs"],
                "canonical_experiments": counts["autonomous_experiments"],
                "passes": counts["autonomous_passes"],
                "conditional_passes": counts["autonomous_conditional_passes"],
                "failures": counts["autonomous_failures"],
                "promotion_events": counts["autonomous_promotion_events"],
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
                "research_role": "exploratory_development_only_not_final_holdout",
                "phase_two_enabled": True,
                "seed_candidate_count": 84,
                "candidate_cap": 240,
                "recent_regime_research": {
                    "status": recent_regime_status.get("status"),
                    "dataset": recent_regime_status.get("dataset"),
                    "data_refresh_action": recent_regime_status.get("data_refresh_action"),
                    "research_action": recent_regime_status.get("research_action"),
                    "operational_fingerprint": recent_regime_status.get("operational_fingerprint"),
                    "summary": recent_regime_summary,
                    "report_path": recent_regime_status.get("report_path"),
                    "prospective_snapshot": recent_regime_status.get("prospective_snapshot"),
                    "prospective_evaluation_action": recent_regime_status.get("prospective_evaluation_action"),
                    "prospective_evaluation": recent_prospective_evaluation,
                    "component_robustness_action": recent_regime_status.get("component_robustness_action"),
                    "component_robustness": recent_component_robustness,
                    "signal_overlay_action": recent_regime_status.get("signal_overlay_action"),
                    "signal_overlay": signal_overlay,
                    "research_grade": bool(recent_regime_status.get("research_grade", False)),
                    "research_role": "recent_2024_warmup_2025_2026_rolling_development_only_not_independent_holdout",
                    "automatic_promotion": False,
                    "paper_or_live_execution": False,
                    "execution_authority": False,
                },
                "phase3_broad_research": {
                    "status": phase3_research_run.get("status"),
                    "latest_cycle_created_at": phase3_research_run.get("created_at"),
                    "dataset": phase3_research_run.get("dataset"),
                    "tested_candidate_count_total": phase3_research_run.get(
                        "tested_candidate_count_total",
                        len(phase3_research_state.get("tested_candidate_ids", [])),
                    ),
                    "latest_batch_size": len(phase3_research_run.get("results") or []),
                    "adaptive_children_added": len(phase3_research_run.get("adaptive_children_added") or []),
                    "research_role": "broad_phase3_development_only",
                    "automatic_promotion": False,
                    "execution_authority": False,
                },
                "locked_temporal_validation": {
                    "status": locked_validation.get("status"),
                    "dataset": locked_validation.get("dataset"),
                    "candidate_family_freeze": locked_validation.get("candidate_family_freeze"),
                    "summary": locked_validation_summary,
                    "adaptive_feedback_allowed": False,
                    "automatic_promotion": False,
                    "execution_authority": False,
                },
                "locked_2026_ytd_validation": {
                    "status": ytd_validation.get("status"),
                    "dataset": ytd_validation.get("dataset"),
                    "candidate_identity_freeze": ytd_validation.get("candidate_identity_freeze"),
                    "summary": ytd_validation_summary,
                    "adaptive_feedback_allowed": False,
                    "automatic_promotion": False,
                    "execution_authority": False,
                },
                "ytd_2026_robustness": {
                    "status": ytd_robustness.get("status"),
                    "candidate_count": ytd_robustness.get("candidate_count"),
                    "robust_candidate_count": ytd_robustness.get("robust_candidate_count"),
                    "automatic_promotion": False,
                    "execution_authority": False,
                },
                "annual_regime_stability": {
                    "status": annual_stability.get("status"),
                    "candidate_count": annual_stability.get("candidate_count"),
                    "stable_candidate_count": annual_stability.get("stable_candidate_count"),
                    "family_summary": annual_stability.get("family_summary"),
                    "automatic_promotion": False,
                    "execution_authority": False,
                },
                "cross_period_consensus": {
                    "status": cross_period_matrix.get("status"),
                    "identity_key": cross_period_matrix.get("identity_key"),
                    "summary": cross_period_summary,
                    "multi_period_leaders": cross_period_matrix.get("multi_period_leaders", []),
                    "automatic_promotion": False,
                    "execution_authority": False,
                },
                "auxiliary_research": {
                    "status": auxiliary_research_status.get("status"),
                    "operational_fingerprint": auxiliary_research_status.get("operational_fingerprint"),
                    "summary": auxiliary_research_summary,
                    "reports": auxiliary_research_status.get("reports"),
                    "research_grade": bool(auxiliary_research_status.get("research_grade", False)),
                    "allowed_claim": auxiliary_research_summary.get("allowed_claim"),
                    "automatic_promotion": False,
                    "source_code_auto_edit": False,
                    "execution_authority": False,
                },
                "options_research": {
                    "status": option_research_status.get("status"),
                    "configuration": option_research_status.get("configuration"),
                    "candidate_variants": option_research_summary.get("candidate_variants"),
                    "holdout_months": option_research_summary.get("holdout_months"),
                    "degradation_survivor_count": option_research_summary.get("degradation_survivor_count"),
                    "severe_survivor_count": option_research_summary.get("severe_survivor_count"),
                    "allowed_claim": option_research_summary.get("allowed_claim"),
                    "research_grade": bool(option_research_status.get("research_grade", False)),
                    "automatic_promotion": False,
                    "execution_authority": False,
                },
                "automatic_promotion": False,
                "lean_replication": False,
                "paper_or_live_execution": False,
                "execution_authority": False,
            },
            "unified_product": {
                "audit_available": bool(unified_product_audit),
                "verdict": unified_product_audit.get("verdict"),
                "complete": bool(unified_product_audit.get("unified_product_complete", False)),
                "canonical_source": (
                    unified_product_audit.get("paths", {})
                    .get("canonical_source", {})
                    .get("resolved")
                ),
                "runtime_data": (
                    unified_product_audit.get("paths", {})
                    .get("runtime_data", {})
                    .get("resolved")
                ),
                "execution_authority": False,
            },
            "post_merge_verification": {
                "audit_available": bool(post_merge_verification),
                "verdict": post_merge_verification.get("verdict"),
                "passed": bool(post_merge_verification.get("verification_passed", False)),
                "known_canonical_lineage_gap": bool(
                    post_merge_verification.get(
                        "known_canonical_lineage_gap",
                        any(
                            item.get("id") == "holdout_c_canonical_lineage_absent"
                            for item in post_merge_verification.get("known_gaps", [])
                            if isinstance(item, dict)
                        ),
                    )
                ),
                "artifact": str(GOV / "post_merge_verification.json"),
                "execution_authority": False,
            },
        },
        "maximum_promotion_state": "LIVE_REVIEW_REQUIRED",
        "live_execution": False,
    }


def main() -> int:
    GOV.mkdir(parents=True, exist_ok=True)
    payload = build_status()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = GOV / f"master_end_state_status_{stamp}.json"
    stable = GOV / "master_end_state_status.json"
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    timestamped.write_text(encoded, encoding="utf-8")
    stable.write_text(encoded, encoding="utf-8")
    print(json.dumps({"path": str(stable), "closed_tracks": payload["closed_tracks"], "all_eight_closed": payload["all_eight_closed"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
