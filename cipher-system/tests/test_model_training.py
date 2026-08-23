"""Unit tests for quantitative model training pipeline components."""
from __future__ import annotations

import math
import os
import pickle
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
OUTCOMES_CSV = ROOT / "data" / "eod_option_pattern_lab" / "daily_option_outcomes.csv"
EQUITY_DB = ROOT / "data" / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite"
GEX_DB = ROOT / "data" / "gex_history.sqlite"
MODELS_DIR = ROOT / "data" / "models"


# ---------------------------------------------------------------------------
# Feature Extractor Tests
# ---------------------------------------------------------------------------

class TestFeatureExtractor:
    """Tests for core.models.feature_extractor."""

    def test_load_daily_bars_returns_dataframe(self):
        from core.models.feature_extractor import load_daily_bars
        df = load_daily_bars()
        if EQUITY_DB.is_file():
            assert isinstance(df, pd.DataFrame)
            assert len(df) > 0
            assert "ret_1d" in df.columns
            assert "realized_vol_20d" in df.columns
            assert "atr_pct" in df.columns

    def test_build_options_dataset_shape(self):
        from core.models.feature_extractor import build_options_dataset
        if not OUTCOMES_CSV.is_file():
            pytest.skip("Options outcomes CSV not available")
        df, cols = build_options_dataset(OUTCOMES_CSV)
        assert len(df) > 0
        assert len(cols) > 5
        assert "label_profitable" in df.columns
        assert "label_return_on_risk" in df.columns
        assert "moneyness_pct" in cols
        assert "fee_to_risk_ratio" in cols
        # No NaN in feature columns
        for col in cols:
            if col in df.columns:
                assert df[col].isna().sum() == 0, f"NaN found in feature column {col}"

    def test_build_options_dataset_filters_executed_only(self):
        from core.models.feature_extractor import build_options_dataset
        if not OUTCOMES_CSV.is_file():
            pytest.skip("Options outcomes CSV not available")
        df, _ = build_options_dataset(OUTCOMES_CSV)
        # All rows should be executed (skipped filtered out)
        raw = pd.read_csv(OUTCOMES_CSV)
        assert len(df) == len(raw[raw["status"] == "executed"])

    def test_calendar_features_cyclic(self):
        from core.models.feature_extractor import build_options_dataset
        if not OUTCOMES_CSV.is_file():
            pytest.skip("Options outcomes CSV not available")
        df, cols = build_options_dataset(OUTCOMES_CSV)
        assert "dow_sin" in cols
        assert "dow_cos" in cols
        # Values should be bounded [-1, 1]
        assert df["dow_sin"].between(-1.0, 1.0).all()
        assert df["dow_cos"].between(-1.0, 1.0).all()


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio Tests
# ---------------------------------------------------------------------------

class TestDeflatedSharpeRatio:
    """Tests for DSR statistical validation."""

    def test_dsr_with_random_returns(self):
        from core.models.option_alpha_model import calculate_deflated_sharpe_ratio
        np.random.seed(42)
        random_returns = np.random.normal(0, 0.02, 200)
        sr, p_value = calculate_deflated_sharpe_ratio(random_returns, num_trials=25)
        # Random returns should have near-zero Sharpe
        assert abs(sr) < 3.0
        # p-value for random returns should be high (not significant)
        assert p_value > 0.01

    def test_dsr_with_strong_alpha(self):
        from core.models.option_alpha_model import calculate_deflated_sharpe_ratio
        np.random.seed(42)
        alpha_returns = np.random.normal(0.02, 0.01, 200)  # Strong positive alpha
        sr, p_value = calculate_deflated_sharpe_ratio(alpha_returns, num_trials=1)
        assert sr > 2.0
        assert p_value < 0.05

    def test_dsr_degenerate_input(self):
        from core.models.option_alpha_model import calculate_deflated_sharpe_ratio
        sr, p_value = calculate_deflated_sharpe_ratio([0.0, 0.0, 0.0])
        assert sr == 0.0
        assert p_value == 1.0


# ---------------------------------------------------------------------------
# CPCV Splitter Tests
# ---------------------------------------------------------------------------

class TestCPCVSplitter:
    """Tests for Combinatorial Purged Group Splitter."""

    def test_split_respects_date_ordering(self):
        from core.models.option_alpha_model import CombinatorialPurgedGroupSplitter
        dates = pd.date_range("2026-01-01", periods=100, freq="B")
        df = pd.DataFrame({"day": dates.strftime("%Y-%m-%d"), "value": range(100)})
        splitter = CombinatorialPurgedGroupSplitter(n_splits=5, embargo_pct=0.02)
        folds = splitter.split(df, date_col="day")
        assert len(folds) == 5
        for train_idx, test_idx in folds:
            assert len(train_idx) > 0
            assert len(test_idx) > 0
            # No overlap
            assert len(set(train_idx) & set(test_idx)) == 0

    def test_embargo_removes_adjacent_dates(self):
        from core.models.option_alpha_model import CombinatorialPurgedGroupSplitter
        dates = pd.date_range("2026-01-01", periods=50, freq="B")
        df = pd.DataFrame({"day": dates.strftime("%Y-%m-%d"), "value": range(50)})
        splitter = CombinatorialPurgedGroupSplitter(n_splits=5, embargo_pct=0.1)
        folds = splitter.split(df, date_col="day")
        embargo_observed = False
        for train_idx, test_idx in folds:
            total = len(train_idx) + len(test_idx)
            if total < len(df):
                embargo_observed = True
        # At least some folds should have embargo-excluded rows
        assert embargo_observed, "No embargo effect observed in any fold"


# ---------------------------------------------------------------------------
# Option Alpha Model Tests
# ---------------------------------------------------------------------------

class TestOptionAlphaModel:
    """Tests for the Tier 1 Options Alpha Model."""

    def test_model_artifact_exists(self):
        model_path = MODELS_DIR / "cipher_option_alpha_v1.pkl"
        assert model_path.is_file(), "Trained Tier 1 model artifact not found"

    def test_model_loads_successfully(self):
        from core.models.option_alpha_model import OptionAlphaModel
        model_path = MODELS_DIR / "cipher_option_alpha_v1.pkl"
        if not model_path.is_file():
            pytest.skip("Model not trained yet")
        model = OptionAlphaModel.load(model_path)
        assert model.model_id == "cipher_option_alpha_v1"
        assert model.model is not None
        assert len(model.feature_names) > 0

    def test_model_predicts_probabilities(self):
        from core.models.option_alpha_model import OptionAlphaModel
        model_path = MODELS_DIR / "cipher_option_alpha_v1.pkl"
        if not model_path.is_file():
            pytest.skip("Model not trained yet")
        model = OptionAlphaModel.load(model_path)
        # Create a sample feature vector
        sample = pd.DataFrame([{col: 0.0 for col in model.feature_names}])
        preds = model.predict_edge_probability(sample)
        assert len(preds) == 1
        assert 0.0 <= preds[0] <= 1.0

    def test_model_save_load_roundtrip(self):
        from core.models.option_alpha_model import OptionAlphaModel
        model_path = MODELS_DIR / "cipher_option_alpha_v1.pkl"
        if not model_path.is_file():
            pytest.skip("Model not trained yet")
        model = OptionAlphaModel.load(model_path)
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            model.save(tmp_path)
            reloaded = OptionAlphaModel.load(tmp_path)
            assert reloaded.model_id == model.model_id
            assert reloaded.feature_names == model.feature_names
            # Predictions should match
            sample = pd.DataFrame([{col: 0.5 for col in model.feature_names}])
            orig_pred = model.predict_edge_probability(sample)
            reload_pred = reloaded.predict_edge_probability(sample)
            assert abs(orig_pred[0] - reload_pred[0]) < 1e-6
        finally:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# GEX Pinning Model Tests
# ---------------------------------------------------------------------------

class TestGexPinningModel:
    """Tests for the Tier 2 GEX Pinning Model."""

    def test_gex_model_artifact_exists(self):
        model_path = MODELS_DIR / "cipher_gex_pinning_v1.pkl"
        assert model_path.is_file(), "Trained Tier 2 GEX model artifact not found"

    def test_gex_model_loads_successfully(self):
        from core.models.gex_pinning_model import GexPinningModel
        model_path = MODELS_DIR / "cipher_gex_pinning_v1.pkl"
        if not model_path.is_file():
            pytest.skip("GEX model not trained yet")
        model = GexPinningModel.load(model_path)
        assert model.model_id == "cipher_gex_pinning_v1"
        assert model.model is not None

    def test_gex_model_predicts_pin_probability(self):
        from core.models.gex_pinning_model import GexPinningModel
        model_path = MODELS_DIR / "cipher_gex_pinning_v1.pkl"
        if not model_path.is_file():
            pytest.skip("GEX model not trained yet")
        model = GexPinningModel.load(model_path)
        sample = pd.DataFrame([{col: 0.0 for col in model.feature_names}])
        preds = model.predict_pin_probability(sample)
        assert len(preds) == 1
        assert 0.0 <= preds[0] <= 1.0

    def test_gex_load_training_samples(self):
        from core.models.gex_pinning_model import load_gex_training_samples
        if not GEX_DB.is_file():
            pytest.skip("GEX database not available")
        df = load_gex_training_samples(limit=100)
        assert isinstance(df, pd.DataFrame)
        if len(df) > 0:
            assert "dist_to_strike_pct" in df.columns
            assert "label_pinned" in df.columns


# ---------------------------------------------------------------------------
# Model Registry Tests
# ---------------------------------------------------------------------------

class TestModelRegistry:
    """Tests for model governance registry persistence."""

    def test_registry_database_exists(self):
        registry_db = ROOT / "data" / "governance" / "model_registry.sqlite"
        assert registry_db.is_file(), "Model registry database not found"

    def test_registered_models_present(self):
        registry_db = ROOT / "data" / "governance" / "model_registry.sqlite"
        if not registry_db.is_file():
            pytest.skip("Registry not created yet")
        with sqlite3.connect(registry_db) as db:
            models = db.execute("SELECT model_id, status FROM registered_models").fetchall()
        model_ids = {row[0] for row in models}
        assert "cipher_option_alpha_v1" in model_ids
        assert "cipher_gex_pinning_v1" in model_ids

    def test_validation_runs_recorded(self):
        registry_db = ROOT / "data" / "governance" / "model_registry.sqlite"
        if not registry_db.is_file():
            pytest.skip("Registry not created yet")
        with sqlite3.connect(registry_db) as db:
            runs = db.execute("SELECT COUNT(*) FROM validation_runs").fetchone()[0]
        assert runs >= 5, f"Expected at least 5 validation fold records, got {runs}"

    def test_feature_importance_recorded(self):
        registry_db = ROOT / "data" / "governance" / "model_registry.sqlite"
        if not registry_db.is_file():
            pytest.skip("Registry not created yet")
        with sqlite3.connect(registry_db) as db:
            features = db.execute("SELECT COUNT(*) FROM feature_importance").fetchone()[0]
        assert features > 0, "No feature importance records found"
