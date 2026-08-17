from datetime import date

from core import terminal_service


def test_options_use_case_bounds_horizon_and_preserves_unknowns(monkeypatch):
    seen = {}
    def chain(ticker, feed, **kwargs):
        seen.update({"ticker": ticker, "feed": feed, **kwargs})
        return {"contracts": []}
    monkeypatch.setattr(terminal_service.options_terminal, "chain_view", lambda ticker, quote, contracts, expiration_limit: {
        "ticker": ticker, "quote": quote, "contracts": contracts, "expiration_limit": expiration_limit,
    })
    result = terminal_service.options_chain_view(
        "NVDA", "opra", 99, quote_fn=lambda ticker: {"ticker": ticker, "price_context": 100},
        chain_fn=chain, force=True, today=date(2026, 8, 14),
    )
    assert result["expiration_limit"] == 12
    assert seen["expiration_gte"] == "2026-08-14"
    assert seen["expiration_lte"] == "2027-03-18"
    assert seen["force"] is True


def test_portfolio_use_case_injects_provider_without_order_authority(monkeypatch):
    seen = {}
    def fake_status(**kwargs):
        seen.update(kwargs)
        return {"execution_capability": False}
    monkeypatch.setattr(terminal_service.portfolio_risk, "status", fake_status)
    result = terminal_service.portfolio_snapshot("opra", quote_fn=lambda ticker: {}, chain_fn=lambda *args, **kwargs: {})
    assert result["execution_capability"] is False
    seen["chain_fn"]("AAPL", "2026-08-14", "2026-09-14")
