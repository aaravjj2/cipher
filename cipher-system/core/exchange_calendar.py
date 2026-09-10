"""Small deterministic NYSE session calendar used by runtime gates.

The remote Alpaca calendar remains authoritative for historical bulk jobs.  Runtime
gates need a local answer when the provider is unavailable, so the regular full-day
US exchange holidays are calculated here with no network dependency.
"""
from __future__ import annotations

from datetime import date, timedelta


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    day = first_next - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter(year: int) -> date:
    """Gregorian Easter (Anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f, g = (b + 8) // 25, (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


def holidays(year: int) -> frozenset[date]:
    days = {
        date(year, 1, 1) if date(year, 1, 1).weekday() == 5 else _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),       # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),       # Washington's Birthday
        _easter(year) - timedelta(days=2), # Good Friday
        _last_weekday(year, 5, 0),         # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),       # Labor Day
        _nth_weekday(year, 11, 3, 4),      # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))
    # NYSE does not observe Saturday New Year's Day on the preceding Friday.
    # See NYSE's published 2026–2028 calendar (January 1, 2028 footnote).
    return frozenset(days)


def is_session(day: date) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year) and day not in holidays(day.year - 1)


def previous_session(day: date) -> date:
    candidate = day - timedelta(days=1)
    while not is_session(candidate):
        candidate -= timedelta(days=1)
    return candidate


def trading_days_between(start: date, end: date) -> int:
    """Count exchange sessions strictly after start through end."""
    if end < start:
        return -trading_days_between(end, start)
    count, current = 0, start + timedelta(days=1)
    while current <= end:
        count += is_session(current)
        current += timedelta(days=1)
    return count
