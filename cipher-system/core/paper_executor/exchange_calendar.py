"""Closing deadlines derived from the existing local exchange calendar."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from core.exchange_calendar import is_session, _nth_weekday

ET = ZoneInfo("America/New_York")


def session_close(day):
    if not is_session(day):
        return None
    early = (day == _nth_weekday(day.year, 11, 3, 4) + timedelta(days=1)
             or (day.month == 12 and day.day == 24)
             or (day.month == 7 and day.day == 3))
    return datetime.combine(day, time(13 if early else 16), ET)


def closing_reason(opened_at, now, force_close_time, allow_overnight=False, expiration=None):
    local = now.astimezone(ET)
    if expiration and local.date().isoformat() > expiration:
        return "expiration_recovery"
    if not allow_overnight and local.date() > opened_at.astimezone(ET).date():
        return "system_shutdown_recovery"
    close = session_close(local.date())
    if close:
        hh, mm = map(int, force_close_time.split(":"))
        deadline = min(datetime.combine(local.date(), time(hh, mm), ET), close - timedelta(minutes=15))
        if local >= deadline:
            return "force_close_time"
    return None
