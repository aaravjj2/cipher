from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from core.paper_executor import local_scan_scheduler
from core.paper_executor.local_scan_scheduler import executor_payload, in_entry_window, scanner_url


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"ok": true}'



def test_request_json_adds_internal_guest_context_from_environment(monkeypatch):
    captured = {}
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "unit-test-token")
    monkeypatch.setenv("CIPHER_PROVIDER_SESSION", "opaque-session")

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(local_scan_scheduler.urllib.request, "urlopen", fake_urlopen)
    assert local_scan_scheduler.request_json("http://127.0.0.1:8282/api/health", timeout=7) == {"ok": True}
    request = captured["request"]
    assert request.get_header("X-cipher-internal-token") == "unit-test-token"
    assert request.get_header("X-cipher-guest") == "1"
    assert request.get_header("X-cipher-user-id") == "guest"
    assert request.get_header("X-cipher-provider-session") == "opaque-session"
    assert captured["timeout"] == 7



def test_request_json_does_not_create_auth_headers_without_token(monkeypatch):
    captured = {}
    monkeypatch.delenv("CIPHER_INTERNAL_PROXY_TOKEN", raising=False)
    monkeypatch.delenv("CIPHER_PROVIDER_SESSION", raising=False)

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return _Response()

    monkeypatch.setattr(local_scan_scheduler.urllib.request, "urlopen", fake_urlopen)
    local_scan_scheduler.request_json("http://127.0.0.1:8282/api/health")
    assert captured["request"].get_header("X-cipher-internal-token") is None
    assert captured["request"].get_header("X-cipher-guest") is None




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
