from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from core.paper_executor.local_scan_scheduler import executor_payload, in_entry_window, scanner_url


def test_entry_window_is_new_york_weekday_and_dst_aware():
    assert in_entry_window(datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc))
    assert not in_entry_window(datetime(2026, 8, 13, 19, 1, tzinfo=timezone.utc))
    assert not in_entry_window(datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc))


def test_scanner_url_forces_single_worker():
    parsed = urlparse(scanner_url("http://127.0.0.1:8282/", "flash", ["NVDA", "AAPL"], workers=9))
    params = parse_qs(parsed.query)
    assert parsed.path == "/api/scan"
    assert params["tickers"] == ["NVDA,AAPL"]
    assert params["strategy"] == ["flash"]
    assert params["workers"] == ["1"]


def test_scanner_url_rejects_unapproved_strategy():
    with pytest.raises(ValueError, match="unsupported scheduled strategy"):
        scanner_url("http://127.0.0.1:8282", "cluster", ["NVDA"])


def test_executor_payload_labels_source_and_fresh_timestamp():
    timestamp = "2026-08-13T14:00:00+00:00"
    payload = executor_payload(
        "flash_agentic",
        {"top": [{"ticker": "NVDA", "direction": "BULLISH", "setup_type": "FLOOR BOUNCE"}]},
        timestamp,
    )
    assert payload["source"] == "cipher_local_scanner"
    assert payload["scan_type"] == "flash_agentic"
    assert payload["cards"][0]["scanner_type"] == "flash_agentic"
    assert payload["cards"][0]["captured_at"] == timestamp


def test_executor_payload_rejects_malformed_scanner_response():
    with pytest.raises(ValueError, match="top list"):
        executor_payload("flash", {"top": None}, "2026-08-13T14:00:00+00:00")
