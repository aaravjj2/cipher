from __future__ import annotations

from datetime import datetime, timezone

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
