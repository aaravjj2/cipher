"""Recurring guarded refresh for Cipher's historical option walk-forward study.

The strategy loop's primary search uses the canonical price-only panel.  This
module adds a separate options-specific branch built on the existing immutable
EOD option outcome archive.  It reruns a fixed nested walk-forward configuration
only when its source inputs change, then records degradation-aware evidence.

Historical NBBO, IV, Greeks, and complete point-in-time open interest are not
available.  Results therefore remain conservative one-minute trade-bar
approximations and cannot promote or execute a strategy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .hashing import sha256_file, stable_id


CONFIG = {
    "train_window_sessions": 80,
    "max_candidates": 3,
    "overlap_threshold": 0.65,
    "configuration_id": "rolling80_top3_overlap065",
}


def refresh_option_research(
    *,
    system_root: str | Path,
    state_path: str | Path,
    status_path: str | Path,
    force: bool = False,
    timeout_seconds: int = 1200,
) -> dict[str, Any]:
    root = Path(system_root).resolve()
    state_path = Path(state_path)
    status_path = Path(status_path)
    script = root / "core" / "eod_option_walkforward.py"
    equity_db = root / "data" / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite"
    outcomes = root / "data" / "eod_option_pattern_lab" / "daily_option_outcomes.csv"
    output_dir = root / "data" / "eod_option_walkforward_phase2_rolling80_top3"
    report_path = output_dir / "report.json"
    required = (script, equity_db, outcomes)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        payload = _base_payload("blocked_missing_inputs")
        payload.update({"missing_inputs": missing, "report_path": str(report_path)})
        _write_json(status_path, payload)
        return payload

    operational_fingerprint = stable_id(
        "option_refresh_inputs",
        {
            "config": CONFIG,
            "files": {
                str(path): {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
                for path in required
            },
        },
        length=64,
    )
    state = _read_json(state_path)
    previous_fingerprint = state.get("operational_fingerprint")
    should_run = bool(force or not report_path.is_file() or (previous_fingerprint and previous_fingerprint != operational_fingerprint))
    seeded_existing = bool(report_path.is_file() and not state and not force)
    if not should_run and state and status_path.is_file():
        existing = _read_json(status_path)
        if existing.get("operational_fingerprint") == operational_fingerprint:
            payload = {
                **existing,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "status": "not_due_inputs_unchanged",
                "process": None,
            }
            _write_json(status_path, payload)
            return payload

    command = [
        str(Path(sys.executable).resolve()),
        str(script),
        "--equity-db",
        str(equity_db),
        "--outcomes",
        str(outcomes),
        "--output-dir",
        str(output_dir),
        "--train-window-sessions",
        str(CONFIG["train_window_sessions"]),
        "--max-candidates",
        str(CONFIG["max_candidates"]),
        "--overlap-threshold",
        str(CONFIG["overlap_threshold"]),
    ]
    process_record: dict[str, Any] | None = None
    if should_run:
        started = datetime.now(timezone.utc)
        completed = subprocess.run(
            command,
            cwd=root.parent,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "CIPHER_OPTION_RESEARCH_REFRESH": "1"},
        )
        process_record = {
            "started_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "command": command,
        }
        if completed.returncode != 0 or not report_path.is_file():
            payload = _base_payload("failed")
            payload.update(
                {
                    "operational_fingerprint": operational_fingerprint,
                    "process": process_record,
                    "report_path": str(report_path),
                }
            )
            _write_json(status_path, payload)
            return payload

    report = _read_json(report_path)
    if not report:
        payload = _base_payload("failed_invalid_report")
        payload.update({"operational_fingerprint": operational_fingerprint, "report_path": str(report_path)})
        _write_json(status_path, payload)
        return payload

    summary = summarize_option_report(report)
    exact_hashes = {
        "walkforward_script_sha256": sha256_file(script),
        "outcomes_sha256": sha256_file(outcomes),
        "equity_database_sha256": sha256_file(equity_db),
        "report_sha256": sha256_file(report_path),
    }
    status = "completed" if should_run else "seeded_existing_report" if seeded_existing else "not_due_inputs_unchanged"
    payload = {
        **_base_payload(status),
        "configuration": dict(CONFIG),
        "operational_fingerprint": operational_fingerprint,
        "source_hashes": exact_hashes,
        "report_path": str(report_path),
        "report_generated_at": report.get("generated_at"),
        "summary": summary,
        "process": process_record,
        "research_grade": False,
        "research_grade_reason": report.get("research_grade_reason") or (
            "Historical NBBO, IV, and Greeks are unavailable; execution uses conservative trade-bar approximations."
        ),
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    state_payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "operational_fingerprint": operational_fingerprint,
        "report_path": str(report_path),
        "report_sha256": exact_hashes["report_sha256"],
        "last_refresh_status": status,
        "configuration": dict(CONFIG),
        "execution_authority": False,
    }
    _write_json(state_path, state_payload)
    _write_json(status_path, payload)
    return payload


def summarize_option_report(report: Mapping[str, Any]) -> dict[str, Any]:
    aggregate = [item for item in report.get("aggregate_results", []) if isinstance(item, Mapping)]
    grouped: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in aggregate:
        grouped.setdefault(str(row.get("policy")), {})[str(row.get("execution_model"))] = row

    degradation_survivors: list[dict[str, Any]] = []
    severe_survivors: list[dict[str, Any]] = []
    for policy, models in sorted(grouped.items()):
        base_row = models.get("base")
        worse_row = models.get("worse")
        severe_row = models.get("severe")
        if base_row and worse_row and _positive_model(base_row) and _positive_model(worse_row):
            degradation_survivors.append(_policy_summary(policy, models))
        if base_row and worse_row and severe_row and all(_positive_model(row) for row in (base_row, worse_row, severe_row)):
            severe_survivors.append(_policy_summary(policy, models))

    best_base = max(
        (row for row in aggregate if row.get("execution_model") == "base"),
        key=lambda row: float(row.get("pnl_on_deployed_risk_pct") or -1e9),
        default=None,
    )
    best_worse = max(
        (row for row in aggregate if row.get("execution_model") == "worse"),
        key=lambda row: float(row.get("pnl_on_deployed_risk_pct") or -1e9),
        default=None,
    )
    return {
        "analysis_start": report.get("analysis_start"),
        "analysis_end": report.get("analysis_end"),
        "holdout_months": list(report.get("holdout_months") or []),
        "candidate_variants": int(report.get("candidate_variants") or 0),
        "outcome_rows_loaded": int(report.get("outcome_rows_loaded") or 0),
        "selection_policies": list(report.get("selection_policies") or []),
        "aggregate_result_count": len(aggregate),
        "degradation_survivor_count": len(degradation_survivors),
        "severe_survivor_count": len(severe_survivors),
        "degradation_survivors": degradation_survivors,
        "severe_survivors": severe_survivors,
        "best_base": dict(best_base) if best_base else None,
        "best_worse": dict(best_worse) if best_worse else None,
        "allowed_claim": (
            "one_or_more_policies_survived_base_and_worse_but_not_required_severe_execution"
            if degradation_survivors and not severe_survivors
            else "one_or_more_policies_survived_all_three_execution_models"
            if severe_survivors
            else "no_policy_survived_base_and_worse_execution"
        ),
        "promotion_eligible": False,
    }


def _positive_model(row: Mapping[str, Any]) -> bool:
    return (
        int(row.get("trades") or 0) >= 20
        and float(row.get("total_pnl_dollars") or 0.0) > 0.0
        and float(row.get("pnl_on_deployed_risk_pct") or 0.0) > 0.0
        and float(row.get("profit_factor") or 0.0) > 1.0
    )


def _policy_summary(policy: str, models: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "policy": policy,
        "models": {
            name: {
                "trades": int(row.get("trades") or 0),
                "total_pnl_dollars": row.get("total_pnl_dollars"),
                "pnl_on_deployed_risk_pct": row.get("pnl_on_deployed_risk_pct"),
                "profit_factor": row.get("profit_factor"),
                "max_drawdown_dollars": row.get("max_drawdown_dollars"),
            }
            for name, row in sorted(models.items())
        },
    }


def _base_payload(status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "branch": "historical_options_nested_walk_forward",
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)
