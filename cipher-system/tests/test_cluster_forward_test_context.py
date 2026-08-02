from __future__ import annotations

import json
from datetime import datetime, timezone

from core.paper_executor import cluster_forward_test as cft


def test_build_research_context_uses_latest_ticker_reports(tmp_path, monkeypatch):
    data = tmp_path / "data"
    setup_dir = data / "setup_research"
    company_dir = data / "company_research"
    setup_dir.mkdir(parents=True)
    company_dir.mkdir(parents=True)
    (setup_dir / "setup_research_20260730.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-30T12:00:00+00:00",
                "ranked": [
                    {
                        "ticker": "MSFT",
                        "grade": "A",
                        "score": 91,
                        "direction": "up",
                        "setup": "QUAD UPSIDE",
                        "reasons": ["top5_cluster"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (company_dir / "company_research_20260730.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-30T12:05:00+00:00",
                "rows": [
                    {
                        "ticker": "MSFT",
                        "grade": "A",
                        "alignment": "with",
                        "today_change_pct": 1.2,
                        "week": {"week_return_pct": 2.4},
                        "headlines": [
                            {
                                "title": "Microsoft headline",
                                "published": "Thu, 30 Jul 2026 12:00:00 +0000",
                                "link": "https://example.test/msft",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cft, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cft, "kronos_status_snapshot", lambda: {"available": False, "missing_dependencies": ["torch"]})
    monkeypatch.setattr(cft, "timesfm_status_snapshot", lambda: {"available": False, "blockers": ["timesfm runtime package is not installed"]})

    context = cft.build_research_context("MSFT", datetime(2026, 7, 30, 13, tzinfo=timezone.utc))

    assert context["setup_research"]["available"] is True
    assert context["setup_research"]["grade"] == "A"
    assert context["company_news"]["available"] is True
    assert context["company_news"]["headline_count"] == 1
    assert context["kronos"]["missing_dependencies"] == ["torch"]
    assert context["timesfm"]["blockers"] == ["timesfm runtime package is not installed"]


def test_separate_capture_root_seeds_existing_and_processes_new_files(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    capture_root = tmp_path / "capture_mirror"
    uploaded = capture_root / "uploaded"
    uploaded.mkdir(parents=True)
    state_path = runtime_root / "state" / "cluster_forward_test_state.json"

    existing = uploaded / "cluster_20260730T130000Z_existing.json"
    existing.write_text(
        json.dumps(
            {
                "captured_at": "2026-07-30T13:00:00Z",
                "scan_type": "cluster",
                "cards": [
                    {
                        "ticker": "MSFT",
                        "spot": 400,
                        "direction": "bullish",
                        "target": 405,
                        "invalidation": 397,
                        "rank": 1,
                        "setup_type": "quad upside",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    seeded = cft.seed_existing_state(
        runtime_root,
        state_path,
        reset=True,
        capture_root=capture_root,
    )

    assert seeded["seeded_existing_cluster_files"] == 1
    seeded_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert seeded_state["capture_root"] == str(capture_root.resolve())
    assert seeded_state["open_positions"] == []

    new_capture = uploaded / "cluster_20260730T133000Z_new.json"
    new_capture.write_text(
        json.dumps(
            {
                "captured_at": "2026-07-30T13:30:00Z",
                "scan_type": "cluster",
                "cards": [
                    {
                        "ticker": "NVDA",
                        "spot": 180,
                        "direction": "bullish",
                        "target": 185,
                        "invalidation": 177,
                        "rank": 2,
                        "setup_type": "cluster upside",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cft, "build_research_context", lambda ticker, decision_time: {"ticker": ticker})

    result = cft.process_once(
        runtime_root,
        state_path,
        cooldown_minutes=10,
        capture_root=capture_root,
    )

    assert result["opened"] == len(cft.PROFILES)
    assert result["capture_root"] == str(capture_root.resolve())
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["open_positions"]) == len(cft.PROFILES)
    assert {row["ticker"] for row in state["open_positions"]} == {"NVDA"}
    report = (runtime_root / "data" / "cluster_forward_tests" / "latest_cluster_forward_test.md").read_text(
        encoding="utf-8"
    )
    assert f"- Capture root: {capture_root.resolve()}" in report
