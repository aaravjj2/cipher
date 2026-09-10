from datetime import date, datetime, timezone

from core.exchange_calendar import is_session, previous_session, trading_days_between
from core.paper_executor.autopilot_planner import AutopilotPhase, phase_at


def test_weekends_and_exchange_holidays_are_closed():
    assert not is_session(date(2026, 9, 6))
    assert not is_session(date(2026, 9, 7))  # Labor Day
    assert not is_session(date(2026, 11, 26))  # Thanksgiving
    assert is_session(date(2026, 9, 8))


def test_phase_and_previous_session_use_exchange_calendar():
    labor_day_midday = datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)
    assert phase_at(labor_day_midday) is AutopilotPhase.CLOSED
    assert previous_session(date(2026, 9, 7)) == date(2026, 9, 4)
    assert trading_days_between(date(2026, 9, 4), date(2026, 9, 8)) == 1
