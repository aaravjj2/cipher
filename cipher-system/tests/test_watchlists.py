from core import watchlists


def test_named_watchlist_and_reproducible_screen(tmp_path):
    path = tmp_path / "watch.sqlite"
    listing = watchlists.create_watchlist("Momentum", path)
    watchlists.add_member(listing["id"], "AAPL", path)
    watchlists.add_member(listing["id"], "MSFT", path)
    screen = watchlists.save_screen("Positive", {"day_change_min": 1, "optionable": True}, listing["id"], path)
    quotes = {"AAPL": {"price_context": 200, "day_change_pct": 2, "as_of": "now"}, "MSFT": {"price_context": 300, "day_change_pct": -1, "as_of": "now"}}
    result = watchlists.run_screen(screen["id"], quote_fn=lambda ticker: quotes[ticker], universe={"AAPL", "MSFT"}, scanner_scores={}, path=path)
    assert [row["ticker"] for row in result["matches"]] == ["AAPL"]
    assert result["reproducible_inputs"]["tickers"] == ["AAPL", "MSFT"]
    assert watchlists.list_all(path)["execution_capability"] is False
