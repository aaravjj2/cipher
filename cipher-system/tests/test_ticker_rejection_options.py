from core.ticker_rejection_options import _intraday_contract_choices


def test_intraday_contract_choices_allow_same_day_and_respect_direction():
    rows = [
        {"symbol": "MU1", "expiration": "2026-08-14", "strike": 99, "option_type": "put"},
        {"symbol": "MU2", "expiration": "2026-08-14", "strike": 100, "option_type": "put"},
        {"symbol": "MU3", "expiration": "2026-08-14", "strike": 101, "option_type": "put"},
        {"symbol": "MUC", "expiration": "2026-08-14", "strike": 100, "option_type": "call"},
    ]
    choices = _intraday_contract_choices(rows, spot=100.0, direction=-1, signal_day="2026-08-14")
    assert choices["atm"]["symbol"] == "MU2"
    assert choices["itm"]["symbol"] == "MU3"
    assert choices["otm"]["symbol"] == "MU1"
