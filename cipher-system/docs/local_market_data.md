# Local Market Data

The approved Hugging Face imports are stored locally under
`cipher-system/data/external/huggingface/` and registered as immutable objects
in `cipher-system/data/raw_lake/`. `scripts/audit_local_market_data.py` creates
the queryable local DuckDB catalog at `cipher-system/data/market_catalog.duckdb`.
It creates views only and does not rewrite the raw files.

## IV/OHLCV Join Constraint

`data_IV_USA.csv` covers 2019-10-14 through 2023-07-28. Any joined OHLCV/IV
backtest must restrict itself to this verified overlap. Missing IV values must
not be extrapolated or treated as a value available outside that period.

The OHLCV source must be checked for split adjustment and session-density
limitations before it is used for an adjusted-price or volume-sensitive rule.
