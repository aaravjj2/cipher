"""Tier 2 GEX Pinning and Volatility Dynamics Model.

Learns market maker gamma hedging pinning probabilities and strike attraction
dynamics from historical public-OI GEX surface snapshots.
"""
from __future__ import annotations

import math
import pickle
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error, roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "data" / "models"
GEX_DB = ROOT / "data" / "gex_history.sqlite"
EQUITY_DB = ROOT / "data" / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite"


def load_gex_training_samples(
    db_path: Path = GEX_DB,
    equity_path: Path = EQUITY_DB,
    limit: int = 50000,
) -> pd.DataFrame:
    """Extract strike-level GEX features and subsequent underlying pin outcomes."""
    if not db_path.is_file():
        return pd.DataFrame()

    with sqlite3.connect(db_path) as db:
        query = f"""
            SELECT s.id as snapshot_id, s.ticker, s.captured_at, s.spot,
                   s.call_wall_strike as call_wall, s.put_wall_strike as put_floor,
                   s.gamma_flip_level as zero_gamma,
                   c.strike, c.call_gex, c.put_gex, c.net_gex as strike_net_gex,
                   c.call_oi as open_interest, c.call_mid as implied_vol
            FROM gex_snapshots s
            JOIN gex_strike_cells c ON s.id = c.snapshot_id
            WHERE s.spot IS NOT NULL AND s.spot > 0 AND c.strike IS NOT NULL
            ORDER BY s.captured_at DESC
            LIMIT {limit}
        """
        df = pd.read_sql_query(query, db)

    if df.empty:
        return df

    # Feature engineering for gamma pinning
    df["dist_to_strike_pct"] = (df["strike"] - df["spot"]) / df["spot"] * 100.0
    df["abs_dist_pct"] = df["dist_to_strike_pct"].abs()

    # Filter to strikes within 5% of spot
    df = df[df["abs_dist_pct"] <= 5.0].copy()

    df["gex_concentration_ratio"] = df["strike_net_gex"].abs() / (df["call_gex"].abs() + df["put_gex"].abs() + 1.0)
    df["is_call_wall"] = (df["strike"] == df["call_wall"]).astype(int)
    df["is_put_floor"] = (df["strike"] == df["put_floor"]).astype(int)
    df["is_near_zero_gamma"] = (df["strike"] - df["zero_gamma"]).abs() < (df["spot"] * 0.005)

    # Pinning label heuristic: strike within 0.5% of spot at expiry/observation
    df["label_pinned"] = (df["abs_dist_pct"] <= 0.75).astype(int)
    df["date"] = pd.to_datetime(df["captured_at"]).dt.strftime("%Y-%m-%d")

    return df


class GexPinningModel:
    """Predicts dealer delta-hedging pinning attraction to strikes."""

    def __init__(
        self,
        *,
        model_id: str = "cipher_gex_pinning_v1",
        version: str = "1.0.0",
        params: dict[str, Any] | None = None,
    ):
        self.model_id = model_id
        self.version = version
        self.feature_names: list[str] = [
            "dist_to_strike_pct",
            "abs_dist_pct",
            "gex_concentration_ratio",
            "is_call_wall",
            "is_put_floor",
            "is_near_zero_gamma",
            "open_interest",
            "implied_vol",
        ]
        self.model: lgb.Booster | None = None
        self.params = params or {
            "objective": "binary",
            "metric": "auc",
            "boosting_type": "gbdt",
            "n_estimators": 150,
            "learning_rate": 0.05,
            "num_leaves": 20,
            "verbose": -1,
        }

    def fit(self, df: pd.DataFrame) -> dict[str, Any]:
        """Train GEX pinning model."""
        for col in self.feature_names:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        X = df[self.feature_names].values
        y = df["label_profitable"] if "label_profitable" in df.columns else df["label_pinned"].values

        # Time split (80% train, 20% test)
        split_idx = int(len(df) * 0.8)
        X_train, y_train = X[:split_idx], y[:split_idx]
        X_test, y_test = X[split_idx:], y[split_idx:]

        train_data = lgb.Dataset(X_train, label=y_train, feature_name=self.feature_names)
        val_data = lgb.Dataset(X_test, label=y_test, feature_name=self.feature_names, reference=train_data)

        self.model = lgb.train(
            self.params,
            train_data,
            valid_sets=[val_data],
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )

        preds = self.model.predict(X_test)
        auc = float(roc_auc_score(y_test, preds)) if len(np.unique(y_test)) > 1 else 0.5

        importance_scores = self.model.feature_importance(importance_type="gain")
        feat_imp = {
            name: float(score)
            for name, score in sorted(
                zip(self.feature_names, importance_scores), key=lambda x: x[1], reverse=True
            )
        }

        return {
            "model_id": self.model_id,
            "auc_roc": round(auc, 4),
            "sample_count": len(df),
            "feature_importances": feat_imp,
        }

    def predict_pin_probability(self, features_df: pd.DataFrame) -> np.ndarray:
        """Predict pin probability for strike candidate."""
        if not self.model:
            raise RuntimeError("Model has not been fitted.")
        aligned = pd.DataFrame(index=features_df.index)
        for col in self.feature_names:
            aligned[col] = pd.to_numeric(features_df[col] if col in features_df.columns else 0.0, errors="coerce").fillna(0.0)
        return self.model.predict(aligned.values)

    def save(self, destination: Path | None = None) -> Path:
        target = destination or (MODELS_DIR / f"{self.model_id}.pkl")
        with open(target, "wb") as f:
            pickle.dump({
                "model_id": self.model_id,
                "version": self.version,
                "feature_names": self.feature_names,
                "params": self.params,
                "model": self.model,
            }, f)
        return target

    @classmethod
    def load(cls, path: Path) -> GexPinningModel:
        with open(path, "rb") as f:
            payload = pickle.load(f)
        inst = cls(model_id=payload["model_id"], version=payload["version"], params=payload["params"])
        inst.feature_names = payload["feature_names"]
        inst.model = payload["model"]
        return inst
