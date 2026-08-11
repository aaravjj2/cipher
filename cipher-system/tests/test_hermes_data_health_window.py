"""The data-health alert's capture window must match the collector's.

`market_window("tradier")` claimed 07:30-17:00 ET while
`core/tradier_stream_capture.py:regular_session_open` only records events from 09:30 to
16:00 ET. For about three hours of every trading day the alert therefore expected data
that by design does not exist, reported `stale`, and pushed it to Telegram. An alert that
is wrong on a schedule trains the reader to ignore the channel that also carries real
outages, so these tests pin the two definitions together.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "scripts", ROOT / "core"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import hermes_data_health_alerts as health  # noqa: E402
from tradier_stream_capture import regular_session_open  # noqa: E402

NY = ZoneInfo("America/New_York")


def at(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NY)


# 2026-08-11 is a Tuesday; 2026-08-15 is a Saturday.
@pytest.mark.parametrize(
    "moment,expected,why",
    [
        (at(2026, 8, 11, 7, 45), False, "process is up at 07:45 but the collector records nothing"),
        (at(2026, 8, 11, 9, 15), False, "the false-stale window that fired every morning"),
        (at(2026, 8, 11, 9, 30), True, "regular session opens"),
        (at(2026, 8, 11, 12, 0), True, "mid-session"),
        (at(2026, 8, 11, 15, 59), True, "still inside the session"),
        (at(2026, 8, 11, 16, 30), False, "after the close the collector stops recording"),
        (at(2026, 8, 15, 12, 0), False, "weekend"),
    ],
)
def test_tradier_window_tracks_when_data_actually_flows(moment, expected, why):
    assert health.market_window("tradier", moment) is expected, why


def test_tradier_window_is_the_collector_definition_not_a_copy():
    """Checked minute by minute so the two cannot drift apart again silently."""
    for hour in range(0, 24):
        for minute in (0, 15, 29, 30, 31, 45, 59):
            moment = at(2026, 8, 11, hour, minute)
            assert health.market_window("tradier", moment) is bool(regular_session_open(moment)), (
                f"disagreement at {hour:02d}:{minute:02d} ET"
            )


def test_gex_window_keeps_its_closing_tail():
    """GEX and chains intentionally run slightly past the close for the final snapshot."""
    assert health.market_window("gex", at(2026, 8, 11, 16, 5)) is True
    assert health.market_window("gex", at(2026, 8, 11, 16, 20)) is False
    assert health.market_window("gex", at(2026, 8, 11, 9, 15)) is False
    # And the tail is *not* granted to tradier, whose collector stops at 16:00 exactly.
    assert health.market_window("tradier", at(2026, 8, 11, 16, 5)) is False


def test_off_hours_is_reported_rather_than_stale():
    """The whole point: outside the window a quiet collector is not an incident."""
    status, detail = health.status_from_latest({"ok": True, "latest": None}, max_age_minutes=30, active=False)
    assert status == "off_hours"
    assert "outside capture window" in detail
