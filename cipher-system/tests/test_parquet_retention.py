from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
from scripts import parquet_retention


def _database(path: Path):
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE tradier_stream_events (
          id INTEGER PRIMARY KEY, run_id INTEGER, captured_at TEXT, provider_ts TEXT,
          event_type TEXT, symbol TEXT, bid REAL, ask REAL, last REAL, price REAL,
          size REAL, raw_json TEXT, asset_class TEXT, underlying TEXT,
          option_expiration TEXT, option_type TEXT, strike REAL)""")
        db.execute("INSERT INTO tradier_stream_events VALUES (1,1,'2026-08-01T12:00:00Z',NULL,'trade','SPY',NULL,NULL,1,1,1,'{}','equity','SPY',NULL,NULL,NULL)")


def test_retention_is_atomic_resumable_and_never_prunes_source(tmp_path: Path):
    source, archive = tmp_path / "source.sqlite", tmp_path / "archive"
    _database(source)
    first = parquet_retention.archive_day(source, archive, date(2026, 8, 1), today=date(2026, 8, 3))
    second = parquet_retention.archive_day(source, archive, date(2026, 8, 1), today=date(2026, 8, 3))
    assert first["status"] == "archived"
    assert first["source_deleted"] is False
    assert second["status"] == "already_archived"
    assert parquet_retention.status(archive)["source_pruning_enabled"] is False
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT count(*) FROM tradier_stream_events").fetchone()[0] == 1


def test_retention_rejects_current_or_future_day(tmp_path: Path):
    with pytest.raises(ValueError, match="completed UTC"):
        parquet_retention.archive_day(tmp_path / "source", tmp_path / "archive", date(2026, 8, 3), today=date(2026, 8, 3))


def test_default_day_is_the_newest_one_the_archiver_will_accept():
    """The timer runs with no --date, so the default must never be a live day.

    `archive_day` rejects anything >= today; this pairs with it so an unattended run
    archives yesterday rather than failing or, worse, capturing a partial day.
    """
    assert parquet_retention.most_recent_completed_day(date(2026, 8, 11)) == date(2026, 8, 10)
    # Month and year boundaries are where a hand-rolled "yesterday" usually breaks.
    assert parquet_retention.most_recent_completed_day(date(2026, 8, 1)) == date(2026, 7, 31)
    assert parquet_retention.most_recent_completed_day(date(2026, 1, 1)) == date(2025, 12, 31)

    today = date(2026, 8, 11)
    default_day = parquet_retention.most_recent_completed_day(today)
    # Whatever the default is, archive_day must accept it: no ValueError.
    assert default_day < today
