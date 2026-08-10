"""Measure execution cost from captured quotes instead of assuming it.

`backtest_engine.DEFAULT_COST_BPS` was a hardcoded 2.0 basis points per side, and
by this repository's own findings it is the number that decides every verdict:
`docs/backtest-findings.md` records that the one result which replicated out of
sample is "smaller than the difference between a 2bp and a 3bp execution
assumption". A verdict that turns on a guess is not a verdict.

`data/tradier_stream.sqlite` holds tens of millions of captured quote events with
`bid` and `ask` columns, so the guess can be replaced with a measurement.

Two profiles, and the distinction matters more than it looks:

  equity — half-spread as basis points of the underlying price. This is what
    `backtest_engine` needs, because `_simulate` trades equity bars. It is the
    number that decides the current findings.
  option — half-spread as a percentage of the option premium. Options are quoted
    in dollars of premium, so expressing their spread in basis points of the
    underlying's notional would be meaningless. Kept separate, and not
    interchangeable with the equity figure.

Percentiles come from a histogram aggregated in SQL rather than from loading the
rows, because the corpus does not fit comfortably in memory and does not need to:
bucketing to a fixed resolution and reading the percentile off the cumulative
counts is exact to that resolution.

The database is opened read-only via a `mode=ro` URI. It is irreplaceable — the
quotes cannot be re-fetched from any vendor at a later date — so this module never
writes, never creates an index, and never runs VACUUM.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "tradier_stream.sqlite"
DEFAULT_OUT = ROOT / "data" / "execution_costs" / "spread_profile.json"

# Histogram resolution. 0.05bp on the equity side resolves a sub-basis-point
# median, which is the range the liquid names actually occupy; a coarser bucket
# would round the answer to the assumption being tested.
EQUITY_BUCKET_BPS = 0.05
# Option spreads are a percentage of premium and run orders of magnitude wider,
# so they get their own, coarser resolution.
OPTION_BUCKET_PCT = 0.25

# Regular US session in UTC. The capture window is July-August, so ET is EDT
# (UTC-4) throughout; a capture spanning a DST change would need a real timezone
# conversion here rather than a fixed offset.
RTH_START_UTC_HOUR = 13   # 09:30 ET
RTH_END_UTC_HOUR = 20     # 16:00 ET

# A cell built from a handful of quotes is noise wearing a number. Cells below
# this are still written, but carry `sufficient: false` so a consumer cannot use
# one by accident.
MIN_SAMPLES_FOR_USE = 1000
# Below this corpus-wide event count, a capture date is a ramp-up/partial day,
# not evidence for a full regular session.
MIN_EVENTS_FOR_FULL_CAPTURE_DAY = 1_000_000


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open strictly read-only. The corpus cannot be re-created if damaged."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute("pragma query_only=on")
    # This is a corpus scan, not a point lookup. SQLite's default tiny cache was
    # observed issuing one 4 KiB read syscall per page against the 47 GB file.
    # mmap lets the kernel perform readahead; the larger private cache and
    # in-memory temp store keep the small grouped histogram off the data disk.
    # These pragmas are connection-local and do not modify the source database.
    conn.execute("pragma mmap_size=8589934592")
    conn.execute("pragma cache_size=-262144")
    conn.execute("pragma temp_store=memory")
    return conn


def _percentile_from_histogram(buckets: dict[int, int], width: float, pct: float) -> float:
    """Read a percentile off cumulative bucket counts.

    Exact to `width`. Returns the midpoint of the bucket the percentile falls in,
    rather than its edge, so the value is not biased low by construction.
    """
    total = sum(buckets.values())
    if not total:
        return 0.0
    target = total * pct
    seen = 0
    for key in sorted(buckets):
        seen += buckets[key]
        if seen >= target:
            return round((key + 0.5) * width, 4)
    return round((max(buckets) + 0.5) * width, 4)


def _summarise(buckets: dict[int, int], width: float) -> dict:
    total = sum(buckets.values())
    return {
        "samples": total,
        "p25": _percentile_from_histogram(buckets, width, 0.25),
        "median": _percentile_from_histogram(buckets, width, 0.50),
        "p75": _percentile_from_histogram(buckets, width, 0.75),
        "p95": _percentile_from_histogram(buckets, width, 0.95),
        "sufficient": total >= MIN_SAMPLES_FOR_USE,
    }


def _capture_window(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "select substr(captured_at,1,10), min(captured_at), max(captured_at), count(*) "
        "from tradier_stream_events group by 1 order by 1"
    ).fetchall()
    if not rows:
        return {
            "first_event": None,
            "last_event": None,
            "distinct_days": 0,
            "daily_event_counts": {},
            "sparse_days": {},
            "missing_weekdays": [],
        }

    counts = {str(row[0]): int(row[3]) for row in rows}
    first_day = date.fromisoformat(str(rows[0][0]))
    last_day = date.fromisoformat(str(rows[-1][0]))
    observed = set(counts)
    missing_weekdays = []
    cursor = first_day
    while cursor <= last_day:
        if cursor.weekday() < 5 and cursor.isoformat() not in observed:
            missing_weekdays.append(cursor.isoformat())
        cursor += timedelta(days=1)

    return {
        "first_event": rows[0][1],
        "last_event": rows[-1][2],
        "distinct_days": len(rows),
        "daily_event_counts": counts,
        "sparse_days": {
            day: count
            for day, count in counts.items()
            if count < MIN_EVENTS_FOR_FULL_CAPTURE_DAY
        },
        "missing_weekdays": missing_weekdays,
    }


def build_equity_profile(conn: sqlite3.Connection, *, rth_only: bool = True) -> dict:
    """Half-spread in basis points of price, per underlying symbol.

    This is the figure `backtest_engine` charges, because it trades equity bars.
    """
    hour = "cast(substr(captured_at,12,2) as integer)"
    where = [
        "asset_class='underlying'", "event_type='quote'",
        "bid>0", "ask>0", "ask>=bid",
    ]
    if rth_only:
        where.append(f"{hour} >= {RTH_START_UTC_HOUR}")
        where.append(f"{hour} < {RTH_END_UTC_HOUR}")

    sql = f"""
        select symbol,
               cast((ask-bid)/2.0/((ask+bid)/2.0)*10000.0/{EQUITY_BUCKET_BPS} as integer) bucket,
               count(*) n
        -- Most rows are quotes. Following the event_type index back to the table
        -- turns this corpus-wide aggregation into tens of millions of random
        -- lookups. A sequential scan is exact, read-only, and materially faster.
        from tradier_stream_events not indexed
        where {' and '.join(where)}
        group by 1, 2
    """
    per_symbol: dict[str, dict[int, int]] = {}
    for symbol, bucket, n in conn.execute(sql):
        per_symbol.setdefault(symbol, {})[bucket] = n

    cells = {}
    for symbol, buckets in per_symbol.items():
        summary = _summarise(buckets, EQUITY_BUCKET_BPS)
        # Zero-width quotes are real on the tightest ETFs but also what a stale or
        # locked book looks like, so the share is reported rather than filtered:
        # a cell that is mostly zeros should be distrusted by the reader.
        summary["zero_spread_share"] = round(buckets.get(0, 0) / max(summary["samples"], 1), 4)
        cells[symbol] = summary
    return cells


def build_option_profile(conn: sqlite3.Connection, *, rth_only: bool = True) -> dict:
    """Half-spread as a percentage of premium, per (underlying, DTE bucket).

    Not comparable to the equity figure and never substitutable for it. Moneyness
    is deliberately omitted: it needs the underlying's spot at quote time, and
    joining 8M option quotes to a spot timeline is a materially larger job than
    the question this artifact currently answers.
    """
    hour = "cast(substr(captured_at,12,2) as integer)"
    where = [
        "asset_class='option'", "event_type='quote'",
        "bid>0", "ask>0", "ask>=bid",
        "underlying is not null", "option_expiration is not null",
    ]
    if rth_only:
        where.append(f"{hour} >= {RTH_START_UTC_HOUR}")
        where.append(f"{hour} < {RTH_END_UTC_HOUR}")

    sql = f"""
        select underlying,
               cast(julianday(option_expiration) - julianday(substr(captured_at,1,10)) as integer) dte,
               cast((ask-bid)/2.0/((ask+bid)/2.0)*100.0/{OPTION_BUCKET_PCT} as integer) bucket,
               count(*) n
        -- See build_equity_profile: quote selectivity is too low for the
        -- event_type index, so force bounded sequential I/O.
        from tradier_stream_events not indexed
        where {' and '.join(where)}
        group by 1, 2, 3
    """
    grouped: dict[tuple[str, str], dict[int, int]] = {}
    for underlying, dte, bucket, n in conn.execute(sql):
        if dte is None or dte < 0:
            continue
        band = "0dte" if dte == 0 else "1-7" if dte <= 7 else "8-30" if dte <= 30 else "31+"
        grouped.setdefault((underlying, band), {})[bucket] = (
            grouped.get((underlying, band), {}).get(bucket, 0) + n
        )

    cells = {}
    for (underlying, band), buckets in grouped.items():
        cells[f"{underlying}|{band}"] = _summarise(buckets, OPTION_BUCKET_PCT)
    return cells


def build_profiles(conn: sqlite3.Connection, *, rth_only: bool = True) -> tuple[dict, dict]:
    """Build equity and option profiles in one corpus traversal.

    The source table is dominated by a large raw JSON column. Scanning it once
    and grouping both asset classes with their own formulas is exactly
    equivalent to the two specialized queries above, but avoids reading the
    47 GB table twice.
    """
    hour = "cast(substr(captured_at,12,2) as integer)"
    where = [
        "event_type='quote'",
        "bid>0",
        "ask>0",
        "ask>=bid",
        "(asset_class='underlying' or "
        "(asset_class='option' and underlying is not null "
        "and option_expiration is not null))",
    ]
    if rth_only:
        where.append(f"{hour} >= {RTH_START_UTC_HOUR}")
        where.append(f"{hour} < {RTH_END_UTC_HOUR}")

    sql = f"""
        select asset_class,
               case when asset_class='underlying' then symbol else underlying end profile_key,
               case when asset_class='option'
                    then cast(julianday(option_expiration) -
                              julianday(substr(captured_at,1,10)) as integer)
                    else null end dte,
               case when asset_class='underlying'
                    then cast((ask-bid)/2.0/((ask+bid)/2.0)*10000.0/
                              {EQUITY_BUCKET_BPS} as integer)
                    else cast((ask-bid)/2.0/((ask+bid)/2.0)*100.0/
                              {OPTION_BUCKET_PCT} as integer)
                    end bucket,
               count(*) n
        from tradier_stream_events not indexed
        where {' and '.join(where)}
        group by 1, 2, 3, 4
    """
    equity_buckets: dict[str, dict[int, int]] = {}
    option_buckets: dict[tuple[str, str], dict[int, int]] = {}
    for asset_class, key, dte, bucket, n in conn.execute(sql):
        if not key:
            continue
        if asset_class == "underlying":
            equity_buckets.setdefault(key, {})[bucket] = n
            continue
        if dte is None or dte < 0:
            continue
        band = "0dte" if dte == 0 else "1-7" if dte <= 7 else "8-30" if dte <= 30 else "31+"
        grouped = option_buckets.setdefault((key, band), {})
        grouped[bucket] = grouped.get(bucket, 0) + n

    equity = {}
    for symbol, buckets in equity_buckets.items():
        summary = _summarise(buckets, EQUITY_BUCKET_BPS)
        summary["zero_spread_share"] = round(
            buckets.get(0, 0) / max(summary["samples"], 1),
            4,
        )
        equity[symbol] = summary

    option = {
        f"{underlying}|{band}": _summarise(buckets, OPTION_BUCKET_PCT)
        for (underlying, band), buckets in option_buckets.items()
    }
    return equity, option


def build_profile(db_path: Path = DEFAULT_DB, *, rth_only: bool = True) -> dict:
    with _connect(db_path) as conn:
        window = _capture_window(conn)
        equity, option = build_profiles(conn, rth_only=rth_only)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_db": str(db_path),
        "capture_window": window,
        "regular_hours_only": rth_only,
        "equity_half_spread_bps": equity,
        "option_half_spread_pct_of_premium": option,
        "cells": len(equity) + len(option),
        "caveat": (
            "Quoted half-spread from one vendor's consolidated feed over "
            f"{window['distinct_days']} capture days ({window['first_event']} to "
            f"{window['last_event']}). It bounds the modelled execution assumption; "
            "it is not a cost model for historical periods outside this window, and "
            "it excludes commissions and market impact. A backtest spanning years "
            "cannot be costed from it — it can only be told whether its assumption "
            "is optimistic against currently observable spreads. Sparse capture "
            "days and missing weekdays are reported explicitly; they are collector "
            "coverage gaps, not market closures."
        ),
    }


def load_profile(path: Path = DEFAULT_OUT) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None


def equity_half_spread_bps(symbol: str, *, profile: dict | None,
                           fallback: float) -> tuple[float, str]:
    """Measured half-spread for `symbol`, else `fallback`.

    Returns the value and its provenance, so a caller can record which of its
    numbers were measured and which were assumed rather than silently blending
    the two.

    `profile` is required and `None` means exactly that — no profile. An earlier
    version fell back to `load_profile()` when it was omitted, which made the same
    call return a measured value or an assumed one depending on whether a file
    happened to exist on disk. A cost lookup that changes answer with the
    filesystem is not something a backtest can be built on, so loading is now the
    caller's explicit decision.
    """
    if not profile:
        return fallback, "assumed:no-profile"
    cell = (profile.get("equity_half_spread_bps") or {}).get(symbol.upper())
    if not cell:
        return fallback, "assumed:symbol-not-captured"
    if not cell.get("sufficient"):
        return fallback, f"assumed:insufficient-samples({cell.get('samples', 0)})"
    return float(cell["median"]), "measured:median"
