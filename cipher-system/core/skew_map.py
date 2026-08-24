"""Read-only options-skew research surface.

Skew is positioning evidence, not a forecast.  The map joins Cipher's stored
OPRA-derived 25-delta skew observation to underlying daily bars no later than
that observation.  Missing or unaligned inputs stay missing.
"""
from __future__ import annotations

from datetime import date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from typing import Callable

try:
    from .option_history import DEFAULT_DB
except ImportError:  # direct `python core/app.py` execution
    from option_history import DEFAULT_DB


DEFAULT_UNIVERSE = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "MU", "AVGO")
SECTORS = {
    "SPY": "Index", "QQQ": "Index", "AAPL": "Technology", "MSFT": "Technology",
    "NVDA": "Semiconductors", "AMZN": "Consumer", "GOOGL": "Communication",
    "META": "Communication", "TSLA": "Consumer", "AMD": "Semiconductors",
    "MU": "Semiconductors", "AVGO": "Semiconductors",
}


def _latest_rows(db_path: Path, universe: tuple[str, ...]) -> list[dict]:
    if not db_path.exists():
        return []
    placeholders = ",".join("?" for _ in universe)
    query = f"""
        select s.ticker,s.observed_at,s.market_session_date,s.front_expiry,
               s.front_atm_iv,s.front_skew_25d,s.iv_coverage,s.quote_coverage,
               s.median_spread_pct,s.contract_count,s.feed
        from snapshots s
        join (
          select ticker,max(observed_at) observed_at from snapshots
          where ticker in ({placeholders}) group by ticker
        ) latest on latest.ticker=s.ticker and latest.observed_at=s.observed_at
        order by s.ticker
    """
    with sqlite3.connect(db_path) as db:
        rows = db.execute(query, universe).fetchall()
        sessions = dict(db.execute(
            f"select ticker,count(distinct market_session_date) from snapshots where ticker in ({placeholders}) group by ticker",
            universe,
        ).fetchall())
    return [{
        "ticker": row[0], "observed_at": row[1], "market_session_date": row[2],
        "front_expiry": row[3], "atm_iv": row[4], "raw_skew": row[5],
        "iv_coverage": row[6], "quote_coverage": row[7], "median_spread_pct": row[8],
        "contract_count": row[9], "feed": row[10], "sessions": sessions.get(row[0], 0),
    } for row in rows]


def _one_month_return(bar_payload: dict, as_of: str | None) -> tuple[float | None, str | None]:
    rows = []
    for row in bar_payload.get("bars", []):
        stamp, close = str(row.get("time") or "")[:10], row.get("close")
        if stamp and close is not None and (not as_of or stamp <= as_of):
            rows.append((stamp, float(close)))
    if len(rows) < 2:
        return None, rows[-1][0] if rows else None
    rows = rows[-22:]
    first, last = rows[0][1], rows[-1][1]
    return (round((last / first - 1.0) * 100.0, 6) if first else None), rows[-1][0]


def build_skew_map(
    bar_loader: Callable[[str, str, int], dict],
    *,
    db_path: Path = DEFAULT_DB,
    universe: tuple[str, ...] = DEFAULT_UNIVERSE,
) -> dict:
    rows = _latest_rows(db_path, universe)

    def enrich(row: dict) -> dict:
        price_return, price_as_of, price_error = None, None, None
        try:
            price_return, price_as_of = _one_month_return(
                bar_loader(row["ticker"], "1d", 35), row["market_session_date"]
            )
        except Exception as exc:  # provider gaps are a visible data state
            price_error = f"{type(exc).__name__}: {str(exc)[:120]}"
        raw = row["raw_skew"]
        atm = row["atm_iv"]
        normalized = raw / atm if raw is not None and atm not in (None, 0) else None
        if raw is None or price_return is None:
            quadrant = "insufficient_data"
        elif price_return < 0 and raw < 0:
            quadrant = "contrarian_bid"
        elif price_return >= 0 and raw < 0:
            quadrant = "chase"
        elif price_return >= 0 and raw >= 0:
            quadrant = "hedged_rally"
        else:
            quadrant = "fear"
        quality = "provisional" if row["sessions"] < 20 else "usable"
        quality_reasons = []
        if row["sessions"] < 20:
            quality_reasons.append(f"only {row['sessions']} stored sessions")
        if (row["iv_coverage"] or 0) < .7 or (row["quote_coverage"] or 0) < .5:
            quality_reasons.append("thin IV or quote coverage")
            if row["sessions"] >= 20:
                quality = "limited"
        if raw is not None and abs(raw) > 1.0:
            quality_reasons.append("raw skew exceeds 100 volatility points")
            if row["sessions"] >= 20:
                quality = "suspect"
        if price_return is None:
            quality_reasons.append("aligned one-month return unavailable")
            if row["sessions"] >= 20:
                quality = "limited"
        return {
            **row, "sector": SECTORS.get(row["ticker"], "Other"),
            "return_1m_pct": price_return, "price_as_of": price_as_of,
            "normalized_skew": normalized, "quadrant": quadrant,
            "quality": quality, "quality_reasons": quality_reasons,
            "price_error": price_error,
        }

    # Underlying bar requests are independent and the outer API is already a
    # threaded read-only server. Bound concurrency to avoid turning one map view
    # into an unbounded provider fan-out.
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(rows)))) as pool:
        points = list(pool.map(enrich, rows))
    latest = max((p["observed_at"] for p in points), default=None)
    return {
        "generated_from": "stored_opra_surface_plus_aligned_underlying_bars",
        "as_of": latest,
        "formula": "raw_skew = 25-delta put IV - 25-delta call IV; normalized_skew = raw_skew / ATM IV",
        "points": points,
        "read_only": True,
        "execution_capability": False,
        "caveat": (
            "Skew describes relative option demand, not direction or an entry signal. "
            "Compare raw volatility-point skew across unlike sectors and normalized skew within similar names. "
            "Earnings inside the selected expiry can dominate sentiment; verify the event calendar and chain quality."
        ),
    }
