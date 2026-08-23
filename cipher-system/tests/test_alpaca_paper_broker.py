from datetime import datetime, timedelta, timezone

import pytest

from core.paper_executor.alpaca_paper_broker import AlpacaPaperBroker, PAPER_BASE_URL, intent_payload
from core.paper_executor.models import TradeIntent


def intent() -> TradeIntent:
    return TradeIntent(
        decision_id="episode-123", evidence_snapshot_id="snapshot-1", ticker="MU",
        contract_symbol="MU260828C01000000", side="buy", quantity=1,
        limit_price=4.25, target=1010.0, invalidation=980.0,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )


def test_rejects_every_non_paper_boundary():
    with pytest.raises(ValueError, match="PK-prefixed"):
        AlpacaPaperBroker("AKLIVE", "secret")
    with pytest.raises(ValueError, match="locked"):
        AlpacaPaperBroker("PKPAPER", "secret", base_url="https://api.alpaca.markets")
    assert PAPER_BASE_URL == "https://paper-api.alpaca.markets"


def test_submit_is_limit_day_paper_and_idempotent():
    calls = []
    created = []

    def transport(method, path, payload):
        calls.append((method, path, payload))
        if method == "GET" and path.startswith("/v2/orders?"):
            return created
        if method == "POST":
            order = {"id": "broker-1", "status": "accepted", **payload, "filled_qty": "0"}
            created.append(order)
            return order
        raise AssertionError((method, path))

    broker = AlpacaPaperBroker("PKPAPER", "secret", transport=transport)
    first = broker.submit(intent())
    second = broker.submit(intent())
    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][1] == "/v2/orders"
    assert posts[0][2] == {
        "symbol": "MU260828C01000000", "qty": "1", "side": "buy",
        "type": "limit", "time_in_force": "day", "limit_price": "4.25",
        "client_order_id": intent().client_order_id,
    }
    assert first["paper_only"] is True
    assert second["id"] == "broker-1"


def test_account_and_positions_are_normalized_without_credentials():
    def transport(method, path, payload):
        if path == "/v2/account":
            return {"id": "account-1", "status": "ACTIVE", "currency": "USD", "buying_power": "100000", "equity": "100000", "trading_blocked": False, "account_blocked": False}
        if path == "/v2/positions":
            return [{"symbol": "MU260828C01000000", "qty": "1", "side": "long", "avg_entry_price": "4.20", "market_value": "430", "unrealized_pl": "10"}]
        raise AssertionError(path)

    broker = AlpacaPaperBroker("PKPAPER", "secret", account_id="account-1", transport=transport)
    assert broker.account()["paper_only"] is True
    assert broker.positions()[0]["average_entry_price"] == 4.2
    assert "secret" not in repr(broker.account())


def test_intent_payload_has_stable_client_id_and_explicit_boundary():
    payload = intent_payload(intent())
    assert payload["client_order_id"].startswith("cipher-")
    assert payload["paper_only"] is True
    assert payload["expires_at"].endswith("+00:00")
