from core import live_option_chain_capture as capture


def test_open_interest_request_has_explicit_future_expiration_window(monkeypatch):
    queries = []

    def request(_path, query, _key, _secret, **_kwargs):
        queries.append(dict(query))
        if len(queries) == 1:
            return {
                "option_contracts": [{"symbol": "SPY261218C00700000", "open_interest": "123"}],
                "next_page_token": "next",
            }
        return {"option_contracts": []}

    monkeypatch.setattr(capture, "alpaca_request", request)
    result = capture.fetch_open_interest("SPY", "key", "secret")
    assert result["SPY261218C00700000"]["open_interest"] == 123.0
    assert all(q.get("expiration_date_gte") and q.get("expiration_date_lte") for q in queries)
    assert queries[1]["page_token"] == "next"
