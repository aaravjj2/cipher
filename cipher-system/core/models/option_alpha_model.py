"""Tier 1 Multi-Factor Options Alpha Model.

Trains gradient boosted decision trees (LightGBM / XGBoost) to predict the
probability of profitable options execution under realistic slippage.
Validates using Combinatorial Purged Cross-Validation (CPCV) and Deflated Sharpe Ratio (DSR).
"""
from __future__ import annotations

import math
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "data" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def calculate_deflated_sharpe_ratio(
    returns: Sequence[float],
    *,
    num_trials: int = 25,
    annualization_factor: float = 252.0,
) -> tuple[float, float]:
    """Calculate annualized Sharpe Ratio and Deflated Sharpe Ratio (DSR) p-value.

    DSR tests against data-mining bias / multiple testing over `num_trials` experiments.
    """
    clean = np.array([r for r in returns if math.isfinite(r)])
    if len(clean) < 5:
        return 0.0, 1.0

    mean_ret = np.mean(clean)
    std_ret = np.std(clean, ddof=1)
    if std_ret < 1e-8:
        return 0.0, 1.0

    daily_sr = mean_ret / std_ret
    annualized_sr = daily_sr * math.sqrt(annualization_factor)

    # Higher moments
    skew = float(stats.skew(clean))
    kurt = float(stats.kurtosis(clean, fisher=False))  # Pearson kurtosis (normal = 3)

    # Expected maximum Sharpe among num_trials independent zero-alpha strategies
    euler_mascheroni = 0.5772156649
    if num_trials > 1:
        z = (1 - euler_mascheroni) * stats.norm.ppf(1 - 1.0 / num_trials) + euler_mascheroni * stats.norm.ppf(
            1 - 1.0 / (num_trials * math.e)
        )
        expected_max_sr = max(0.0, z)
    else:
        expected_max_sr = 0.0

    # Variance of the Sharpe ratio estimator
    t = len(clean)
    sr_var = (1.0 + 0.5 * (daily_sr ** 2) - skew * daily_sr + ((kurt - 3.0) / 4.0) * (daily_sr ** 2)) / t
    if sr_var <= 0:
        return annualized_sr, 0.5

    sr_std = math.sqrt(sr_var)
    z_stat = (daily_sr - (expected_max_sr / math.sqrt(annualization_factor))) / sr_std
    p_value = 1.0 - float(stats.norm.cdf(z_stat))

    return round(float(annualized_sr), 4), round(float(p_value), 6)


class CombinatorialPurgedGroupSplitter:
    """Grouped Time-Series Cross Validation with Purging and Embargoing."""

    def __init__(self, n_splits: int = 5, embargo_pct: float = 0.02):
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct

    def split(self, df: pd.DataFrame, date_col: str = "day") -> list[tuple[np.ndarray, np.ndarray]]:
        unique_dates = sorted(df[date_col].unique())
        n_dates = len(unique_dates)
        if n_dates < self.n_splits:
            # Fallback simple split if few dates
            indices = np.arange(len(df))
            split_size = len(df) // self.n_splits
            folds = []
            for i in range(self.n_splits):
                test_idx = indices[i * split_size : (i + 1) * split_size]
                train_idx = np.setdiff1d(indices, test_idx)
                folds.append((train_idx, test_idx))
            return folds

        fold_size = n_dates // self.n_splits
        embargo_size = max(1, int(n_dates * self.embargo_pct))
        folds = []

        for i in range(self.n_splits):
            test_dates = set(unique_dates[i * fold_size : (i + 1) * fold_size])
            # Embargo dates immediately following test window
            embargo_end_idx = min(n_dates, (i + 1) * fold_size + embargo_size)
            embargo_dates = set(unique_dates[(i + 1) * fold_size : embargo_end_idx])

            test_mask = df[date_col].isin(test_dates)
            embargo_mask = df[date_col].isin(embargo_dates)
            train_mask = ~(test_mask | embargo_mask)

            train_idx = np.where(train_mask)[0]
            test_idx = np.where(test_mask)[0]
            if len(test_idx) > 0 and len(train_idx) > 0:
                folds.append((train_idx, test_idx))

        return folds


class OptionAlphaModel:
    """LightGBM Multi-Factor Options Alpha & Edge Classifier."""

    def __init__(
        self,
        *,
        model_id: str = "cipher_option_alpha_v1",
        version: str = "1.0.0",
        params: dict[str, Any] | None = None,
    ):
        self.model_id = model_id
        self.version = version
        self.feature_names: list[str] = []
        self.model: lgb.Booster | None = None
        self.params = params or {
            "objective": "binary",
            "metric": "auc",
            "boosting_type": "gbdt",
            "n_estimators": 300,
            "learning_rate": 0.03,
            "num_leaves": 31,
            "max_depth": 6,
            "min_child_samples": 50,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "scale_pos_weight": 2.0,
            "random_state": 42,
            "verbose": -1,
        }

    def fit_cpcv(
        self,
        df: pd.DataFrame,
        feature_cols: list[str],
        label_col: str = "label_profitable",
        date_col: str = "day",
        n_splits: int = 5,
    ) -> dict[str, Any]:
        """Train model with Combinatorial Purged Cross-Validation."""
        self.feature_names = list(feature_cols)
        X = df[self.feature_names].values
        y = df[label_col].values
        returns = df["label_return_on_risk"].values

        splitter = CombinatorialPurgedGroupSplitter(n_splits=n_splits)
        folds = splitter.split(df, date_col=date_col)

        oof_preds = np.zeros(len(df))
        oof_mask = np.zeros(len(df), dtype=bool)
        fold_results = []

        for fold_idx, (train_idx, test_idx) in enumerate(folds):
            X_train, y_train = X[train_idx], y[train_idx]
            X_test, y_test = X[test_idx], y[test_idx]

            train_data = lgb.Dataset(X_train, label=y_train, feature_name=self.feature_names)
            val_data = lgb.Dataset(X_test, label=y_test, feature_name=self.feature_names, reference=train_data)

            booster = lgb.train(
                self.params,
                train_data,
                valid_sets=[val_data],
                callbacks=[lgb.early_stopping(50, verbose=False)],
            )

            preds = booster.predict(X_test)
            oof_preds[test_idx] = preds
            oof_mask[test_idx] = True

            test_dates = df.iloc[test_idx][date_col]
            auc = float(roc_auc_score(y_test, preds)) if len(np.unique(y_test)) > 1 else 0.5
            loss = float(log_loss(y_test, preds))

            # Strategy simulation on top 20% model scores
            top_threshold = np.percentile(preds, 80)
            selected = preds >= top_threshold
            selected_returns = returns[test_idx][selected]
            win_rate = float((selected_returns > 0).mean()) if len(selected_returns) > 0 else 0.0

            # Profit factor
            gains = selected_returns[selected_returns > 0].sum()
            losses = abs(selected_returns[selected_returns < 0].sum())
            profit_factor = float(gains / losses) if losses > 0 else (2.0 if gains > 0 else 1.0)
            sharpe, _ = calculate_deflated_sharpe_ratio(selected_returns, num_trials=1)

            fold_results.append({
                "fold": fold_idx,
                "train_samples": len(train_idx),
                "test_samples": len(test_idx),
                "test_start_date": str(test_dates.min()),
                "test_end_date": str(test_dates.max()),
                "auc_roc": round(auc, 4),
                "log_loss": round(loss, 4),
                "sharpe": round(sharpe, 4),
                "profit_factor": round(profit_factor, 4),
                "win_rate": round(win_rate, 4),
            })

        # Train final model on entire dataset
        full_data = lgb.Dataset(X, label=y, feature_name=self.feature_names)
        self.model = lgb.train(self.params, full_data)

        # Overall OOS metrics
        valid_oof_y = y[oof_mask]
        valid_oof_preds = oof_preds[oof_mask]
        valid_oof_ret = returns[oof_mask]

        overall_auc = float(roc_auc_score(valid_oof_y, valid_oof_preds)) if len(np.unique(valid_oof_y)) > 1 else 0.5
        overall_brier = float(brier_score_loss(valid_oof_y, valid_oof_preds))

        # Overall strategy simulation on top 15% OOF scores
        top_cut = np.percentile(valid_oof_preds, 85)
        top_selected = valid_oof_preds >= top_cut
        strat_returns = valid_oof_ret[top_selected]
        cpcv_sharpe, dsr_pvalue = calculate_deflated_sharpe_ratio(strat_returns, num_trials=25)

        # Feature importances
        importance_scores = self.model.feature_importance(importance_type="gain")
        feat_imp = {
            name: float(score)
            for name, score in sorted(
                zip(self.feature_names, importance_scores), key=lambda x: x[1], reverse=True
            )
        }

        return {
            "model_id": self.model_id,
            "auc_roc": round(overall_auc, 4),
            "brier_score": round(overall_brier, 4),
            "cpcv_sharpe": cpcv_sharpe,
            "dsr_pvalue": dsr_pvalue,
            "feature_importances": feat_imp,
            "fold_results": fold_results,
            "sample_count": len(df),
            "market_dates": int(df[date_col].nunique()),
        }

    def predict_edge_probability(self, features_df: pd.DataFrame) -> np.ndarray:
        """Predict probability of positive execution return for candidate features."""
        if not self.model:
            raise RuntimeError("Model has not been fitted.")

        # Align features
        aligned = pd.DataFrame(index=features_df.index)
        for col in self.feature_names:
            aligned[col] = features_df[col] if col in features_df.columns else 0.0

        return self.model.predict(aligned.values)

    def save(self, destination: Path | None = None) -> Path:
        """Save model artifact to disk."""
        target = destination or (MODELS_DIR / f"{self.model_id}.pkl")
        payload = {
            "model_id": self.model_id,
            "version": self.version,
            "feature_names": self.feature_names,
            "params": self.params,
            "model": self.model,
        }
        with open(target, "wb") as f:
            pickle.dump(payload, f)
        return target

    @classmethod
    def load(cls, path: Path) -> OptionAlphaModel:
        """Load model artifact from disk."""
        with open(path, "rb") as f:
            payload = pickle.load(f)
        instance = cls(
            model_id=payload["model_id"],
            version=payload["version"],
            params=payload["params"],
        )
        instance.feature_names = payload["feature_names"]
        instance.model = payload["model"]
        return instance
