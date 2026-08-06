"""Deterministic outcome scoring for immutable recent-regime snapshots.

Snapshots are scored only with future session opens.  A one-session outcome
requires two opens after the snapshot session: the next session open is the
entry and the following session open is the exit.  Longer horizons use the same
frozen basket and never substitute closes or partial marks.

Each matured observation/horizon is immutable.  Later recalculation conflicts
are preserved separately.  This module cannot promote, paper trade, or submit
orders.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .hashing import stable_id

DEFAULT_HORIZONS = (1, 5, 21)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _component_symbol_weights(
    selection: Mapping[str, Any],
    selected_components: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, float], list[str]]:
    declared = dict(selection.get("weights") or {})
    missing: list[str] = []
    symbol_weights: dict[str, float] = {}

    passive = float(declared.get("passive_spy") or 0.0)
    if passive > 0:
        symbol_weights["SPY"] = symbol_weights.get("SPY", 0.0) + passive

    components = {
        str(component.get("candidate_id")): component
        for component in selected_components
        if component.get("candidate_id")
    }
    for candidate_id, raw_weight in declared.items():
        if candidate_id == "passive_spy":
            continue
        component_weight = float(raw_weight or 0.0)
        if component_weight <= 0:
            continue
        component = components.get(str(candidate_id))
        if not component:
            missing.append(f"component:{candidate_id}")
            continue
        symbols = sorted(
            {
                str(item.get("symbol")).upper()
                for item in component.get("active_symbols") or []
                if item.get("symbol")
            }
        )
        if not symbols:
            missing.append(f"active_symbols:{candidate_id}")
            continue
        per_symbol = component_weight / len(symbols)
        for symbol in symbols:
            symbol_weights[symbol] = symbol_weights.get(symbol, 0.0) + per_symbol

    gross = sum(abs(value) for value in symbol_weights.values())
    if gross > 1.0 + 1e-9:
        raise ValueError(f"prospective basket gross exposure exceeds one: {gross}")
    return dict(sorted(symbol_weights.items())), missing


def _generic_symbol_weights(payload: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    missing: list[str] = []
    symbol_weights: dict[str, float] = {}
    for raw_symbol, raw_weight in dict(payload.get("symbol_weights") or {}).items():
        symbol = str(raw_symbol or "").upper().strip()
        if not symbol:
            missing.append("blank_symbol")
            continue
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            missing.append(f"invalid_weight:{symbol}")
            continue
        if not math.isfinite(weight) or weight < 0.0:
            missing.append(f"invalid_weight:{symbol}")
            continue
        if weight > 0.0:
            symbol_weights[symbol] = symbol_weights.get(symbol, 0.0) + weight
    gross = sum(symbol_weights.values())
    if gross > 1.0 + 1e-9:
        raise ValueError(f"prospective basket gross exposure exceeds one: {gross}")
    return dict(sorted(symbol_weights.items())), missing


def snapshot_observations(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    leader = dict(snapshot.get("leader") or {})
    if leader:
        leader_weights, leader_missing = _component_symbol_weights(
            dict(leader.get("current_selection") or {}),
            list(leader.get("current_selected_components") or []),
        )
        observations.append(
            {
                "observation_type": "leader_selector",
                "observation_name": leader.get("selector_name"),
                "observation_id": leader.get("selector_id"),
                "selection": leader.get("current_selection"),
                "symbol_weights": leader_weights,
                "missing_inputs": leader_missing,
            }
        )

    gates = list(snapshot.get("gates") or [])
    if gates:
        gate = dict(gates[0])
        effective = dict(gate.get("current_effective_selection") or {})
        base_name = str(gate.get("base_selector_name") or "")
        matching_selector = next(
            (
                row
                for row in snapshot.get("selectors") or []
                if str(row.get("selector_name") or "") == base_name
            ),
            {},
        )
        gate_weights, gate_missing = _component_symbol_weights(
            effective,
            list(matching_selector.get("selected_components") or []),
        )
        observations.append(
            {
                "observation_type": "leader_gate",
                "observation_name": gate.get("gate_name"),
                "observation_id": gate.get("hypothesis_id") or gate.get("gate_id"),
                "selection": effective,
                "gate_decision": gate.get("current_gate_decision"),
                "symbol_weights": gate_weights,
                "missing_inputs": gate_missing,
            }
        )

    for raw in snapshot.get("observations") or []:
        if not isinstance(raw, Mapping):
            continue
        weights, missing = _generic_symbol_weights(raw)
        observations.append(
            {
                "observation_type": raw.get("observation_type") or "generic_observation",
                "observation_name": raw.get("observation_name"),
                "observation_id": raw.get("observation_id"),
                "selection": raw.get("selection"),
                "symbol_weights": weights,
                "missing_inputs": [*(raw.get("missing_inputs") or []), *missing],
                "metadata": raw.get("metadata"),
            }
        )
    return observations


def _score_horizon(
    opens: pd.DataFrame,
    *,
    market_session: str,
    symbol_weights: Mapping[str, float],
    horizon_sessions: int,
) -> dict[str, Any]:
    session = pd.Timestamp(market_session)
    future = pd.DatetimeIndex(opens.index[opens.index > session]).sort_values().unique()
    required_future_opens = int(horizon_sessions) + 1
    if len(future) < required_future_opens:
        return {
            "status": "pending_future_opens",
            "horizon_sessions": int(horizon_sessions),
            "required_future_opens": required_future_opens,
            "available_future_opens": int(len(future)),
        }
    entry_session = pd.Timestamp(future[0])
    exit_session = pd.Timestamp(future[horizon_sessions])
    symbols = sorted(set(symbol_weights) | {"SPY"})
    missing_symbols = [symbol for symbol in symbols if symbol not in opens.columns]
    if missing_symbols:
        return {
            "status": "unscorable_missing_symbols",
            "horizon_sessions": int(horizon_sessions),
            "missing_symbols": missing_symbols,
        }
    entry = opens.loc[entry_session, symbols]
    exit_values = opens.loc[exit_session, symbols]
    invalid = [
        symbol
        for symbol in symbols
        if pd.isna(entry[symbol])
        or pd.isna(exit_values[symbol])
        or float(entry[symbol]) <= 0
        or float(exit_values[symbol]) <= 0
    ]
    if invalid:
        return {
            "status": "unscorable_missing_opens",
            "horizon_sessions": int(horizon_sessions),
            "invalid_symbols": invalid,
            "entry_session": entry_session.date().isoformat(),
            "exit_session": exit_session.date().isoformat(),
        }

    symbol_returns = {
        symbol: float(exit_values[symbol] / entry[symbol] - 1.0)
        for symbol in sorted(symbol_weights)
    }
    strategy_return = sum(float(symbol_weights[symbol]) * symbol_returns[symbol] for symbol in symbol_returns)
    spy_return = float(exit_values["SPY"] / entry["SPY"] - 1.0)
    return {
        "status": "matured",
        "horizon_sessions": int(horizon_sessions),
        "entry_session": entry_session.date().isoformat(),
        "exit_session": exit_session.date().isoformat(),
        "symbol_weights": dict(symbol_weights),
        "symbol_returns_pct": {symbol: value * 100.0 for symbol, value in symbol_returns.items()},
        "strategy_return_pct": strategy_return * 100.0,
        "spy_return_pct": spy_return * 100.0,
        "strategy_excess_return_pct": (strategy_return - spy_return) * 100.0,
    }


def _persist_matured_result(
    *,
    root: Path,
    snapshot: Mapping[str, Any],
    observation: Mapping[str, Any],
    scored: Mapping[str, Any],
    evaluated_at: datetime,
    dataset: Mapping[str, Any],
) -> dict[str, Any]:
    observation_key = stable_id(
        "recent_prospective_observation",
        {
            "snapshot_id": snapshot.get("snapshot_id"),
            "observation_type": observation.get("observation_type"),
            "observation_id": observation.get("observation_id"),
        },
        length=24,
    )
    horizon = int(scored["horizon_sessions"])
    result = {
        "schema_version": 1,
        "snapshot_id": snapshot.get("snapshot_id"),
        "market_session": snapshot.get("market_session"),
        "observation_type": observation.get("observation_type"),
        "observation_name": observation.get("observation_name"),
        "observation_id": observation.get("observation_id"),
        "observation_key": observation_key,
        "evaluated_at": evaluated_at.isoformat(),
        "dataset": dict(dataset),
        "result": dict(scored),
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    identity = {key: value for key, value in result.items() if key != "evaluated_at"}
    result["result_id"] = stable_id("recent_prospective_result", identity, length=64)
    canonical = root / "evaluations" / str(snapshot.get("market_session")) / f"{observation_key}_{horizon:02d}.json"
    conflicts = root / "evaluation_conflicts"
    if canonical.is_file():
        existing = _read_json(canonical)
        if existing.get("result_id") == result.get("result_id"):
            return {
                "status": "existing_immutable_result",
                "path": str(canonical),
                "result": existing,
            }
        conflict = conflicts / f"{snapshot.get('market_session')}_{result['result_id']}.json"
        if not conflict.is_file():
            _atomic_json(conflict, result)
        return {
            "status": "immutable_result_conflict_preserved",
            "path": str(canonical),
            "conflict_path": str(conflict),
            "result": existing,
        }
    _atomic_json(canonical, result)
    return {"status": "created_immutable_result", "path": str(canonical), "result": result}


def _aggregate_one_session(records: Iterable[Mapping[str, Any]], observation_type: str) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    for row in records:
        result = row.get("result") or {}
        if row.get("observation_type") != observation_type:
            continue
        if int(result.get("horizon_sessions") or 0) != 1:
            continue
        selected.append(dict(row))
    selected.sort(key=lambda row: str(row.get("market_session")))
    strategy_factor = 1.0
    spy_factor = 1.0
    wins = 0
    for row in selected:
        result = row["result"]
        strategy = float(result["strategy_return_pct"]) / 100.0
        spy = float(result["spy_return_pct"]) / 100.0
        strategy_factor *= 1.0 + strategy
        spy_factor *= 1.0 + spy
        wins += int(strategy > spy)
    return {
        "scored_sessions": len(selected),
        "strategy_return_pct": (strategy_factor - 1.0) * 100.0,
        "spy_return_pct": (spy_factor - 1.0) * 100.0,
        "strategy_excess_return_pct": (strategy_factor - spy_factor) * 100.0,
        "benchmark_beating_sessions": wins,
        "benchmark_beating_fraction": wins / len(selected) if selected else None,
        "first_market_session": selected[0].get("market_session") if selected else None,
        "last_market_session": selected[-1].get("market_session") if selected else None,
    }


def evaluate_prospective_snapshots(
    *,
    opens: pd.DataFrame,
    snapshot_paths: Sequence[str | Path],
    root: str | Path,
    dataset: Mapping[str, Any],
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    now = evaluated_at or datetime.now(timezone.utc)
    root_path = Path(root)
    opens = opens.copy()
    opens.index = pd.to_datetime(opens.index).tz_localize(None)
    opens = opens.sort_index()

    status_rows: list[dict[str, Any]] = []
    immutable_results: list[dict[str, Any]] = []
    for path_value in sorted(Path(path) for path in snapshot_paths):
        snapshot = _read_json(path_value)
        if not snapshot:
            continue
        for observation in snapshot_observations(snapshot):
            if observation.get("missing_inputs") or not observation.get("symbol_weights"):
                status_rows.append(
                    {
                        "market_session": snapshot.get("market_session"),
                        "snapshot_id": snapshot.get("snapshot_id"),
                        "observation_type": observation.get("observation_type"),
                        "observation_name": observation.get("observation_name"),
                        "observation_id": observation.get("observation_id"),
                        "status": "unscorable_observation",
                        "missing_inputs": observation.get("missing_inputs"),
                    }
                )
                continue
            for horizon in horizons:
                scored = _score_horizon(
                    opens,
                    market_session=str(snapshot.get("market_session")),
                    symbol_weights=dict(observation["symbol_weights"]),
                    horizon_sessions=int(horizon),
                )
                status = {
                    "market_session": snapshot.get("market_session"),
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "observation_type": observation.get("observation_type"),
                    "observation_name": observation.get("observation_name"),
                    "observation_id": observation.get("observation_id"),
                    "horizon_sessions": int(horizon),
                    **scored,
                }
                if scored.get("status") == "matured":
                    persisted = _persist_matured_result(
                        root=root_path,
                        snapshot=snapshot,
                        observation=observation,
                        scored=scored,
                        evaluated_at=now,
                        dataset=dataset,
                    )
                    immutable = dict(persisted.get("result") or {})
                    immutable_results.append(
                        {
                            "market_session": immutable.get("market_session"),
                            "observation_type": immutable.get("observation_type"),
                            "observation_name": immutable.get("observation_name"),
                            "observation_id": immutable.get("observation_id"),
                            "result": immutable.get("result"),
                            "path": persisted.get("path"),
                        }
                    )
                    status["persistence_status"] = persisted.get("status")
                    status["result_path"] = persisted.get("path")
                    if persisted.get("conflict_path"):
                        status["conflict_path"] = persisted.get("conflict_path")
                status_rows.append(status)

    matured = sum(row.get("status") == "matured" for row in status_rows)
    pending = sum(row.get("status") == "pending_future_opens" for row in status_rows)
    observation_keys = sorted(
        {
            (str(row.get("observation_type") or ""), str(row.get("observation_id") or ""))
            for row in immutable_results
            if row.get("observation_type") and row.get("observation_id")
        }
    )
    one_session_by_observation = [
        {
            "observation_type": observation_type,
            "observation_id": observation_id,
            "metrics": _aggregate_one_session(
                [
                    row
                    for row in immutable_results
                    if str(row.get("observation_type") or "") == observation_type
                    and str(row.get("observation_id") or "") == observation_id
                ],
                observation_type,
            ),
        }
        for observation_type, observation_id in observation_keys
    ]
    summary = {
        "schema_version": 1,
        "updated_at": now.isoformat(),
        "status": "completed",
        "dataset": dict(dataset),
        "snapshots": len({str(row.get("snapshot_id")) for row in status_rows if row.get("snapshot_id")}),
        "observations": len(status_rows),
        "matured_observations": matured,
        "pending_observations": pending,
        "leader_selector_one_session": _aggregate_one_session(immutable_results, "leader_selector"),
        "leader_gate_one_session": _aggregate_one_session(immutable_results, "leader_gate"),
        "one_session_by_observation": one_session_by_observation,
        "observation_status": status_rows,
        "research_role": "prospective_frozen_basket_outcome_tracking_only",
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    _atomic_json(root_path / "latest_evaluation_summary.json", summary)
    return summary
