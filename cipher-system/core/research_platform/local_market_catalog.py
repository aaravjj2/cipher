"""Queryable local catalog for revision-pinned Hugging Face market datasets.

The raw lake remains immutable.  This module only creates local DuckDB views
over approved copies, and records the limited IV/OHLCV overlap explicitly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


IV_JOIN_LIMITATION = (
    "The approved IV panel covers 2019-10-14 through 2023-07-28 only. "
    "Any OHLCV/IV joined backtest must restrict its analysis window to the "
    "verified overlap and must not extrapolate missing IV features."
)


def _literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_market_catalog(
    database_path: str | Path,
    *,
    iv_csv: str | Path,
    ohlcv_parquet_files: Iterable[str | Path],
) -> Path:
    """Create read-only-source DuckDB views for local research queries."""
    import duckdb

    files = [Path(path).resolve() for path in ohlcv_parquet_files]
    if not files:
        raise ValueError("at least one OHLCV parquet file is required")
    iv_csv = Path(iv_csv).resolve()
    if not iv_csv.is_file():
        raise FileNotFoundError(iv_csv)
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    database_path = Path(database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    array_literal = "[" + ", ".join(_literal(path) for path in files) + "]"
    with duckdb.connect(str(database_path)) as db:
        db.execute("create schema if not exists cipher_market")
        db.execute(
            "create or replace view cipher_market.options_iv_sp500 as "
            f"select * from read_csv_auto({_literal(iv_csv)}, header=true)"
        )
        db.execute(
            "create or replace view cipher_market.ohlcv_1m as "
            f"select * from read_parquet({array_literal}, union_by_name=true)"
        )
        db.execute(
            "create or replace table cipher_market.catalog_metadata as "
            "select 'iv_ohlcv_join_limitation' as key, ? as value",
            [IV_JOIN_LIMITATION],
        )
    return database_path
