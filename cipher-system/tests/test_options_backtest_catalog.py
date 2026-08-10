from __future__ import annotations

import json
from pathlib import Path

from core import options_backtest_catalog as catalog


CAPABILITIES = {
    "historical_option_bars": True,
    "historical_option_bid_ask": False,
    "historical_iv_greeks": False,
    "historical_open_interest": False,
}
CAVEATS = [
    "Only rows marked observed_on_decision may be considered available.",
    "Execution tests must be labeled as bar/trade approximations.",
]


def write_standard(root: Path, name: str):
    directory = root / "historical_options" / name
    directory.mkdir(parents=True)
    (directory / "historical_options.sqlite").write_bytes(b"sqlite-placeholder")
    (directory / "download_manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "provider_id_reused",
                "status": "HISTORICAL_BARS_WITHOUT_NBBO",
                "cumulative_coverage": {"option_bar_rows": 123, "decision_date_count": 4},
                "capabilities": CAPABILITIES,
                "caveats": CAVEATS,
                "provenance": {"database": {"sha256": "a" * 64}, "raw_pages": ["large"]},
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_catalog_reconciles_manifest_generations_and_reused_provider_ids(tmp_path: Path):
    first = write_standard(tmp_path, "first")
    write_standard(tmp_path, "second")
    eod = tmp_path / "historical_options" / "indices" / "spy"
    eod.mkdir(parents=True)
    (eod / "historical_options.sqlite").write_bytes(b"sqlite-placeholder")
    (eod / "eod_archive_manifest.json").write_text(
        json.dumps(
            {
                "option_bar_rows": 456,
                "selected_unique_symbols": 20,
                "observed_unique_symbols": 30,
                "research_grade": False,
                "research_grade_reason": "Historical NBBO is absent.",
            }
        ),
        encoding="utf-8",
    )
    report = first / "strategy_lab" / "historical_option_strategy_report.json"
    report.parent.mkdir()
    report.write_text(json.dumps({"summary": {"trades": 10}}), encoding="utf-8")

    payload = catalog.build_catalog(tmp_path)

    assert payload["counts"] == {"datasets": 3, "reports": 1, "manifest_errors": 0}
    assert len({row["id"] for row in payload["datasets"]}) == 3
    standard = [row for row in payload["datasets"] if row["manifest_type"] == "download_manifest"]
    assert {row["provider_dataset_id"] for row in standard} == {"provider_id_reused"}
    assert standard[0]["capabilities"] == CAPABILITIES
    assert standard[0]["caveats"] == CAVEATS
    assert "raw_pages" not in standard[0]
    assert payload["reports"][0]["dataset_id"] == next(
        row["id"] for row in payload["datasets"] if row["relative_path"] == "first"
    )


def test_malformed_manifest_is_reported_without_breaking_catalog(tmp_path: Path):
    directory = tmp_path / "historical_options" / "broken"
    directory.mkdir(parents=True)
    (directory / "download_manifest.json").write_text("not json", encoding="utf-8")

    payload = catalog.build_catalog(tmp_path)

    assert payload["datasets"] == []
    assert payload["counts"]["manifest_errors"] == 1
    assert payload["errors"][0]["relative_path"].endswith("download_manifest.json")
