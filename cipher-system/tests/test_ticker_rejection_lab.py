from __future__ import annotations

import pandas as pd

from core.ticker_rejection_lab import RejectionCandidate, prepare_panel, signal_trades


def _gex() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "snapshot_id": 1, "ticker": "MU", "date": "2026-08-10",
            "timestamp": pd.Timestamp("2026-08-10T14:00:00Z"),
            "call_wall_strike": 101.0, "put_wall_strike": 98.0,
            "gex_balance": 0.5, "vex_balance": -0.3, "near_oi_balance": 0.4,
            "available_rate": 1.0, "total_oi": 10_000.0,
            "call_wall_move": 0.0, "put_wall_move": 0.0,
        }
    ])


def _bars() -> dict[str, list[dict]]:
    rows = []
    for index in range(14):
        timestamp = pd.Timestamp("2026-08-10T14:00:00Z") + pd.Timedelta(minutes=5 * index)
        rows.append({"time": timestamp.isoformat(), "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1000})
    # A bearish call-wall rejection after an advance, followed by a target hit.
    rows[5].update(open=100.8, high=101.2, low=100.2, close=100.3, volume=2000)
    rows[6].update(open=100.2, high=100.3, low=99.0, close=99.2, volume=1500)
    return {"MU": rows}


def test_prepare_panel_never_backfills_future_snapshot():
    bars = _bars()
    bars["MU"].insert(0, {"time": "2026-08-10T13:55:00Z", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    panel = prepare_panel(_gex(), bars)
    assert panel["timestamp"].min() >= pd.Timestamp("2026-08-10T14:00:00Z")
    assert panel["snapshot_id"].eq(1).all()


def test_rejection_enters_next_bar_and_uses_conservative_bracket():
    panel = prepare_panel(_gex(), _bars())
    candidate = RejectionCandidate(0.001, 0.0, 0.003, 0.35, "vex_aligned", 6, 1.0)
    trades = signal_trades(panel, candidate)
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["direction"] == -1
    assert trade["signal_timestamp"] == pd.Timestamp("2026-08-10T14:25:00Z")
    assert trade["entry_timestamp"] == pd.Timestamp("2026-08-10T14:30:00Z")
    assert trade["exit_reason"] == "target"


def test_stale_snapshot_is_not_carried_indefinitely():
    panel = prepare_panel(_gex(), _bars(), maximum_snapshot_age_minutes=20)
    assert panel["timestamp"].max() == pd.Timestamp("2026-08-10T14:20:00Z")
