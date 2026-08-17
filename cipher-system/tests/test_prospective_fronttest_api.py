from __future__ import annotations

from pathlib import Path

from core import prospective_fronttest_api


def test_snapshot_registers_and_exposes_read_only_programs(tmp_path: Path) -> None:
    payload = prospective_fronttest_api.snapshot(tmp_path / "fronttests.sqlite")
    assert payload["paper_only"] is True
    assert payload["read_only"] is True
    assert payload["execution_capability"] is False
    assert {row["program_id"] for row in payload["programs"]} == {
        "tsla_stable_wall_rejection_v1",
        "spartan_weekly_radar_2026_08_17",
    }
    assert all(row["effective_status"] in {"REGISTERED", "MONITORING", "COMPLETED"} for row in payload["programs"])
    assert payload["signals"] == []
    assert payload["observations"] == []
    assert payload["latest_coverage"]["observed"] == 0


def test_snapshot_preserves_gex_and_missing_data_caveat(tmp_path: Path) -> None:
    payload = prospective_fronttest_api.snapshot(tmp_path / "fronttests.sqlite")
    assert "no signal backfill" in payload["caveat"].lower()
    assert "public-OI heuristic" in payload["caveat"]
    assert "Missing quotes" in payload["caveat"]
