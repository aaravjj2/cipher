"""Qlib-compatible daily adapter for the price-only research track.

The adapter deliberately emits only OHLC and instrument/time fields.  Volume is
never selected, exported, or available to the factor screen in this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd


PRICE_COLUMNS = ("datetime", "instrument", "open", "high", "low", "close")


def load_price_only_daily(paths: Iterable[str | Path], symbols: Iterable[str]) -> list[dict]:
    """Aggregate normalized minute bars into a deterministic daily panel."""

    files = [str(Path(path)) for path in paths]
    if not files:
        raise ValueError("at least one normalized parquet path is required")
    requested = tuple(sorted({str(symbol).upper() for symbol in symbols}))
    if not requested:
        raise ValueError("at least one symbol is required")
    placeholders = ",".join("?" for _ in requested)
    query = f"""
        select
            cast(date_trunc('day', cast(timestamp as timestamp)) as date) as datetime,
            upper(ticker) as instrument,
            first(open order by cast(timestamp as timestamp)) as open,
            max(high) as high,
            min(low) as low,
            last(close order by cast(timestamp as timestamp)) as close
        from read_parquet(?, hive_partitioning=true)
        where upper(ticker) in ({placeholders})
          and cast(cast(timestamp as timestamp) as time) >= time '09:30'
          and cast(cast(timestamp as timestamp) as time) <= time '16:00'
        group by 1, 2
        order by 2, 1
    """
    # DuckDB's list parameter is accepted by read_parquet while symbols remain
    # bound parameters, keeping the adapter free of string-built values.
    with duckdb.connect() as connection:
        rows = connection.execute(query, [files, *requested]).fetchall()
    return [dict(zip(PRICE_COLUMNS, row)) for row in rows]


def write_qlib_panel(rows: list[dict], output_path: str | Path) -> Path:
    """Write Qlib's tabular daily shape without introducing volume."""

    if not rows:
        raise ValueError("cannot write an empty Qlib panel")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.register("price_rows", pd.DataFrame(rows, columns=PRICE_COLUMNS))
        connection.execute(
            "copy (select datetime, instrument, open, high, low, close from price_rows "
            "order by instrument, datetime) to ? (format parquet, compression zstd)",
            [str(target)],
        )
    return target
