"""Read-only account tool tests - broker adapter fully faked, no network."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from core.copilot import account


class FakeBroker:
    def __init__(self):
        self.account_payload = {"status": "ACTIVE", "equity": "99817.48", "cash": "99817.48", "trading_blocked": False}
        self.positions_payload = []
        self.orders_payload = []

    def account(self):
        return dict(self.account_payload)

    def positions(self):
        return list(self.positions_payload)

    def orders(self, status="all", limit=100):
        self.last_orders_args = (status, limit)
        return list(self.orders_payload)


@pytest.fixture(autouse=True)
def fake_broker(monkeypatch):
    broker = FakeBroker()
    monkeypatch.setattr(account, "_broker", lambda: broker)
    account.FakeBroker = FakeBroker  # type: ignore[attr-defined] - test handle
    yield broker


def test_get_account_maps_fields():
    out = account.get_account()
    assert out["source"] == "alpaca_paper_account"
    assert out["equity"] == "99817.48"
    assert out["trading_blocked"] is False
    assert "PAPER" in out["note"]


def test_get_positions_flags_option_symbols(fake_broker):
    fake_broker.positions_payload = [
        {"symbol": "SNDK260828C01720000", "quantity": 1.0, "side": "long",
         "average_entry_price": 5.0, "market_value": 430.0, "unrealized_pl": -70.0},
        {"symbol": "AAPL", "quantity": 10.0, "side": "long",
         "average_entry_price": 220.0, "market_value": 2250.0, "unrealized_pl": 50.0},
    ]
    out = account.get_positions()
    by_sym = {row["symbol"]: row for row in out["positions"]}
    assert by_sym["SNDK260828C01720000"]["is_option"] is True
    assert by_sym["AAPL"]["is_option"] is False
    assert out["position_count"] == 2


def test_errors_pass_through_as_data(monkeypatch):
    def boom():
        raise RuntimeError("alpaca unreachable")

    monkeypatch.setattr(account, "_broker", boom)
    assert "alpaca unreachable" in account.get_account()["error"]
    assert "alpaca unreachable" in account.get_trade_history()["error"]


def test_missing_credentials_surface_cleanly(monkeypatch):
    def no_creds():
        raise ValueError("no Alpaca credentials configured (ALPACA_ALGO_KEY/SECRET)")

    monkeypatch.setattr(account, "_broker", no_creds)
    # _broker raises before any network call; get_account converts to data.
    assert "credentials" in account.get_account()["error"]


def _order(symbol, side, qty, price, at):
    return {
        "symbol": symbol, "side": side, "filled_quantity": qty,
        "average_fill_price": price, "filled_at": at, "submitted_at": at, "status": "filled",
    }


def test_trade_history_fifo_pairing(fake_broker):
    fake_broker.orders_payload = [
        _order("XYZ", "buy", 1, 5.0, "2026-08-25T14:20:00Z"),
        _order("XYZ", "sell", 1, 4.0, "2026-08-25T14:25:00Z"),
        _order("ABC", "buy", 2, 2.0, "2026-08-25T13:35:00Z"),
        _order("ABC", "sell", 1, 2.5, "2026-08-25T13:42:00Z"),
        _order("DEF", "sell", 1, 3.0, "2026-08-25T15:00:00Z"),
        _order("DEF", "buy", 1, 3.5, "2026-08-25T15:10:00Z"),
    ]
    out = account.get_trade_history(days=7, now=datetime(2026, 8, 26, tzinfo=timezone.utc))
    by_sym = {t["symbol"]: t for t in out["trades"]}
    assert by_sym["XYZ"]["realized_pnl"] == -1.0      # long loss (equity, x1)
    assert by_sym["ABC"]["realized_pnl"] == 0.5       # half lot closed
    assert out["open_lots_left"] == {"ABC": 1}
    assert by_sym["DEF"]["realized_pnl"] == -0.5      # short covered higher
    assert out["realized_pnl_total"] == -1.0


def test_option_symbols_get_100x_multiplier(fake_broker):
    fake_broker.orders_payload = [
        _order("SNDK260828C01720000", "buy", 1, 5.0, "2026-08-25T14:20:00Z"),
        _order("SNDK260828C01720000", "sell", 1, 4.0, "2026-08-25T14:25:00Z"),
    ]
    out = account.get_trade_history(days=7, now=datetime(2026, 8, 26, tzinfo=timezone.utc))
    trade = out["trades"][0]
    assert trade["realized_pnl"] == -1.0
    assert trade["realized_pnl_dollars"] == -100.0
    assert out["realized_pnl_dollars_total"] == -100.0
    assert account._contract_multiplier("AAPL") == 1


def test_orders_tool_uses_adapter_read_path(fake_broker):
    fake_broker.orders_payload = [_order("AAPL", "buy", 1, 220.0, "2026-08-25T13:35:00Z")]
    out = account.get_orders(status="closed", limit=5)
    assert fake_broker.last_orders_args == ("closed", 5)
    assert out["orders"][0]["symbol"] == "AAPL"
