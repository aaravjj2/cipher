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
