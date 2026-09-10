from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE.parent) not in sys.path:
    sys.path.insert(0, str(CORE.parent))

from core import product_status  # noqa: E402


def test_market_session_boundaries_are_exchange_local() -> None:
    assert product_status.market_session(datetime(2026, 8, 14, 13, 29, tzinfo=timezone.utc))["phase"] == "premarket"
    assert product_status.market_session(datetime(2026, 8, 14, 13, 30, tzinfo=timezone.utc))["phase"] == "regular"
    assert product_status.market_session(datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc))["phase"] == "postmarket"
    assert product_status.market_session(datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc))["phase"] == "closed"
    assert product_status.market_session(datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc))["phase"] == "closed"


def test_freshness_distinguishes_stale_regular_from_last_session() -> None:
    regular_now = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)
    regular = product_status.market_session(regular_now)
    stale = product_status.freshness(
        "flow", "2026-08-13T19:59:00Z", now=regular_now, session=regular,
        stale_after_seconds=120, source="test",
    )
    assert stale["state"] == "stale"

    closed_now = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)
    closed = product_status.market_session(closed_now)
    last = product_status.freshness(
        "flow", "2026-08-14T19:59:00Z", now=closed_now, session=closed,
        stale_after_seconds=120, source="test",
    )
    assert last["state"] == "last_session"


def test_missing_input_is_never_reported_current() -> None:
    now = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)
    item = product_status.freshness(
        "gex", None, now=now, session=product_status.market_session(now),
        stale_after_seconds=120, source="test",
    )
    assert item["state"] == "unavailable"
    assert item["age_seconds"] is None


def test_old_off_hours_input_is_stale_not_last_session() -> None:
    now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
    item = product_status.freshness(
        "universe", "2026-08-14T20:00:00Z", now=now,
        session=product_status.market_session(now), stale_after_seconds=14 * 86400,
        source="test",
    )
    assert item["state"] == "stale"


def test_retired_flow_tape_is_truthfully_snapshot_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(product_status, "GEX_DB", tmp_path / "missing.sqlite")
    result = product_status.status(
        ticker="SPY", quote=None, flow_session=None,
        universe_meta={"as_of": "2026-09-04T20:00:00Z", "source": "test"},
        now=datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc),
    )
    flow = next(row for row in result["items"] if row["name"] == "flow")
    assert flow["state"] == "snapshot_only"
    assert flow not in result["exceptions"]


def test_scalar_reads_bounded_status_clock_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "clock.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("create table samples(observed_at text)")
        db.execute("insert into samples values ('2026-08-14T15:00:00Z')")
    assert product_status._scalar(db_path, "select max(observed_at) from samples") == "2026-08-14T15:00:00Z"


def test_weekend_non_market_jobs_use_their_actual_ttl():
    now = datetime(2026, 9, 6, 22, tzinfo=timezone.utc)
    for stamp, expected in [("2026-09-06T10:00:00Z", "current"), ("2026-09-04T10:00:00Z", "stale")]:
        item = product_status.freshness("research", stamp, now=now,
            session=product_status.market_session(now), stale_after_seconds=36*3600,
            source="test", market_bound=False)
        assert item["state"] == expected


def test_future_clock_is_not_fresh():
    now = datetime(2026, 9, 6, 22, tzinfo=timezone.utc)
    item = product_status.freshness("quote", "2026-09-07T22:00:00Z", now=now,
        session=product_status.market_session(now), stale_after_seconds=30, source="test")
    assert item["state"] == "unavailable"
