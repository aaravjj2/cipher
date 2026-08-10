from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from scripts import parquet_offload


def test_parquet_pilot_preserves_every_event_value(tmp_path: Path):
    database = tmp_path / "events.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """create table tradier_stream_events (
                id integer primary key, run_id integer, captured_at text, provider_ts text,
                event_type text, symbol text, bid real, ask real, last real, price real,
                size real, raw_json text, asset_class text, underlying text,
                option_expiration text, option_type text, strike real
            )"""
        )
        connection.executemany(
            "insert into tradier_stream_events values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, 1, "2026-08-09T23:59:59Z", None, "quote", "SPY", 1.0, 1.1, None, None, 2.0, "{}", "option", "SPY", "2026-08-14", "call", 500.0),
                (2, 1, "2026-08-10T14:30:00Z", "1", "quote", "SPY", 2.0, 2.1, None, None, 3.0, '{"exact":true}', "option", "SPY", "2026-08-14", "put", 500.0),
                (3, 1, "2026-08-10T14:30:01Z", "2", "trade", "AAPL", None, None, 4.2, 4.2, 1.0, '{"text":"unchanged"}', "equity", "AAPL", None, None, None),
                (4, 1, "2026-08-11T00:00:00Z", None, "quote", "QQQ", 1.0, 1.2, None, None, 1.0, "{}", "equity", "QQQ", None, None, None),
            ],
        )

    output = tmp_path / "day.parquet"
    report = parquet_offload.export_day(database, output, date(2026, 8, 10))

    assert report["source_fingerprint"]["row_count"] == 2
    assert report["round_trip_logical_match"] is True
    assert report["source_deleted"] is False
    assert output.is_file()


def test_parquet_pilot_refuses_to_overwrite(tmp_path: Path):
    output = tmp_path / "existing.parquet"
    output.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        parquet_offload.export_day(tmp_path / "missing.sqlite", output, date(2026, 8, 10))
