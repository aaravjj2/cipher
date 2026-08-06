"""Immutable prospective snapshots for recent-regime research.

The first snapshot written for a market session is canonical and is never
replaced. Later reruns with different content preserve the original and write a
separate conflict record. This module records research observations only and
has no promotion, paper-execution, broker, or order authority.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .hashing import stable_id


def build_prospective_snapshot(
    report: Mapping[str, Any],
    *,
    created_at: datetime,
    observation_mode: str,
) -> dict[str, Any]:
    dataset = dict(report.get("dataset") or {})
    latest_session = str(dataset.get("latest_session") or "")
    if not latest_session:
        raise RuntimeError("recent-regime report has no latest session")

    component_map: dict[str, Mapping[str, Any]] = {}
    for row in report.get("component_records") or []:
        if not isinstance(row, Mapping):
            continue
        candidate = row.get("candidate") or {}
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id:
            component_map[candidate_id] = row

    selectors: list[dict[str, Any]] = []
    for row in report.get("selector_records") or []:
        if not isinstance(row, Mapping):
            continue
        current = dict(row.get("current_selection") or {})
        selected_components: list[dict[str, Any]] = []
        for candidate_id in current.get("selected_components") or []:
            component = component_map.get(str(candidate_id), {})
            candidate = component.get("candidate") or {}
            selected_components.append(
                {
                    "candidate_id": str(candidate_id),
                    "family": candidate.get("family"),
                    "parameters": candidate.get("parameters"),
                    "active_symbols": component.get("active_symbols_at_end") or [],
                }
            )
        selectors.append(
            {
                "selector_id": row.get("selector_id"),
                "selector_name": (row.get("spec") or {}).get("name"),
                "verdict": row.get("verdict"),
                "current_selection": current,
                "selected_components": selected_components,
                "metrics_2025": row.get("metrics_2025"),
                "metrics_2026_ytd": row.get("metrics_2026_ytd"),
                "metrics_combined": row.get("metrics_combined"),
            }
        )

    gates: list[dict[str, Any]] = []
    for row in report.get("gate_records") or []:
        if not isinstance(row, Mapping):
            continue
        gates.append(
            {
                "hypothesis_id": row.get("hypothesis_id"),
                "base_selector_name": row.get("base_selector_name"),
                "gate_id": row.get("gate_id"),
                "gate_name": row.get("gate_name"),
                "gate_condition": row.get("gate_condition"),
                "verdict": row.get("verdict"),
                "current_gate_decision": row.get("current_gate_decision"),
                "current_effective_selection": row.get("current_effective_selection"),
                "metrics_2025": row.get("metrics_2025"),
                "metrics_2026_ytd": row.get("metrics_2026_ytd"),
                "metrics_combined": row.get("metrics_combined"),
            }
        )

    core = {
        "schema_version": 1,
        "market_session": latest_session,
        "created_at": created_at.isoformat(),
        "observation_mode": observation_mode,
        "dataset": {
            "dataset_id": dataset.get("dataset_id"),
            "dataset_name": dataset.get("dataset_name"),
            "lineage_hash": dataset.get("lineage_hash"),
            "latest_session": latest_session,
            "evaluation_end": dataset.get("evaluation_end"),
        },
        "candidate_pool": {
            "count": (report.get("candidate_pool") or {}).get("count"),
            "hash": (report.get("candidate_pool") or {}).get("hash"),
            "selection_rule": (report.get("candidate_pool") or {}).get("selection_rule"),
        },
        "leader": {
            "selector_id": (report.get("summary") or {}).get("leader_selector_id"),
            "selector_name": (report.get("summary") or {}).get("leader_selector_name"),
            "verdict": (report.get("summary") or {}).get("leader_verdict"),
            "current_selection": (report.get("summary") or {}).get("current_selection"),
            "current_selected_components": (report.get("summary") or {}).get("current_selected_components"),
        },
        "selectors": selectors,
        "gates": gates,
        "research_role": "prospective_observation_of_exploratory_recent_regime_research",
        "independent_holdout": False,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    identity_payload = {key: value for key, value in core.items() if key != "created_at"}
    core["snapshot_id"] = stable_id("recent_regime_prospective_snapshot", identity_payload, length=64)
    return core


def write_immutable_prospective_snapshot(
    report: Mapping[str, Any],
    *,
    root: str | Path,
    created_at: datetime,
) -> dict[str, Any]:
    root_path = Path(root)
    snapshots = root_path / "snapshots"
    conflicts = root_path / "conflicts"
    snapshots.mkdir(parents=True, exist_ok=True)
    conflicts.mkdir(parents=True, exist_ok=True)

    existing_snapshots = sorted(snapshots.glob("*.json"))
    observation_mode = "initial_baseline" if not existing_snapshots else "prospective_after_close"
    snapshot = build_prospective_snapshot(report, created_at=created_at, observation_mode=observation_mode)
    session = str(snapshot["market_session"])
    canonical_path = snapshots / f"{session}.json"

    if canonical_path.is_file():
        existing = json.loads(canonical_path.read_text(encoding="utf-8"))
        if existing.get("snapshot_id") == snapshot.get("snapshot_id"):
            status = "existing_immutable_snapshot"
            canonical = existing
            conflict_path = None
        else:
            conflict_path = conflicts / f"{session}_{snapshot['snapshot_id']}.json"
            if not conflict_path.exists():
                _write_json_atomic(conflict_path, snapshot)
            status = "immutable_conflict_preserved"
            canonical = existing
    else:
        _write_json_atomic(canonical_path, snapshot)
        status = "created_immutable_snapshot"
        canonical = snapshot
        conflict_path = None

    latest_payload = {
        "schema_version": 1,
        "updated_at": created_at.isoformat(),
        "status": status,
        "market_session": session,
        "snapshot_id": canonical.get("snapshot_id"),
        "snapshot_path": str(canonical_path),
        "conflict_path": str(conflict_path) if conflict_path else None,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    _write_json_atomic(root_path / "latest.json", latest_payload)
    return latest_payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)
