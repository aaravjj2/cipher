from core import app


def test_workspace_context_preserves_partial_errors_and_caveats(monkeypatch):
    monkeypatch.setattr(app, "quote", lambda ticker: {"ticker": ticker, "as_of": "q", "price_context": 100})
    monkeypatch.setattr(app, "matrix", lambda *args: {
        "as_of": "m", "feed": "opra", "summary": {"gamma_flip_level": 99},
        "coverage": {"calculated_cells": 1}, "formula": "public OI heuristic", "caveat": "not dealer positioning",
    })
    monkeypatch.setattr(app, "flow", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("flow unavailable")))
    monkeypatch.setattr(app, "option_chain", lambda *args, **kwargs: {"contracts": []})
    monkeypatch.setattr(app.options_terminal, "chain_view", lambda *args, **kwargs: {
        "as_of": "o", "feed": "opra", "spot": 100, "term_structure": [], "iv_rank": None,
        "iv_history_status": "UNAVAILABLE", "open_interest_caveat": "unknown stays unknown",
    })
    monkeypatch.setattr(app.portfolio_risk, "status", lambda **kwargs: {
        "as_of": "p", "summary": {}, "exceptions": [], "positions": [], "caveat": "manual",
    })
    monkeypatch.setattr(app.trader_journal, "list_entries", lambda **kwargs: {
        "entries": [], "as_of": "j", "execution_capability": False,
    })
    monkeypatch.setattr(app.company_context, "context", lambda ticker: {
        "generated_at": "c", "profile": {"ticker": ticker}, "fundamentals": {}, "filings": [],
        "earnings": {"status": "UNAVAILABLE"}, "corporate_actions": {}, "macro": [], "sources": [], "errors": [],
    })

    result = app.workspace_context("NVDA", "opra")

    assert result["read_only"] is True
    assert result["execution_capability"] is False
    assert result["sections"]["matrix"]["caveat"] == "not dealer positioning"
    assert result["sections"]["options"]["iv_rank"] is None
    assert result["sections"]["company_events"]["earnings"]["status"] == "UNAVAILABLE"
    assert result["errors"] == [{"section": "flow", "error": "flow unavailable"}]
