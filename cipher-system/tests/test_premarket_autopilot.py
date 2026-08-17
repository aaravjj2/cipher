from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone

from core.paper_executor import autopilot_planner as planner
from core.paper_executor import autopilot_scheduler as scheduler


def stamp(hour: int, minute: int) -> datetime:
    # August is EDT: 08:00 ET is 12:00 UTC.
    return datetime(2026, 8, 17, hour + 4, minute, tzinfo=timezone.utc)


def evidence(phase: str) -> dict:
    return {
        "snapshot_id": "a" * 64,
        "feed": "opra",
        "freshness": {"status": "current", "age_seconds": 2.0},
        "coverage": {"status": "sufficient", "contracts": 500, "calculated_cells": 20},
        "session": {"phase": phase, "timezone": "America/New_York", "market_date": "2026-08-17"},
        "event_at": stamp(8, 0).isoformat(),
        "replay_available": True,
    }


def card(*, phase: str = "premarket", state: str = "") -> dict:
    return {
        "ticker": "MU", "direction": "BULLISH", "score": 82.0,
        "spot": 100.0, "target": 104.0, "invalidation": 98.0,
        "reward_risk": 2.0, "setup_type": "CIPHER MODEL",
        "rank_eligible": True, "geometry_valid": True, "actionable": True,
        "agent_state": state, "evidence_snapshot": evidence(phase),
    }


def test_phase_machine_never_enters_during_premarket_or_opening_candle() -> None:
    assert planner.phase_at(stamp(8, 0)) == planner.AutopilotPhase.PREMARKET_DISCOVERY
    assert planner.phase_at(stamp(9, 32)) == planner.AutopilotPhase.OPENING_WAIT
    assert planner.phase_at(stamp(9, 35)) == planner.AutopilotPhase.ENTRY_CONFIRMATION
    assert planner.phase_at(stamp(12, 0)) == planner.AutopilotPhase.MONITOR_ONLY
    assert planner.phase_at(stamp(15, 45)) == planner.AutopilotPhase.FORCE_CLOSE


def test_finbert_context_is_point_in_time_stale_and_never_authoritative(tmp_path) -> None:
    registry = tmp_path / "registry.sqlite"
    with sqlite3.connect(registry) as db:
        db.execute("create table news_events (available_at text, payload_json text)")
        past = {
            "symbols": ["MU"], "positive_probability": 0.9,
            "negative_probability": 0.05, "sentiment_model_id": "finbert@pinned",
        }
        future = {
            "symbols": ["MU"], "positive_probability": 0.01,
            "negative_probability": 0.98, "sentiment_model_id": "finbert@future",
        }
        db.execute("insert into news_events values (?,?)", ("2026-08-15T12:00:00+00:00", json.dumps(past)))
        db.execute("insert into news_events values (?,?)", ("2026-08-18T12:00:00+00:00", json.dumps(future)))
    context = planner.sentiment_context(["MU"], as_of=stamp(8, 0), registry=registry)
    assert context["MU"]["status"] == "stale"
    assert context["MU"]["score"] == 0.85
    assert context["MU"]["model_ids"] == ["finbert@pinned"]
    assert context["MU"]["directional_authority"] is False


def test_plan_is_watch_only_and_rejects_non_premarket_evidence() -> None:
    regular = card(phase="regular")
    scan = {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card(), regular]}
    plan = planner.build_premarket_plan(scan, now=stamp(8, 0))
    assert plan["state"] == "WATCHLIST_ONLY"
    assert plan["entry_policy"]["premarket_entry_allowed"] is False
    assert plan["live_execution_capability"] is False
    assert len(plan["candidates"]) == 1
    assert plan["rejected"][0]["reasons"] == ["not_premarket_evidence"]


def test_confirmation_requires_trigger_same_direction_and_regular_fresh_evidence() -> None:
    plan = planner.build_premarket_plan(
        {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]},
        now=stamp(8, 0),
    )
    waiting = card(phase="regular", state="arming")
    waiting["evidence_snapshot"]["event_at"] = stamp(9, 35).isoformat()
    result = planner.confirmation_payload(plan, {"as_of": stamp(9, 35).isoformat(), "top": [waiting]}, now=stamp(9, 35))
    assert result["cards"] == []
    assert result["rejected"][0]["reasons"] == ["setup_not_triggered"]

    triggered = deepcopy(waiting)
    triggered["agent_state"] = "triggered"
    triggered["evidence_snapshot"]["snapshot_id"] = "b" * 64
    result = planner.confirmation_payload(plan, {"as_of": stamp(9, 35).isoformat(), "top": [triggered]}, now=stamp(9, 35))
    assert len(result["cards"]) == 1
    assert result["cards"][0]["autopilot"]["sentiment_directional_authority"] is False
    assert result["live_execution_capability"] is False


def test_scheduler_premarket_writes_plan_but_never_calls_executor(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def request(url: str, *, payload=None, timeout=600):
        calls.append((url, payload is not None))
        if "finviz-discovery" in url:
            return {"symbols": ["MU"]}
        return {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]}

    monkeypatch.setattr(scheduler, "request_json", request)
    monkeypatch.setattr(scheduler, "sentiment_context", lambda *_args, **_kwargs: {})
    result = scheduler.run_cycle(
        now=stamp(8, 0), plan_path=tmp_path / "plan.json", status_path=tmp_path / "status.json",
    )
    assert result["action"] == "premarket_plan_saved"
    assert result["premarket_entries"] == 0
    assert all(not posted for _, posted in calls)
    assert json.loads((tmp_path / "plan.json").read_text())["live_execution_capability"] is False
    trace = tmp_path / "cycles" / "2026-08-17.jsonl"
    assert trace.is_file()
    event = json.loads(trace.read_text().splitlines()[-1])
    assert event["action"] == "premarket_plan_saved"
    assert event["candidate_tickers"] == ["MU"]
    assert len(event["cycle_id"]) == 24
