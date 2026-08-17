from core import finviz_discovery


def test_discovery_is_cached_deduplicated_and_never_grants_execution(tmp_path):
    calls = []
    def fetch(_preset):
        calls.append(1)
        return [{"Ticker": "NVDA", "Price": 100}, {"Ticker": "NVDA"}, {"Ticker": "MU"}]
    first = finviz_discovery.run_preset("volatile_liquid", cache_dir=tmp_path, fetch_fn=fetch)
    second = finviz_discovery.run_preset("volatile_liquid", cache_dir=tmp_path, fetch_fn=fetch)
    assert [row["ticker"] for row in first["rows"]] == ["NVDA", "MU"]
    assert second["cache_hit"] is True and len(calls) == 1
    assert first["requires_alpaca_validation"] is True
    assert first["live_order_authority"] is False


def test_provider_failure_is_explicit(tmp_path):
    result = finviz_discovery.run_preset(
        "earnings_week", cache_dir=tmp_path,
        fetch_fn=lambda _preset: (_ for _ in ()).throw(RuntimeError("blocked")),
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["rows"] == []


def test_current_finviz_duplicate_first_character_bug_is_repaired_as_batch():
    rows = finviz_discovery.normalize_ticker_rows([
        {"Ticker": "MMU"}, {"Ticker": "SSNDK"}, {"Ticker": "AASML"},
    ])
    assert [row["Ticker"] for row in rows] == ["MU", "SNDK", "ASML"]
