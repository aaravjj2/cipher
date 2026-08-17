"""Bars for the Structural Fib study: load, resample, split into sessions, resolve a race.

Split out of `structural_fib_lab` to keep that module inside the 500-line limit, and because
this is a genuinely separate concern: nothing here knows what a Fibonacci level is. It is the
data layer plus two pure functions about price paths, shared by the backtest and the forward
recorder so both read bars the same way.

Research-only. Reads a local sqlite mirror of Alpaca SIP bars. No orders, no broker calls.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

PRE_OPEN, REG_OPEN, REG_CLOSE = dtime(4, 0), dtime(9, 30), dtime(16, 0)


# ────────────────────────────────────────────────────────────── bars

@dataclass(slots=True)
class Bar:
    t: datetime  # exchange local time, bucket start
    o: float
    h: float
    l: float
    c: float
    v: float

    @property
    def body_hi(self) -> float:
        return max(self.o, self.c)

    @property
    def body_lo(self) -> float:
        return min(self.o, self.c)


def load_minute_bars(db_path: Path, symbol: str) -> list[Bar]:
    """1-minute bars for one symbol, in exchange local time, ascending."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT timestamp, open, high, low, close, volume FROM bars "
            "WHERE symbol = ? AND timeframe = '1Min' ORDER BY timestamp",
            (symbol,),
        ).fetchall()
    finally:
        con.close()
    out: list[Bar] = []
    for stamp, o, h, l, c, v in rows:
        try:
            moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        out.append(Bar(moment.astimezone(NY), float(o), float(h), float(l), float(c), float(v)))
    return out


def resample_5m(bars: Sequence[Bar]) -> list[Bar]:
    """1-minute -> 5-minute, aligned to :00/:05/... in exchange local time.

    Aligning in local time rather than UTC matters: the regular session opens at
    09:30 ET, so a UTC-aligned grid would straddle the open and put pre-market and
    regular trade in the same bar.
    """
    buckets: dict[datetime, list[Bar]] = defaultdict(list)
    for bar in bars:
        key = bar.t.replace(minute=(bar.t.minute // 5) * 5, second=0, microsecond=0)
        buckets[key].append(bar)
    out: list[Bar] = []
    for key in sorted(buckets):
        group = buckets[key]
        out.append(Bar(
            t=key,
            o=group[0].o,
            h=max(b.h for b in group),
            l=min(b.l for b in group),
            c=group[-1].c,
            v=sum(b.v for b in group),
        ))
    return out


def split_sessions(bars: Iterable[Bar]) -> dict[date, dict[str, list[Bar]]]:
    """{trading_date: {"pre": [...], "reg": [...]}}, exchange local time."""
    days: dict[date, dict[str, list[Bar]]] = defaultdict(lambda: {"pre": [], "reg": []})
    for bar in bars:
        clock = bar.t.time()
        if PRE_OPEN <= clock < REG_OPEN:
            days[bar.t.date()]["pre"].append(bar)
        elif REG_OPEN <= clock < REG_CLOSE:
            days[bar.t.date()]["reg"].append(bar)
    return days


def _touched(level: float, bars: Sequence[Bar], direction: str) -> bool:
    for bar in bars:
        if direction == "long" and bar.h >= level:
            return True
        if direction == "short" and bar.l <= level:
            return True
    return False


def _race(entry: float, target: float, stop: float,
          bars: Sequence[Bar], direction: str) -> tuple[str, float]:
    """First touch wins; a bar spanning both is scored as the stop.

    Five-minute bars carry no intrabar sequence, so a bar that reaches target and
    stop is genuinely ambiguous. Scoring it as the stop is the reading that cannot
    flatter the strategy.
    """
    sign = 1.0 if direction == "long" else -1.0
    for bar in bars:
        hit_stop = bar.l <= stop if direction == "long" else bar.h >= stop
        hit_target = bar.h >= target if direction == "long" else bar.l <= target
        if hit_stop:
            return "stop", (stop - entry) * sign / entry * 100.0
        if hit_target:
            return "target", (target - entry) * sign / entry * 100.0
    return "close", (bars[-1].c - entry) * sign / entry * 100.0
