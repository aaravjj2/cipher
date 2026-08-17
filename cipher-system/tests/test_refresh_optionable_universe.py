from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refresh_optionable_universe.py"
spec = importlib.util.spec_from_file_location("refresh_optionable_universe", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def test_refresh_preserves_tiers_and_records_removed_symbols() -> None:
    payload = {"as_of": "old", "count": 3, "sorted_tickers": {"mega": ["AAA", "BAD"], "small": ["BBB"]}}
    assets = [
        {"symbol": "AAA", "status": "active", "attributes": ["has_options"]},
        {"symbol": "BAD", "status": "inactive", "attributes": ["has_options"]},
        {"symbol": "BBB", "status": "active", "attributes": []},
        {"symbol": "NEW", "status": "active", "attributes": ["has_options"]},
    ]
    result = module.refresh_payload(payload, assets, as_of="2026-08-14")
    assert result["sorted_tickers"] == {"mega": ["AAA"], "small": []}
    assert result["count"] == 1
    assert result["as_of"] == "2026-08-14"
    assert {row["ticker"] for row in result["validation"]["removed"]} == {"BAD", "BBB"}
    assert "NEW" not in result["sorted_tickers"]["mega"]
