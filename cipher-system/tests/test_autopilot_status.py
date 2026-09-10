from __future__ import annotations

from datetime import datetime, timezone
import json

from core import autopilot_status


def test_status_preserves_paper_boundary_when_executor_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(autopilot_status, "PLAN", tmp_path / "missing-plan.json")
    monkeypatch.setattr(autopilot_status, "STATUS", tmp_path / "missing-status.json")
    monkeypatch.setattr(autopilot_status, "TRAINING", tmp_path / "missing-training.json")
    monkeypatch.setattr(autopilot_status, "AUTOPILOT_DIR", tmp_path)
    data = autopilot_status.snapshot(
        now=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
        executor_url="http://127.0.0.1:1/api/paper/status",
    )
    assert data["phase"] == "premarket_discovery"
    assert data["executor"]["reachable"] is False
    assert data["paper_only"] is True
    assert data["live_execution_capability"] is False
    assert data["models"]["model_may_authorize_entry"] is False
    assert data["daily_trace"]["trace_available"] is False


def test_executor_status_preserves_readiness_and_ledger_counts(monkeypatch):
    payload = {
        "mode": "shadow", "reconciliation_passed": True,
        "quote_manager": {"degraded": False},
        "market_data_readiness": {
            "provider_session_ready": True, "market_data_ready": True,
            "last_chain_success_at": "2026-08-23T12:00:00+00:00", "last_error": None,
        },
        "observability": {
            "open_shadow_positions": 0, "open_paper_positions": 0,
            "last_mark_at": None, "last_worker_exception": None,
            "last_entry_block": None, "entry_blocked_reason": None,
            "counts": {"signal_cards": 5, "contract_candidates": 2, "paper_orders": 1},
        },
    }

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(autopilot_status, "urlopen", lambda *_args, **_kwargs: Response())
    result = autopilot_status._executor("http://executor/status")
    assert result["operating_state"] == "HEALTHY_NO_SETUP"
    assert result["provider_session_ready"] is True
    assert result["market_data_ready"] is True
    assert result["counts"]["paper_orders"] == 1
    assert result["external_order_capability"] is False


def test_status_tolerates_partial_plan_and_corrupt_cycle_lines(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"
    status = tmp_path / "status.json"
    training = tmp_path / "training.json"
    plan.write_text(json.dumps({
        "state": "WATCHLIST_ONLY",
        "candidates": [
            {"ticker": "SPY", "direction": "BULLISH", "score": None, "sentiment": "missing"},
            "corrupt-row",
        ],
    }))
    cycles = tmp_path / "cycles"
    cycles.mkdir()
    (cycles / "2026-08-17.jsonl").write_text(
        '{"action":"premarket_plan_saved","rejection_reason_counts":{"missing":2}}\n'
        '{broken\n'
        '{"action":"executor_monitoring","rejection_reason_counts":"missing"}\n'
    )
    monkeypatch.setattr(autopilot_status, "PLAN", plan)
    monkeypatch.setattr(autopilot_status, "STATUS", status)
    monkeypatch.setattr(autopilot_status, "TRAINING", training)
    monkeypatch.setattr(autopilot_status, "AUTOPILOT_DIR", tmp_path)

    data = autopilot_status.snapshot(
        now=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
        executor_url="http://127.0.0.1:1/api/paper/status",
    )

    assert data["plan"]["candidate_count"] == 1
    assert data["plan"]["candidates"][0]["score"] is None
    assert data["plan"]["candidates"][0]["sentiment_status"] is None
    assert data["daily_trace"]["cycles"] == 2
    assert data["daily_trace"]["rejection_reason_counts"] == {"missing": 2}


def test_closed_market_status_uses_latest_exchange_session(monkeypatch, tmp_path):
    (tmp_path / "cycles").mkdir()
    (tmp_path / "cycles" / "2026-09-04.jsonl").write_text(
        json.dumps({"action": "paper_confirmations_submitted", "cards_submitted": 4}) + "\n"
    )
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"market_date": "2026-09-04", "candidates": []}))
    monkeypatch.setattr(autopilot_status, "PLAN", plan)
    monkeypatch.setattr(autopilot_status, "STATUS", tmp_path / "missing-status.json")
    monkeypatch.setattr(autopilot_status, "TRAINING", tmp_path / "missing-training.json")
    monkeypatch.setattr(autopilot_status, "AUTOPILOT_DIR", tmp_path)
    result = autopilot_status.snapshot(
        now=datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc),
        executor_url="http://127.0.0.1:1/api/paper/status",
    )
    assert result["phase"] == "closed"
    assert result["plan"]["freshness"] == "last_session"
    assert result["daily_trace"]["market_date"] == "2026-09-04"
    assert result["daily_trace"]["cards_submitted"] == 4
    assert result["executor"]["operating_state"] == "DATA_FAILURE"


def test_closed_market_without_live_data_is_healthy_idle(monkeypatch, tmp_path):
    monkeypatch.setattr(autopilot_status, "PLAN", tmp_path / "missing-plan.json")
    monkeypatch.setattr(autopilot_status, "STATUS", tmp_path / "missing-status.json")
    monkeypatch.setattr(autopilot_status, "TRAINING", tmp_path / "missing-training.json")
    monkeypatch.setattr(autopilot_status, "AUTOPILOT_DIR", tmp_path)
    monkeypatch.setattr(autopilot_status, "_executor", lambda _url: {
        "reachable": True,
        "operating_state": "AWAITING_DATA_CHECK",
    })

    result = autopilot_status.snapshot(now=datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc))

    assert result["phase"] == "closed"
    assert result["executor"]["operating_state"] == "MARKET_CLOSED"
