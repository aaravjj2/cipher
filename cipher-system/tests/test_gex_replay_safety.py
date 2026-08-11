from __future__ import annotations

import sqlite3
from pathlib import Path

from core import gex_capture, gex_replay, historical_backtest


def _seed_history(db_path: Path) -> tuple[int, int]:
    gex_capture.ensure_schema(db_path)
    with sqlite3.connect(db_path) as db:
        run_id = db.execute(
            """
            INSERT INTO gex_capture_runs
                (started_at, source, feed, depth, expirations, ticker_count, caveat)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-08-01T14:00:00Z", "test", "opra", "all", 1, 1, "test caveat"),
        ).lastrowid
        first = db.execute(
            """
            INSERT INTO gex_snapshots
                (run_id, ticker, captured_at, spot, raw_json_path,
                 global_max_strike, call_wall_strike, put_wall_strike,
                 gamma_flip_level, caveat)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "SPY",
                "2026-08-01T14:00:00Z",
                101.25,
                "first.json",
                105.0,
                110.0,
                90.0,
                100.0,
                "test caveat",
            ),
        ).lastrowid
        second = db.execute(
            """
            INSERT INTO gex_snapshots
                (run_id, ticker, captured_at, spot, raw_json_path, caveat)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "SPY",
                "2026-08-01T15:00:00Z",
                102.0,
                "second.json",
                "test caveat",
            ),
        ).lastrowid

        rows = [
            (first, "2026-08-01T14:00:00Z", 100.0, 10.0, 1),
            # An unavailable visual placeholder must never become a zero-GEX
            # observation or influence deltas/profile walls.
            (first, "2026-08-01T14:00:00Z", 101.0, 1000.0, 0),
            (second, "2026-08-01T15:00:00Z", 100.0, 15.0, 1),
        ]
        db.executemany(
            """
            INSERT INTO gex_strike_cells
                (snapshot_id, ticker, captured_at, expiration, strike,
                 call_gex, put_gex, net_gex, call_oi, put_oi, volume,
                 listed, available)
            VALUES (?, 'SPY', ?, '2026-08-21', ?, ?, ?, ?, 10, 10, 5, 1, ?)
            """,
            [
                (snapshot_id, captured_at, strike, net_gex + 2.0, -2.0, net_gex, available)
                for snapshot_id, captured_at, strike, net_gex, available in rows
            ],
        )
    return int(first), int(second)


def test_snapshot_cells_exclude_unavailable_placeholders_and_use_index(tmp_path: Path):
    db_path = tmp_path / "gex.sqlite"
    first, _ = _seed_history(db_path)

    observed = gex_replay.get_snapshot_cells(db_path, first)
    all_cells = gex_replay.get_snapshot_cells(
        db_path,
        first,
        include_unavailable=True,
    )

    assert [row["strike"] for row in observed] == [100.0]
    assert [row["strike"] for row in all_cells] == [100.0, 101.0]
    with sqlite3.connect(db_path) as db:
        plan = db.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT expiration, strike
            FROM gex_strike_cells
            WHERE snapshot_id = ? AND available = 1
            ORDER BY expiration, strike
            """,
            (first,),
        ).fetchall()
    assert "idx_gex_cells_snapshot_exp_strike" in " ".join(str(row) for row in plan)


def test_delta_gex_ignores_unavailable_placeholder_values(tmp_path: Path):
    db_path = tmp_path / "gex.sqlite"
    _seed_history(db_path)

    deltas = gex_replay.compute_delta_gex(db_path, "spy")

    assert len(deltas) == 1
    assert deltas[0]["delta_net_gex"] == 5.0
    assert [row["strike"] for row in deltas[0]["strikes"]] == [100.0]


def test_replay_payload_labels_incomplete_strikes_and_navigates(tmp_path: Path):
    db_path = tmp_path / "gex.sqlite"
    first, second = _seed_history(db_path)

    catalog = gex_replay.replay_catalog(db_path, ticker="spy", limit=99_999)
    payload = gex_replay.replay_snapshot(db_path, first)

    assert catalog["counts"] == {"tickers": 1, "snapshots": 2}
    assert [row["id"] for row in catalog["snapshots"]] == [second, first]
    assert payload is not None
    assert payload["previous"] is None
    assert payload["next"]["id"] == second
    assert payload["strikes"][0]["incomplete"] is False
    assert payload["strikes"][1]["available"] is False
    assert payload["strikes"][1]["incomplete"] is True
    assert payload["strikes"][1]["net_gex"] is None


def test_historical_profile_uses_captured_spot_and_snapshot_levels(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "gex.sqlite"
    first, _ = _seed_history(db_path)
    monkeypatch.setattr(historical_backtest, "GEX_HISTORY_DB", db_path)

    profile, spot = historical_backtest._load_snapshot_profile(first, 101.25)
    summary = historical_backtest._load_snapshot_summary(
        profile,
        {
            "put_wall_strike": 90.0,
            "call_wall_strike": 110.0,
            "gamma_flip_level": 100.0,
            "global_max_strike": 105.0,
        },
    )

    assert spot == 101.25
    assert [row["strike"] for row in profile] == [100.0]
    assert summary == {
        "put_wall_strike": 90.0,
        "call_wall_strike": 110.0,
        "gamma_flip_level": 100.0,
        "global_max_strike": 105.0,
    }


def test_forward_bar_fetch_passes_snapshot_start_to_data_source():
    calls = []

    def bars_fn(ticker, timeframe, limit, *, start=None):
        calls.append((ticker, timeframe, limit, start))
        return {
            "bars": [
                {"time": "2026-07-31T04:00:00Z"},
                {"time": "2026-08-01T04:00:00Z"},
                {"time": "2026-08-02T04:00:00Z"},
            ]
        }

    bars = historical_backtest._bars_from_date(
        bars_fn,
        "SPY",
        "2026-08-01T14:00:00Z",
        limit=5,
    )

    assert calls == [("SPY", "1Day", 35, "2026-08-01T14:00:00Z")]
    assert [row["time"] for row in bars] == [
        "2026-08-01T04:00:00Z",
        "2026-08-02T04:00:00Z",
    ]
