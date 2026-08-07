"""Prior-session and pre/post-market price levels for Night Vision.

The real product draws these on every chart alongside the exposure levels —
previous day high/low, previous week high/low, pre-market high/low and the
post-market low — and treats them as reaction zones in their own right. Nothing
in this codebase produced them; Night Vision only ever returned GEX/VEX-derived
strikes.

They are computed from minute bars rather than daily bars on purpose. A daily bar
covers the regular session only, so it cannot distinguish pre-market from regular
trade, and the whole point of a pre-market high is that it formed *before* the
open. Alpaca's minute bars carry extended-hours prints, so the session split has
to be done here, in exchange local time.

Session boundaries (US equities, America/New_York):
    pre-market      04:00 - 09:30
    regular         09:30 - 16:00
    post-market     16:00 - 20:00

Research-only: derived from historical bars, no orders, no broker calls.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

PREMARKET_OPEN = dtime(4, 0)
REGULAR_OPEN = dtime(9, 30)
REGULAR_CLOSE = dtime(16, 0)
POSTMARKET_CLOSE = dtime(20, 0)


def _to_et(value):
    """Parse an ISO timestamp (Alpaca returns UTC with Z) into exchange local time."""
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(ET)


def _session_of(moment):
    clock = moment.time()
    if PREMARKET_OPEN <= clock < REGULAR_OPEN:
        return "premarket"
    if REGULAR_OPEN <= clock < REGULAR_CLOSE:
        return "regular"
    if REGULAR_CLOSE <= clock < POSTMARKET_CLOSE:
        return "postmarket"
    return "overnight"


def _extent(bars):
    """(high, low) across a set of bars, or (None, None) when empty."""
    highs = [float(b["high"]) for b in bars if b.get("high") is not None]
    lows = [float(b["low"]) for b in bars if b.get("low") is not None]
    if not highs or not lows:
        return None, None
    return max(highs), min(lows)


def group_by_session(bars):
    """{trading_date: {session: [bars]}} in exchange local time.

    Keyed by the *trading* date, so a post-market bar at 18:00 belongs to the day
    that just closed rather than being orphaned.
    """
    grouped = {}
    for bar in bars or ():
        moment = _to_et(bar.get("time") or bar.get("t"))
        if moment is None:
            continue
        session = _session_of(moment)
        if session == "overnight":
            # 20:00-04:00 is neither session; it belongs to no trading day's levels.
            continue
        day = moment.date()
        grouped.setdefault(day, {}).setdefault(session, []).append(bar)
    return grouped


def _week_start(day):
    """Monday of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def _daily_by_date(daily_bars):
    out = {}
    for bar in daily_bars or ():
        moment = _to_et(bar.get("time") or bar.get("t"))
        if moment is None:
            continue
        out[moment.date()] = bar
    return out


def compute(bars, *, daily_bars=None, as_of=None):
    """Session levels. Minute bars for the extended-hours extremes, daily for prior sessions.

    The two sources are not interchangeable. Pre-market and post-market extremes
    need intraday resolution and extended-hours prints, so only minute bars can
    produce them. Previous-day and previous-week extents are regular-hours
    quantities that a daily bar already reports exactly — and deriving them from
    minute bars silently truncates: a 1000-bar 5-minute window covers about five
    trading days, so "previous week" landed on a partial Friday and reported a
    203/200 range while the stock traded at 223.

    `daily_bars` is therefore strongly preferred. Without it the prior-session
    levels are computed from whatever minute history is present and marked with a
    coverage warning rather than silently trusted.
    """
    grouped = group_by_session(bars)
    daily = _daily_by_date(daily_bars)
    if not grouped and not daily:
        return {"levels": [], "note": "no bars with usable timestamps"}

    today = (_to_et(as_of) or datetime.now(ET)).date() if as_of else datetime.now(ET).date()
    days = sorted(grouped)
    # "Today" is the newest day present, which during a session is the live one and
    # after hours is still the day that just traded.
    current_day = max((d for d in days if d <= today), default=days[-1] if days else today)
    prior_days = [d for d in days if d < current_day]

    levels = []
    warnings = []

    def add(kind, label, price):
        if price is not None:
            levels.append({"kind": kind, "label": label, "price": round(float(price), 4)})

    # ── previous day / previous week ────────────────────────────────────────
    daily_days = sorted(d for d in daily if d < current_day)
    if daily_days:
        prev = daily[daily_days[-1]]
        add("prev_day_high", "PDH", prev.get("high"))
        add("prev_day_low", "PDL", prev.get("low"))

        current_week = _week_start(current_day)
        prior_week_days = [d for d in daily_days if _week_start(d) < current_week]
        if prior_week_days:
            last_week = _week_start(max(prior_week_days))
            week = [daily[d] for d in daily_days if _week_start(d) == last_week]
            high, low = _extent(week)
            add("prev_week_high", "PWH", high)
            add("prev_week_low", "PWL", low)
        else:
            warnings.append("no daily bars from a prior week — previous-week levels omitted")
    elif prior_days:
        warnings.append(
            "prior-session levels derived from minute bars because no daily bars were "
            "supplied; a short minute window truncates the previous week"
        )
        prev = prior_days[-1]
        high, low = _extent(grouped[prev].get("regular", []))
        add("prev_day_high", "PDH", high)
        add("prev_day_low", "PDL", low)

        current_week = _week_start(current_day)
        prior_week_days = [d for d in days if _week_start(d) < current_week]
        if prior_week_days:
            last_week = _week_start(max(prior_week_days))
            week_bars = []
            for day in (d for d in days if _week_start(d) == last_week):
                week_bars.extend(grouped[day].get("regular", []))
            high, low = _extent(week_bars)
            add("prev_week_high", "PWH", high)
            add("prev_week_low", "PWL", low)

    # ── today's pre-market ──────────────────────────────────────────────────
    high, low = _extent(grouped.get(current_day, {}).get("premarket", []))
    add("premarket_high", "PMH", high)
    add("premarket_low", "PML", low)

    # ── most recent post-market ─────────────────────────────────────────────
    # Usually last night's, which is what sets up the current session; if today has
    # already closed and printed post-market trade, that is the newer one.
    post_source = None
    for day in reversed(days):
        if grouped[day].get("postmarket"):
            post_source = day
            break
    if post_source is not None:
        high, low = _extent(grouped[post_source]["postmarket"])
        add("postmarket_high", "PostH", high)
        add("postmarket_low", "PostL", low)

    return {
        "levels": levels,
        "session_dates": {
            "current": current_day.isoformat(),
            "previous_day": (daily_days[-1].isoformat() if daily_days
                             else prior_days[-1].isoformat() if prior_days else None),
            "postmarket_from": post_source.isoformat() if post_source else None,
        },
        "warnings": warnings,
        "note": "Previous-day and previous-week extents are regular-hours only, from "
                "daily bars. Pre-market and post-market extremes come from minute bars "
                "split in exchange local time (pre 04:00-09:30, post 16:00-20:00 ET).",
    }


def premarket_range_pct(bars, *, as_of=None):
    """Today's pre-market range as a percent of the pre-market low.

    Separated out because it is the gate for the Structural Fib strategy, which
    treats a range at or under 1.5% as a trending day and anything wider as chop.
    Returns None when the pre-market has not traded.
    """
    grouped = group_by_session(bars)
    if not grouped:
        return None
    today = (_to_et(as_of) or datetime.now(ET)).date() if as_of else datetime.now(ET).date()
    days = sorted(grouped)
    current_day = max((d for d in days if d <= today), default=days[-1] if days else None)
    if current_day is None:
        return None
    high, low = _extent(grouped.get(current_day, {}).get("premarket", []))
    if high is None or not low:
        return None
    return (high - low) / low * 100.0
