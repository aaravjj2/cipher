"""Read-only operator summary for the staged paper autopilot."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from core.paper_executor.autopilot_planner import phase_at


ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT_DIR = ROOT / "data" / "paper_runtime" / "autopilot"
PLAN = AUTOPILOT_DIR / "premarket_plan.json"
STATUS = AUTOPILOT_DIR / "status.json"
TRAINING = AUTOPILOT_DIR / "training" / "manifest.json"


def _cycle_trace(now: datetime) -> dict[str, Any]:
    market_date = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    path = AUTOPILOT_DIR / "cycles" / f"{market_date}.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-250:]:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, json.JSONDecodeError):
        pass
    actions = Counter(str(row.get("action") or "unknown") for row in rows)
    rejection_reasons: Counter[str] = Counter()
    for row in rows:
        rejection_reasons.update({str(k): int(v) for k, v in (row.get("rejection_reason_counts") or {}).items()})
    return {
        "market_date": market_date, "trace_available": bool(rows), "cycles": len(rows),
        "actions": dict(sorted(actions.items())),
        "rejection_reason_counts": dict(sorted(rejection_reasons.items())),
        "premarket_plan_observed": any(row.get("action") == "premarket_plan_saved" for row in rows),
        "confirmation_cycle_observed": any(row.get("phase") == "entry_confirmation" for row in rows),
        "paper_submissions": sum(int(row.get("confirmed") or 0) for row in rows if row.get("action") == "paper_confirmations_submitted"),
        "recent": [{k: row.get(k) for k in ("cycle_id", "as_of", "phase", "action", "reason", "plan_id", "confirmed", "batch_id")} for row in rows[-10:]],
    }


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _executor(url: str) -> dict[str, Any]:
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=0.75) as response:
            payload = json.loads(response.read().decode("utf-8"))
        observed = payload.get("observability") or {}
        readiness = payload.get("market_data_readiness") or {}
        broker = payload.get("paper_broker") or {}
        execution = payload.get("execution") or {}
        counts = observed.get("counts") or {}
        blocked = observed.get("entry_blocked_reason")
        last_entry_block = observed.get("last_entry_block")
        block_today = False
        try:
            block_time = datetime.fromisoformat(str((last_entry_block or {}).get("event_time")).replace("Z", "+00:00"))
            block_today = block_time.astimezone(ZoneInfo("America/New_York")).date() == datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
        except (TypeError, ValueError):
            pass
        open_positions = int(observed.get("open_shadow_positions") or 0) + int(observed.get("open_paper_positions") or 0)
        if blocked:
            operating_state = "DATA_FAILURE"
        elif open_positions:
            operating_state = "ACTIVE_POSITION"
        elif block_today:
            operating_state = "SETUP_REJECTED"
        elif readiness.get("market_data_ready"):
            operating_state = "HEALTHY_NO_SETUP"
        else:
            operating_state = "AWAITING_DATA_CHECK"
        return {
            "reachable": True,
            "mode": payload.get("mode"),
            "operating_state": operating_state,
            "reconciliation_passed": bool(payload.get("reconciliation_passed")),
            "quote_feed_degraded": bool((payload.get("quote_manager") or {}).get("degraded")),
            "provider_session_ready": readiness.get("provider_session_ready"),
            "market_data_ready": bool(readiness.get("market_data_ready")),
            "last_chain_success_at": readiness.get("last_chain_success_at"),
            "entry_blocked_reason": blocked,
            "last_entry_block": last_entry_block,
            "counts": counts,
            "open_shadow_positions": int(observed.get("open_shadow_positions") or 0),
            "last_mark_at": observed.get("last_mark_at"),
            "last_worker_exception": observed.get("last_worker_exception"),
            "execution_backend": execution.get("backend") or broker.get("backend") or "simulated",
            "paper_broker": {
                "backend": broker.get("backend") or execution.get("backend") or "simulated",
                "ready": bool(broker.get("ready")),
                "paper_only": True,
                "last_error": broker.get("last_error"),
                "account": broker.get("account"),
                "unknown_positions": broker.get("unknown_positions") or [],
                "recent_orders": broker.get("recent_orders") or [],
            },
        }
    except Exception as exc:
        return {
            "reachable": False, "reason": type(exc).__name__, "mode": "offline",
            "operating_state": "DATA_FAILURE", "market_data_ready": False,
            "open_shadow_positions": 0, "counts": {},
            "execution_backend": "unavailable",
            "paper_broker": {"backend": "unavailable", "ready": False, "paper_only": True, "recent_orders": []},
        }


def snapshot(*, now: datetime | None = None, executor_url: str = "http://127.0.0.1:8787/api/paper/status") -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    plan = _read(PLAN)
    scheduler = _read(STATUS)
    training = _read(TRAINING)
    candidates = (plan or {}).get("candidates") or []
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "phase": phase_at(now).value,
        "scheduler": scheduler or {"action": "not_run", "as_of": None},
        "plan": {
            "available": bool(plan),
            "plan_id": (plan or {}).get("plan_id"),
            "market_date": (plan or {}).get("market_date"),
            "state": (plan or {}).get("state") or "MISSING",
            "created_at": (plan or {}).get("created_at"),
            "candidate_count": len(candidates),
            "candidates": [{
                "ticker": row.get("ticker"), "direction": row.get("direction"),
                "score": row.get("score"), "reward_risk": row.get("reward_risk"),
                "sentiment_status": (row.get("sentiment") or {}).get("status"),
                "ai_evaluation": row.get("ai_evaluation"),
            } for row in candidates],
        },
        "executor": _executor(executor_url),
        "learning": training or {
            "training_status": "NOT_BUILT", "samples": 0, "market_dates": 0,
            "blockers": ["prospective_shadow_outcomes_not_collected"],
        },
        "models": {
            "finbert": "advisory_only",
            "openrouter_model": "openai/gpt-4o-mini",
            "ai_multi_factor": "active_advisory",
            "fingpt": "not_enabled",
            "custom_model": "not_trained",
            "model_may_authorize_entry": False,
        },
        "daily_trace": _cycle_trace(now),
        "paper_only": True,
        "live_execution_capability": False,
    }
