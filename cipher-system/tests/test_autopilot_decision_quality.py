from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.autopilot_decision_quality import analyze


def _row(ticker: str, entry: float, exit_: float, mfe: float, hour: str = "13:35") -> dict:
    opened = f"2026-08-25T{hour}:00+00:00"
    return {
        "ticker": ticker, "direction": "bullish", "quantity": 1,
        "entry_price": entry, "exit_price": exit_, "exit_reason": "option_stop_loss",
        "opened_at": opened, "closed_at": opened,
        "payload_json": json.dumps({"mfe_pct": mfe, "mae_pct": -20.0}),
    }


def test_expectancy_and_dead_on_arrival_are_computed() -> None:
    rows = [
        _row("AAA", 10.0, 11.0, 12.0),          # win
        _row("BBB", 10.0, 8.5, 1.0),            # DOA loss
        _row("CCC", 10.0, 9.0, 0.0),            # DOA loss
    ]
    report = analyze(rows)
    assert report["trade_count"] == 3
    exp = report["expectancy"]
    assert exp["win_rate_pct"] == 33.3
    assert exp["expectancy_per_trade_pct"] < 0
    assert "below" in exp["note"]
    doa = report["dead_on_arrival"]
    assert doa["count"] == 2 and set(doa["tickers"]) == {"BBB", "CCC"}
    assert doa["share_pct"] == 66.7


def test_buckets_group_by_entry_hour_and_reason() -> None:
    rows = [
        _row("AAA", 10.0, 11.0, 12.0, hour="14:20"),
        _row("BBB", 10.0, 9.0, 0.5, hour="13:35"),
        _row("CCC", 10.0, 9.5, 2.0, hour="13:35"),
    ]
    report = analyze(rows)
    hours = report["by_entry_hour_et"]
    assert hours["14:20"]["wins"] == 1 and hours["13:35"]["wins"] == 0
    reasons = report["by_exit_reason"]
    assert reasons["option_stop_loss"]["trades"] == 3
    tickers = report["by_ticker"]
    assert set(tickers) == {"AAA", "BBB", "CCC"}
    assert tickers["AAA"]["wins"] == 1 and tickers["AAA"]["trades"] == 1


def test_empty_ledger_produces_honest_zeros() -> None:
    report = analyze([])
    assert report["trade_count"] == 0
    assert report["expectancy"] == {}
    assert report["dead_on_arrival"]["share_pct"] is None
