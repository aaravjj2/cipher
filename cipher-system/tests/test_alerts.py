from pathlib import Path

import pytest

from core import alerts


def test_alert_rule_lifecycle_is_local_and_order_free(tmp_path: Path):
    db = tmp_path / "alerts.sqlite"
    rule = alerts.add_rule(ticker="spy", kind="price_above", threshold="700", db_path=db)
    payload = alerts.list_rules(db)
    assert payload["execution_capability"] is False
    assert payload["deliveries"] == []
    assert payload["rules"] == [rule]
    assert alerts.delete_rule(rule["id"], db_path=db) == {"deleted": rule["id"]}
    assert alerts.list_rules(db)["rules"] == []


def test_delivery_ledger_is_idempotent(tmp_path: Path):
    db = tmp_path / "alerts.sqlite"
    rule = alerts.add_rule(ticker="SPY", kind="flow_premium_above", threshold=1000, db_path=db)
    args = dict(rule_id=rule["id"], observed_at="2026-08-14T14:00:00Z", observed=1200,
                threshold=1000, channel="telegram", status="sent", message="crossed", db_path=db)
    alerts.record_delivery(**args)
    alerts.record_delivery(**args)
    assert len(alerts.list_rules(db)["deliveries"]) == 1


@pytest.mark.parametrize("ticker,kind,threshold", [("", "price_above", 1), ("SPY", "orders", 1), ("SPY", "price_above", "x")])
def test_alert_rule_validation(tmp_path: Path, ticker, kind, threshold):
    with pytest.raises(ValueError):
        alerts.add_rule(ticker=ticker, kind=kind, threshold=threshold, db_path=tmp_path / "a.sqlite")
