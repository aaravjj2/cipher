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
        return {"news_events": 0, "anomaly_events": 0}
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as db:
        tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        return {
            name: int(db.execute(f"select count(*) from {name}").fetchone()[0]) if name in tables else 0
            for name in ("news_events", "anomaly_events")
        }


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

    counts = registry_counts()
    app_text = (ROOT / "core" / "app.py").read_text(encoding="utf-8", errors="ignore")
    js_text = (ROOT / "app" / "public" / "app.js").read_text(encoding="utf-8", errors="ignore")
    html_text = (ROOT / "app" / "public" / "index.html").read_text(encoding="utf-8", errors="ignore")
    ui_ready = "/api/research-status" in app_text and "renderResearchStatus" in js_text and 'data-view="researchStatus"' in html_text

    if cohort_pass:
        track1 = track(1, "Price-only Qlib/RD-Agent factor discovery", "completed_real", reason="A frozen cohort passed and the governed factor study completed.", evidence=[str(factor_path)] if factor_path else [], metrics={"strict_independent_origins": observed_origins})
        track2 = track(2, "Expanded price-only model study", "completed_real", reason="A frozen cohort passed and the preregistered model study completed.", evidence=[str(model_path)] if model_path else [], metrics={"strict_independent_origins": observed_origins})
        track3 = track(3, "Full-scale price-only backtesting", "pending", reason="The cohort passed but no qualifying full backtest close-out is registered.", evidence=[])
    else:
        common = {
            "strict_independent_origins": observed_origins,
            "required_strict_independent_origins": required_origins,
            "gate_relaxed": False,
        }
        track1 = track(1, "Price-only Qlib/RD-Agent factor discovery", "closed_data_insufficient", reason="Corrected window-wide cohort eligibility produced fewer than the required independent origins; prior pooled screening is exploratory only.", evidence=[str(path) for path in (cohort_path, factor_path) if path], metrics=common)
        track2 = track(2, "Expanded price-only model study", "closed_data_insufficient", reason="No model was rerun because the frozen cohort failed its unchanged origin requirement.", evidence=[str(path) for path in (cohort_path, model_path) if path], metrics=common)
        track3 = track(3, "Full-scale price-only backtesting", "closed_data_insufficient", reason="VectorBT/LEAN strategy evaluation was skipped because the same frozen cohort failed before outcomes or strategy selection.", evidence=[str(cohort_path)] if cohort_path else [], metrics=common)

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
        evidence=[str(ROOT / "core" / "app.py"), str(ROOT / "app" / "public" / "app.js")],
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
        "architecture_complete": False,
        "architecture_status": "not_measured_by_this_work_package",
        "architecture_audit_artifact": str(GOV / "original_architecture_self_audit.json"),
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
