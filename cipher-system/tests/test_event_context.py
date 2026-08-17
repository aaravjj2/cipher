from core import event_context


def test_missing_event_provider_is_explicit(tmp_path):
    result = event_context.for_ticker("NVDA", latest=tmp_path / "missing.json")
    assert result["earnings"]["status"] == "UNAVAILABLE"
    assert result["corporate_actions"]["status"] == "UNAVAILABLE"


def test_revision_and_symbol_filter_are_preserved(tmp_path):
    payload = event_context.snapshot(["NVDA", "AAPL"], [
        {"provider": "alpaca_market_data", "symbol": "NVDA", "action_type": "split", "event": {"symbol": "NVDA"}},
        {"provider": "alpaca_market_data", "symbol": "AAPL", "action_type": "dividend", "event": {"symbol": "AAPL"}},
    ], observed_at="2026-08-14T00:00:00+00:00")
    path = event_context.save(payload, directory=tmp_path)
    result = event_context.for_ticker("NVDA", latest=path)
    assert result["corporate_actions"]["status"] == "AVAILABLE"
    assert len(result["corporate_actions"]["events"]) == 1
    assert result["corporate_actions"]["source"]["point_in_time_ready"] is False
    assert len((tmp_path / "revisions.jsonl").read_text().splitlines()) == 1
