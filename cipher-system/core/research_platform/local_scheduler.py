"""A file-backed, research-only scheduler for the guarded seven-layer stack.

It deliberately schedules only local, non-execution work.  Jobs whose evidence
or runtime prerequisites are absent are written as blocked rather than invoked.
An external service manager may call ``run_due`` at the desired cadence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class LocalResearchJob:
    job_id: str
    layer: int
    cadence: timedelta
    purpose: str
    prerequisite: Callable[[dict[str, Any]], str | None]


def default_jobs() -> tuple[LocalResearchJob, ...]:
    def model_context_blocker(capabilities: dict[str, Any]) -> str | None:
        if not capabilities["models"]["kronos"]["ready_for_inference"]:
            return "kronos_runtime_or_weights_unavailable"
        return None

    def factor_blocker(capabilities: dict[str, Any]) -> str | None:
        packages = capabilities["research_packages"]
        if not (packages["qlib"] and packages["rdagent"]):
            return "qlib_or_rdagent_runtime_unavailable"
        return "holdout_c_cohort_not_cleared"

    def anomaly_blocker(capabilities: dict[str, Any]) -> str | None:
        if not capabilities["models"]["timesfm"]["ready_for_prospective_forecast"]:
            return "validated_forecast_inputs_unavailable"
        return None

    return (
        LocalResearchJob("model_context_snapshot", 2, timedelta(days=1), "Record non-actionable model context.", model_context_blocker),
        LocalResearchJob("factor_research", 3, timedelta(days=7), "Run offline factor discovery after cohort clearance.", factor_blocker),
        LocalResearchJob("forecast_anomaly_attribution", 4, timedelta(days=1), "Attribute forecast misses after validated inputs exist.", anomaly_blocker),
        LocalResearchJob("autoresearch_feedback", 7, timedelta(days=7), "Write feedback only after validation artifacts exist.", factor_blocker),
    )


def run_due(
    capabilities: dict[str, Any],
    state_path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record due job decisions.  No subprocesses, vendors, or orders are called."""

    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    path = Path(state_path)
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": []}
    runs = list(state.get("runs", []))
    events: list[dict[str, Any]] = []
    for job in default_jobs():
        previous = next((item for item in reversed(runs) if item.get("job_id") == job.job_id), None)
        due = previous is None or timestamp - datetime.fromisoformat(previous["checked_at"]) >= job.cadence
        if not due:
            continue
        blocker = job.prerequisite(capabilities)
        event = {
            "job_id": job.job_id,
            "layer": job.layer,
            "purpose": job.purpose,
            "checked_at": timestamp.isoformat(),
            "status": "blocked" if blocker else "ready_for_manual_research_run",
            "blocker": blocker,
            "live_order_authority": False,
        }
        runs.append(event)
        events.append(event)
    result = {"schema_version": 1, "runs": runs, "last_run": events}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
