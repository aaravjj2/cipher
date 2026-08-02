#!/usr/bin/env python3
"""Generate data-backed coverage, split, and volume checks for local market files."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[0]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.local_market_catalog import build_market_catalog  # noqa: E402


OHLCV_ROOT = (
    ROOT / "data" / "external" / "huggingface" / "ohlcv_1m"
    / "776328445b7ac6e7815ef3a483e9c8ded1eb6d56" / "data"
)
IV_CSV = (
    ROOT / "data" / "external" / "huggingface" / "options_iv_sp500"
    / "34f269b94a2680054d327a8f3c303facc7c7ed3f" / "data_IV_USA.csv"
)
CATALOG_PATH = ROOT / "data" / "market_catalog.duckdb"


def _query_month(path: Path) -> dict:
    import duckdb

    with duckdb.connect() as db:
        rows, tickers, first_at, last_at, days = db.execute(
            """
            select count(*), count(distinct ticker), min(timestamp), max(timestamp),
                   count(distinct cast(timestamp as date))
            from read_parquet(?)
            """,
            [str(path)],
        ).fetchone()
        regular = db.execute(
            """
            select count(*) filter (where bars = 391), count(*), min(bars), max(bars)
            from (
              select ticker, cast(timestamp as date) as day, count(*) as bars
              from read_parquet(?)
              where cast(timestamp at time zone 'America/New_York' as time)
                    between time '09:30:00' and time '16:00:00'
              group by 1, 2
            )
            """,
            [str(path)],
        ).fetchone()
    return {
        "file": path.name,
        "rows": rows,
        "tickers": tickers,
        "first_timestamp": first_at.isoformat(),
        "last_timestamp": last_at.isoformat(),
        "calendar_days": days,
        "regular_session_ticker_days_at_391_bars": regular[0],
        "regular_session_ticker_days": regular[1],
        "regular_session_min_bars": regular[2],
        "regular_session_max_bars": regular[3],
    }


def _split_and_volume(aapl_month: Path) -> dict:
    import duckdb
    import yfinance as yf

    with duckdb.connect() as db:
        split_rows = db.execute(
            """
            select cast(timestamp as date) as day,
                   first(close order by timestamp) as first_close,
                   last(close order by timestamp) as last_close,
                   count(*) as bars
            from read_parquet(?)
            where ticker='AAPL' and cast(timestamp as date) in (date '2020-08-28', date '2020-08-31')
            group by 1 order by 1
            """,
            [str(aapl_month)],
        ).fetchall()
        local = db.execute(
            """
            select cast(timestamp as date) as day, sum(volume) as volume
            from read_parquet(?)
            where ticker='AAPL'
              and cast(timestamp at time zone 'America/New_York' as time)
                    between time '09:30:00' and time '16:00:00'
            group by 1 order by 1
            """,
            [str(aapl_month)],
        ).fetchall()
    history = yf.Ticker("AAPL").history(
        start="2020-08-01", end="2020-09-02", auto_adjust=False, actions=True
    )
    remote = {index.date().isoformat(): int(row["Volume"]) for index, row in history.iterrows()}
    differences = [abs(float(volume) - remote[str(day)]) for day, volume in local if str(day) in remote]
    splits = history["Stock Splits"] if "Stock Splits" in history else []
    return {
        "split_close_rows": [
            {"day": str(day), "first_close": first, "last_close": last, "bars": bars}
            for day, first, last, bars in split_rows
        ],
        "regular_session_volume_days_compared": len(differences),
        "volume_abs_difference_median": sorted(differences)[len(differences) // 2] if differences else None,
        "volume_abs_difference_max": max(differences) if differences else None,
        "yfinance_split_events": {
            str(index.date()): float(value)
            for index, value in splits.items()
            if float(value) != 0.0
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", action="append", default=[])
    args = parser.parse_args(argv)
    months = args.month or [path.name for path in sorted(OHLCV_ROOT.glob("ohlcv_*.parquet"))]
    paths = [OHLCV_ROOT / month for month in months]
    catalog = build_market_catalog(CATALOG_PATH, iv_csv=IV_CSV, ohlcv_parquet_files=paths)
    import duckdb
    with duckdb.connect() as db:
        iv_columns = [row[0] for row in db.execute("describe select * from read_csv_auto(?, header=true)", [str(IV_CSV)]).fetchall()]
        iv_range = db.execute("select min(date), max(date), count(*) from read_csv_auto(?, header=true)", [str(IV_CSV)]).fetchone()
    payload = {
        "catalog": str(catalog),
        "months": [_query_month(path) for path in paths],
        "aapl_split_and_volume": _split_and_volume(OHLCV_ROOT / "ohlcv_2020-08.parquet"),
        "iv": {"columns": iv_columns, "min_date": iv_range[0], "max_date": iv_range[1], "rows": iv_range[2]},
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
