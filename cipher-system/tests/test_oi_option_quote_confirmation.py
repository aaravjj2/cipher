from core.oi_option_quote_confirmation import _contract_choices


def test_contract_choices_reject_same_day_and_map_moneyness():
    contracts = [
        {"symbol": "C95", "option_type": "call", "strike": 95, "expiration": "2026-08-21"},
        {"symbol": "C100", "option_type": "call", "strike": 100, "expiration": "2026-08-21"},
        {"symbol": "C105", "option_type": "call", "strike": 105, "expiration": "2026-08-21"},
        {"symbol": "C0", "option_type": "call", "strike": 100, "expiration": "2026-08-14"},
    ]
    result = _contract_choices(contracts, spot=101, direction=1, signal_day="2026-08-14")
    assert result["atm"]["symbol"] == "C100"
    assert result["itm"]["symbol"] == "C100"
    assert result["otm"]["symbol"] == "C105"
    assert all(row["symbol"] != "C0" for row in result.values())


def test_contract_choices_select_puts_for_short_direction():
    contracts = [
        {"symbol": "P95", "option_type": "put", "strike": 95, "expiration": "2026-08-21"},
        {"symbol": "P100", "option_type": "put", "strike": 100, "expiration": "2026-08-21"},
        {"symbol": "P105", "option_type": "put", "strike": 105, "expiration": "2026-08-21"},
    ]
    result = _contract_choices(contracts, spot=101, direction=-1, signal_day="2026-08-14")
    assert result["atm"]["symbol"] == "P100"
    assert result["itm"]["symbol"] == "P105"
    assert result["otm"]["symbol"] == "P100"
