from pathlib import Path

import pytest

from core import alerts


def test_alert_rule_lifecycle_is_local_and_order_free(tmp_path: Path):
    db = tmp_path / "alerts.sqlite"
    rule = alerts.add_rule(ticker="spy", kind="price_above", threshold="700", db_path=db)
    payload = alerts.list_rules(db)
    assert payload["execution_capability"] is False
    assert payload["rules"] == [rule]
    assert alerts.delete_rule(rule["id"], db_path=db) == {"deleted": rule["id"]}
    assert alerts.list_rules(db)["rules"] == []


@pytest.mark.parametrize("ticker,kind,threshold", [("", "price_above", 1), ("SPY", "orders", 1), ("SPY", "price_above", "x")])
def test_alert_rule_validation(tmp_path: Path, ticker, kind, threshold):
    with pytest.raises(ValueError):
        alerts.add_rule(ticker=ticker, kind=kind, threshold=threshold, db_path=tmp_path / "a.sqlite")
