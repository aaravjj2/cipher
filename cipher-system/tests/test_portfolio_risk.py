from pathlib import Path

from core import portfolio_risk


def _quote(ticker):
    return {"price_context": 105, "as_of": "2026-08-14T14:00:00Z"}


def _chain(ticker, start, end):
    return [{"symbol": "X260821C00110000", "expiry": "2026-08-21", "type": "call", "strike": 110,
             "bid": 1.9, "ask": 2.1, "mid": 2, "last": 1.8, "quote_time": "2026-08-14T14:00:00Z",
             "delta": .25, "gamma": .01, "theta": -.05, "vega": .1, "rho": .01}]


def test_stock_and_option_risk_and_csv_roundtrip(tmp_path: Path):
    path = tmp_path / "portfolio.json"
    portfolio_risk.set_cash(5000, path)
    portfolio_risk.add_position({"strategy": "Covered call", "asset_type": "stock", "ticker": "X", "quantity": 100, "entry_price": 100}, path)
    option = portfolio_risk.add_position({"strategy": "Covered call", "asset_type": "option", "ticker": "X", "option_type": "call", "strike": 110, "expiration": "2026-08-21", "quantity": -1, "entry_price": 1.5, "fees": 1}, path)
    result = portfolio_risk.status(quote_fn=_quote, chain_fn=_chain, path=path)
    assert result["execution_capability"] is False
    assert result["summary"]["aggregate_greeks"]["delta"] == 75
    assert result["summary"]["unrealized_pnl"] == 449
    assert result["expiration_calendar"][0]["short_contracts"] == 1
    assert result["strategy_groups"][0]["name"] == "Covered call"
    exported = portfolio_risk.export_csv(path)
    other = tmp_path / "other.json"
    assert portfolio_risk.import_csv(exported, other)["imported"] == 2
    assert len(portfolio_risk._load(other)["positions"]) == 2
    assert portfolio_risk.delete_position(option["id"], path)["deleted"] == option["id"]


def test_unknown_option_mark_and_greek_stay_unknown(tmp_path: Path):
    path = tmp_path / "portfolio.json"
    portfolio_risk.add_position({"asset_type": "option", "ticker": "X", "option_type": "put", "strike": 90, "expiration": "2026-08-21", "quantity": 1, "entry_price": 1}, path)
    result = portfolio_risk.status(quote_fn=_quote, chain_fn=lambda *_: [], path=path)
    assert result["positions"][0]["current_mark"] is None
    assert result["summary"]["aggregate_greeks"]["delta"] is None
    assert {row["kind"] for row in result["exceptions"]} == {"MARK_UNKNOWN"}
