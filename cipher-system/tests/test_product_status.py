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


def test_scalar_reads_bounded_status_clock_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "clock.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("create table samples(observed_at text)")
        db.execute("insert into samples values ('2026-08-14T15:00:00Z')")
    assert product_status._scalar(db_path, "select max(observed_at) from samples") == "2026-08-14T15:00:00Z"
