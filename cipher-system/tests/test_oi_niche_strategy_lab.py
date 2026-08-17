from __future__ import annotations

import pandas as pd

from core.oi_niche_strategy_lab import Candidate, candidate_catalog, signal_trades, summarize


def _panel() -> pd.DataFrame:
    rows = []
    for ticker, wall in (("AAA", 101.0), ("BBB", 201.0)):
        for day in ("2026-08-10", "2026-08-11"):
            for minute, spot in ((900, wall - 1.0), (960, wall - 0.2), (1200, wall - 1.2)):
                rows.append({
                    "ticker": ticker,
                    "date": day,
                    "timestamp": pd.Timestamp(f"{day}T{minute // 60:02d}:{minute % 60:02d}:00Z"),
                    "minute_utc": minute,
                    "spot": spot,
                    "available_rate": 1.0,
                    "total_oi": 10_000.0,
                    "forward_return": (wall - 1.2) / spot - 1.0,
                    "alpha_forward_return": (wall - 1.2) / spot - 1.0,
                    "call_wall_distance": (wall - spot) / spot,
                    "put_wall_distance": -0.05,
                    "day_return": 0.01,
                    "gex_balance": 0.7,
                    "snapshot_return": 0.005,
                    "oi_balance": 0.0,
                    "near_oi_balance": 0.0,
                    "vex_balance": -0.7,
                    "front_oi_share": 0.8,
                    "global_max_distance": 0.002,
                    "prev_spot": spot - 0.5,
                    "prev_gamma_flip_level": wall - 0.8,
                    "gamma_flip_level": wall - 0.4,
                    "call_wall_move": 0.0,
                    "put_wall_move": 0.0,
                    "gex_balance_delta": 0.0,
                    "volume_oi_ratio": 0.01,
                })
    return pd.DataFrame(rows)


def test_catalog_is_large_deterministic_and_unique():
    first = candidate_catalog()
    second = candidate_catalog()
    assert len(first) >= 200
    assert [row.candidate_id for row in first] == [row.candidate_id for row in second]
    assert len({row.candidate_id for row in first}) == len(first)


def test_signal_trades_keeps_only_first_trigger_per_ticker_day():
    candidate = Candidate(
        "call_wall_rejection",
        {"distance": 0.01, "momentum": 0.0, "gex": 0.5},
        "short",
        "test",
    )
    trades = signal_trades(_panel(), candidate)
    assert len(trades) == 4
    assert (trades["direction"] == -1.0).all()
    assert trades.groupby(["ticker", "date"]).size().max() == 1


def test_costs_are_applied_round_trip_and_can_destroy_edge():
    trades = pd.DataFrame({
        "date": ["2026-08-10", "2026-08-11", "2026-08-12"],
        "ticker": ["AAA", "AAA", "AAA"],
        "direction": [1.0, 1.0, 1.0],
        "raw_gross_return": [0.006, 0.006, 0.006],
        "alpha_gross_return": [0.006, 0.006, 0.006],
    })
    low = summarize(trades, cost_bps_per_side=10)
    high = summarize(trades, cost_bps_per_side=50)
    assert low["mean_alpha_return_pct"] > 0
    assert high["mean_alpha_return_pct"] < 0
