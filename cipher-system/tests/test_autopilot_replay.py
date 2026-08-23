from __future__ import annotations

import json
from datetime import datetime, timezone

from core.paper_executor.autopilot_replay import replay_history


def stamp(hour: int, minute: int) -> str:
    return datetime(2026, 8, 18, hour + 4, minute, tzinfo=timezone.utc).isoformat()


def evidence(phase: str, snapshot: str) -> dict:
    return {
        "snapshot_id": snapshot * 64,
        "feed": "opra",
        "freshness": {"status": "current", "age_seconds": 2},
        "coverage": {"status": "sufficient"},
        "session": {"phase": phase, "market_date": "2026-08-18", "timezone": "America/New_York"},
        "event_at": stamp(8 if phase == "premarket" else 9, 0),
    }


def card(*, phase: str, state: str = "", spot: float = 100.0) -> dict:
    return {
        "ticker": "MU",
        "direction": "BULLISH",
        "score": 82.0 if phase == "premarket" else 60.0,
        "spot": spot,
        "target": 104.0 if phase == "premarket" else 104.0,
        "invalidation": 98.0,
        "reward_risk": 2.0,
        "setup_type": "TRIPLE CLUSTER (3 PEAKS, ABOVE)",
        "rank_eligible": True,
        "geometry_valid": True,
        "actionable": True,
        "agent_state": state,
        "state": state.upper(),
        "evidence_snapshot": evidence(phase, "a" if phase == "premarket" else "b"),
    }


def write_scan(root, name: str, payload: dict) -> None:
    (root / name).write_text(json.dumps(payload), encoding="utf-8")


def test_replay_reconstructs_confirmation_and_snapshot_target(tmp_path) -> None:
    write_scan(tmp_path, "premarket.json", {
        "as_of": stamp(8, 0), "strategy": "cipher", "top": [card(phase="premarket")],
    })
    triggered = card(phase="regular", state="triggered", spot=100.5)
    write_scan(tmp_path, "regular_entry.json", {
        "as_of": stamp(9, 35), "strategy": "flash_agentic", "top": [triggered],
    })
    target = card(phase="regular", state="triggered", spot=104.0)
    write_scan(tmp_path, "regular_target.json", {
        "as_of": stamp(9, 45), "strategy": "flash_agentic", "top": [target],
    })

    report = replay_history(tmp_path)

    assert report["coverage"]["exact_days"] == ["2026-08-18"]
    assert report["summary"]["unique_confirmations"] == 1
    assert report["summary"]["target_hits"] == 1
    assert report["summary"]["invalidation_hits"] == 0
    assert report["trades"][0]["outcome"] == "target_hit"
    assert report["trades"][0]["option_pnl"] is None


def test_replay_keeps_missing_future_path_unknown(tmp_path) -> None:
    write_scan(tmp_path, "premarket.json", {
        "as_of": stamp(8, 0), "strategy": "cipher", "top": [card(phase="premarket")],
    })
    write_scan(tmp_path, "regular_entry.json", {
        "as_of": stamp(9, 35), "strategy": "flash_agentic",
        "top": [card(phase="regular", state="triggered", spot=100.5)],
    })

    report = replay_history(tmp_path)

    assert report["summary"]["unresolved"] == 1
    assert report["summary"]["wins"] == 0
    assert report["summary"]["losses"] == 0
    assert report["trades"][0]["outcome"] == "unresolved_no_future_snapshot"
