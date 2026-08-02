from __future__ import annotations

import sqlite3

from core import kronos_research


def test_load_local_ohlcv_rows_falls_back_to_sqlite_and_aggregates(tmp_path, monkeypatch):
    db_path = tmp_path / "historical_bars.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            create table historical_bars (
                symbol text not null,
                timestamp text not null,
                open real,
                high real,
                low real,
                close real,
                volume real,
                vwap real,
                trades integer,
                primary key (symbol, timestamp)
            )
            """
        )
        db.executemany(
            "insert into historical_bars(symbol,timestamp,open,high,low,close,volume) values(?,?,?,?,?,?,?)",
            [
                ("MSFT", "2026-07-30T13:30:00Z", 100, 101, 99, 100.5, 10),
                ("MSFT", "2026-07-30T13:31:00Z", 100.5, 102, 100, 101.5, 20),
                ("MSFT", "2026-07-30T13:34:00Z", 101.5, 103, 101, 102.5, 30),
                ("MSFT", "2026-07-30T13:35:00Z", 102.5, 104, 102, 103.5, 40),
                ("COIN", "2026-07-28T04:00:00Z", 200, 205, 198, 204, 100),
                ("COIN", "2026-07-29T04:00:00Z", 204, 208, 202, 206, 110),
                ("COIN", "2026-07-30T04:00:00Z", 206, 210, 205, 209, 120),
            ],
        )
    monkeypatch.setattr(kronos_research, "HISTORICAL_BARS_DB", db_path)
    monkeypatch.setattr(kronos_research, "STOCK_DATA_ROOT", tmp_path / "missing")

    rows = kronos_research.load_local_ohlcv_rows("msft", "5m")

    assert len(rows) == 2
    assert rows[0]["open"] == 100
    assert rows[0]["high"] == 103
    assert rows[0]["low"] == 99
    assert rows[0]["close"] == 102.5
    assert rows[0]["volume"] == 60
    assert rows[1]["open"] == 102.5
    assert rows[1]["close"] == 103.5
    assert kronos_research.load_local_ohlcv_rows("COIN", "5m") == []
