"""Point-in-time feature extraction for quantitative options alpha models.

Extracts leakage-free historical features including volatility dynamics,
market momentum, structural levels, execution conditions, and GEX context.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EQUITY_DB = ROOT / "data" / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite"
HIST_BARS_DB = ROOT / "data" / "historical_bars.sqlite"
GEX_DB = ROOT / "data" / "gex_history.sqlite"


def load_daily_bars(db_path: Path = EQUITY_DB) -> pd.DataFrame:
    """Load and aggregate daily bars per symbol for underlying indicators."""
    if not db_path.is_file():
        if HIST_BARS_DB.is_file():
            db_path = HIST_BARS_DB
        else:
            return pd.DataFrame()

    with sqlite3.connect(db_path) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        table_name = "bars" if "bars" in tables else "historical_bars"
        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume, vwap
            FROM {table_name}
            ORDER BY symbol, timestamp
        """
        df = pd.read_sql_query(query, db)

    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")

    # Aggregate to daily if intraday bars
    daily = df.groupby(["symbol", "date"]).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "vwap": "mean",
    }).reset_index()

    # Compute underlying technical and volatility features
    daily_list = []
    for sym, group in daily.groupby("symbol"):
        g = group.sort_values("date").copy()
        g["ret_1d"] = g["close"].pct_change(1)
        g["ret_5d"] = g["close"].pct_change(5)
        g["ret_10d"] = g["close"].pct_change(10)
        g["ret_20d"] = g["close"].pct_change(20)

        # Realized Volatility (20-day annualized)
        g["realized_vol_20d"] = g["ret_1d"].rolling(20).std() * math.sqrt(252)

        # Average True Range (ATR 14)
        tr1 = g["high"] - g["low"]
        tr2 = (g["high"] - g["close"].shift(1)).abs()
        tr3 = (g["low"] - g["close"].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        g["atr_14"] = tr.rolling(14).mean()
        g["atr_pct"] = g["atr_14"] / g["close"]

        # Volume momentum
        g["vol_ma20"] = g["volume"].rolling(20).mean()
        g["rvol_20"] = g["volume"] / g["vol_ma20"].replace(0, np.nan)

        daily_list.append(g)

    if daily_list:
        return pd.concat(daily_list, ignore_index=True)
    return pd.DataFrame()


def build_options_dataset(
    outcomes_csv: Path,
    daily_bars: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Build feature matrix and labels from daily option outcomes."""
    df = pd.read_csv(outcomes_csv)
    df = df[df["status"] == "executed"].copy()

    # Labels
    df["label_profitable"] = (df["pnl_dollars"] > 0).astype(int)
    df["label_return_on_risk"] = df["return_on_risk_pct"].fillna(0.0)

    # Join daily underlying features if available
    if daily_bars is not None and not daily_bars.empty:
        df = df.merge(
            daily_bars,
            left_on=["symbol", "day"],
            right_on=["symbol", "date"],
            how="left",
        )

    # Moneyness and contract features
    df["long_strike"] = pd.to_numeric(df["long_strike"], errors="coerce").fillna(0.0)
    df["short_strike"] = pd.to_numeric(df["short_strike"], errors="coerce").fillna(0.0)
    df["entry_debit"] = pd.to_numeric(df["entry_debit"], errors="coerce").fillna(0.0)
    df["risk_capital_dollars"] = pd.to_numeric(df["risk_capital_dollars"], errors="coerce").fillna(0.0)
    df["fees_dollars"] = pd.to_numeric(df["fees_dollars"], errors="coerce").fillna(0.0)

    if "close" in df.columns:
        close_price = df["close"].fillna(df["long_strike"]).replace(0, 1.0)
    else:
        close_price = df["long_strike"].replace(0, 1.0)
    df["moneyness_pct"] = (df["long_strike"] - close_price) / close_price * 100.0
    df["fee_to_risk_ratio"] = df["fees_dollars"] / df["risk_capital_dollars"].replace(0, 1.0)

    # Calendar features
    dates = pd.to_datetime(df["day"])
    dow = dates.dt.dayofweek
    df["dow_sin"] = np.sin(2 * np.pi * dow / 5.0)
    df["dow_cos"] = np.cos(2 * np.pi * dow / 5.0)
    month = dates.dt.month
    df["month_sin"] = np.sin(2 * np.pi * month / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * month / 12.0)

    # Categorical one-hot features
    cat_cols = ["symbol", "actual_side", "option_type", "structure", "execution_model", "bucket"]
    df_encoded = pd.get_dummies(df, columns=cat_cols, drop_first=False)

    # Selected numeric and encoded feature list
    numeric_features = [
        "moneyness_pct",
        "fee_to_risk_ratio",
        "entry_debit",
        "risk_capital_dollars",
        "dow_sin",
        "dow_cos",
        "month_sin",
        "month_cos",
    ]
    if "ret_1d" in df_encoded.columns:
        numeric_features.extend([
            "ret_1d",
            "ret_5d",
            "ret_10d",
            "ret_20d",
            "realized_vol_20d",
            "atr_pct",
            "rvol_20",
        ])

    one_hot_cols = [c for c in df_encoded.columns if any(c.startswith(f"{col}_") for col in cat_cols)]
    all_feature_cols = numeric_features + one_hot_cols

    # Ensure no NaNs in feature cols
    for col in all_feature_cols:
        if col in df_encoded.columns:
            df_encoded[col] = df_encoded[col].fillna(0.0).astype(float)

    return df_encoded, all_feature_cols
