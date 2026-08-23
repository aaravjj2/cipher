from __future__ import annotations

from core.overnight_close_open_lab import select_option_observations, stock_trades, summarize


def test_stock_close_to_next_open_uses_only_adjacent_sessions_and_costs() -> None:
    rows = [
        {"date": "2026-08-17", "open": 99.0, "close": 100.0},
        {"date": "2026-08-18", "open": 102.0, "close": 101.0},
        {"date": "2026-08-19", "open": 100.0, "close": 103.0},
    ]
    trades = stock_trades("MU", rows, cost_bps_per_side=2.0)
    assert len(trades) == 2
    assert trades[0]["entry_date"] == "2026-08-17"
    assert trades[0]["exit_date"] == "2026-08-18"
    assert round(trades[0]["gross_return_pct"], 6) == 2.0
    assert round(trades[0]["net_return_pct"], 6) == 1.96
    assert trades[1]["entry_price"] == 101.0
    assert trades[1]["exit_price"] == 100.0


def test_summary_reports_fixed_notional_and_path_drawdown() -> None:
    report = summarize([
        {"net_return_pct": 10.0},
        {"net_return_pct": -5.0},
        {"net_return_pct": 2.0},
    ])
    assert report["trades"] == 3
    assert report["wins"] == 2
    assert report["losses"] == 1
    assert round(report["fixed_notional_total_return_pct"], 6) == 7.0
    assert round(report["profit_factor"], 6) == 2.4
    assert round(report["max_drawdown_pct"], 6) == -5.0


def test_option_contract_is_not_changed_when_atm_contract_lacks_future_print() -> None:
    base = {
        "underlying": "MU", "entry_date": "2026-08-17", "exit_date": "2026-08-18",
        "option_type": "call", "expiration": "2026-08-21", "dte": 4,
        "underlying_close": 100.0, "entry_price": 2.0, "source_dataset": "demo",
    }
    selected, audit = select_option_observations([
        {**base, "symbol": "MU260821C00100000", "strike": 100.0, "exit_price": None},
        {**base, "symbol": "MU260821C00105000", "strike": 105.0, "exit_price": 4.0},
    ])
    assert selected[0]["symbol"] == "MU260821C00100000"
    assert selected[0]["exit_price"] is None
    assert audit == {"selected_at_close": 1, "observed_next_open": 0, "missing_next_open": 1, "missing_by_underlying": {"MU": 1}}
