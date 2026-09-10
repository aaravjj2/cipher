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
from core.exchange_calendar import is_session, previous_session


ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT_DIR = ROOT / "data" / "paper_runtime" / "autopilot"
PLAN = AUTOPILOT_DIR / "premarket_plan.json"
STATUS = AUTOPILOT_DIR / "status.json"
TRAINING = AUTOPILOT_DIR / "training" / "manifest.json"


def _cycle_trace(now: datetime) -> dict[str, Any]:
    local = now.astimezone(ZoneInfo("America/New_York"))
    day = local.date() if is_session(local.date()) else previous_session(local.date())
    market_date = day.isoformat()
    path = AUTOPILOT_DIR / "cycles" / f"{market_date}.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-250:]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        pass
    actions = Counter(str(row.get("action") or "unknown") for row in rows)
    rejection_reasons: Counter[str] = Counter()
    for row in rows:
        counts = row.get("rejection_reason_counts")
        if not isinstance(counts, dict):
            continue
        for key, value in counts.items():
            try:
                rejection_reasons[str(key)] += int(value)
            except (TypeError, ValueError):
                continue
    return {
        "market_date": market_date, "trace_available": bool(rows), "cycles": len(rows),
        "actions": dict(sorted(actions.items())),
        "rejection_reason_counts": dict(sorted(rejection_reasons.items())),
        "premarket_plan_observed": any(row.get("action") == "premarket_plan_saved" for row in rows),
        "confirmation_cycle_observed": any(row.get("phase") == "entry_confirmation" for row in rows),
        "cards_submitted": sum(int(row.get("cards_submitted", row.get("confirmed")) or 0) for row in rows if row.get("action") == "paper_confirmations_submitted"),
        "recent": [{k: row.get(k) for k in ("cycle_id", "as_of", "phase", "action", "reason", "plan_id", "cards_submitted", "batch_id")} for row in rows[-10:]],
    }


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _executor(url: str) -> dict[str, Any]:
    try:
        # A cold four-cohort evaluation can exceed 2.5 seconds. Subsequent reads
        # use the executor's cached report; allow the initial audit to complete.
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        observed = payload.get("observability") or {}
        readiness = payload.get("market_data_readiness") or {}
        broker = payload.get("paper_broker") or {}
        execution = payload.get("execution") or {}
        counts = observed.get("counts") or {}
        session = observed.get("session") or {}
        blocked = observed.get("entry_blocked_reason")
        last_entry_block = observed.get("last_entry_block")
        block_today = False
        try:
            block_time = datetime.fromisoformat(str((last_entry_block or {}).get("event_time")).replace("Z", "+00:00"))
            block_today = block_time.astimezone(ZoneInfo("America/New_York")).date() == datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
        except (TypeError, ValueError):
            pass
        open_positions = int(observed.get("open_shadow_positions") or 0) + int(observed.get("open_paper_positions") or 0)
        requires_broker = (execution.get("backend") or broker.get("backend")) == "alpaca_paper"
        if blocked or not payload.get("reconciliation_passed") or (requires_broker and not broker.get("ready")):
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
            "session": session,
            "cohorts": payload.get("cohorts") or [],
            "readiness": payload.get("readiness") or {},
            "open_shadow_positions": int(observed.get("open_shadow_positions") or 0),
            "last_mark_at": observed.get("last_mark_at"),
            "last_worker_exception": observed.get("last_worker_exception"),
            "execution_backend": execution.get("backend") or broker.get("backend") or "simulated",
            "portfolio_kind": execution.get("portfolio_kind") or ("cipher_local_paper" if execution.get("backend") == "simulated" else "external_paper_account"),
            "external_order_capability": bool(execution.get("external_order_capability", False)),
            "paper_broker": {
                "backend": broker.get("backend") or execution.get("backend") or "simulated",
                "ready": bool(broker.get("ready")),
                "paper_only": True,
                "last_error": broker.get("last_error"),
                "account": broker.get("account"),
                "unknown_positions": broker.get("unknown_positions") or [],
                "owned_orphan_positions": broker.get("owned_orphan_positions") or [],
                "recent_orders": broker.get("recent_orders") or [],
            },
        }
    except Exception as exc:
        # Mirror the success schema so consumers never have to handle two shapes.
        return {
            "reachable": False, "reason": type(exc).__name__, "mode": "offline",
            "operating_state": "DATA_FAILURE", "market_data_ready": False,
            "provider_session_ready": False, "last_chain_success_at": None,
            "reconciliation_passed": False, "quote_feed_degraded": None,
            "entry_blocked_reason": None, "last_entry_block": None,
            "open_shadow_positions": 0, "counts": {},
            "last_mark_at": None, "last_worker_exception": None,
            "execution_backend": "unavailable",
            "portfolio_kind": "unavailable", "external_order_capability": False,
            "paper_broker": {"backend": "unavailable", "ready": False, "paper_only": True,
                             "last_error": None, "account": None,
                             "unknown_positions": [], "recent_orders": []},
        }


def snapshot(*, now: datetime | None = None, executor_url: str = "http://127.0.0.1:8787/api/paper/status") -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    phase = phase_at(now)
    plan = _read(PLAN)
    scheduler = _read(STATUS)
    training = _read(TRAINING)
    candidates = [row for row in ((plan or {}).get("candidates") or []) if isinstance(row, dict)]
    local_day = now.astimezone(ZoneInfo("America/New_York")).date()
    expected_plan_day = local_day if is_session(local_day) else previous_session(local_day)
    plan_market_date = (plan or {}).get("market_date")
    executor = _executor(executor_url)
    if phase.value == "closed" and executor.get("operating_state") in {"AWAITING_DATA_CHECK", "HEALTHY_NO_SETUP", "SETUP_REJECTED"}:
        executor["operating_state"] = "MARKET_CLOSED"
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "phase": phase.value,
        "scheduler": scheduler or {"action": "not_run", "as_of": None},
        "plan": {
            "available": bool(plan),
            "plan_id": (plan or {}).get("plan_id"),
            "market_date": (plan or {}).get("market_date"),
            "freshness": "current" if plan_market_date == local_day.isoformat() else "last_session" if plan_market_date == expected_plan_day.isoformat() else "stale",
            "state": (plan or {}).get("state") or "MISSING",
            "created_at": (plan or {}).get("created_at"),
            "candidate_count": len(candidates),
            "candidates": [{
                "ticker": str(row.get("ticker") or ""),
                "direction": str(row.get("direction") or ""),
                "score": _number(row.get("score")),
                "reward_risk": _number(row.get("reward_risk")),
                "sentiment_status": _mapping(row.get("sentiment")).get("status"),
                "ai_evaluation": _mapping(row.get("ai_evaluation")),
            } for row in candidates],
        },
        "executor": executor,
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
