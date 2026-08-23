"""Automated training pipeline for Cipher Quantitative Models.

Ingests all available options outcomes, equity bars, and GEX snapshots;
extracts features, fits Tier 1 and Tier 2 models with CPCV and DSR validation,
and registers artifacts into the model governance registry.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

from core.models.feature_extractor import build_options_dataset, load_daily_bars
from core.models.gex_pinning_model import GexPinningModel, load_gex_training_samples
from core.models.model_registry import register_model
from core.models.option_alpha_model import OptionAlphaModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cipher.training")

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
OUTCOMES_CSV = DATA_DIR / "eod_option_pattern_lab" / "daily_option_outcomes.csv"
EQUITY_DB = DATA_DIR / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite"
GEX_DB = DATA_DIR / "gex_history.sqlite"


def run_full_training(
    outcomes_path: Path = OUTCOMES_CSV,
    equity_path: Path = EQUITY_DB,
    gex_path: Path = GEX_DB,
    n_splits: int = 5,
) -> dict[str, Any]:
    """Execute end-to-end model training across all available historical data."""
    start_time = time.monotonic()
    logger.info("=== Starting Cipher Quantitative Financial Model Training ===")

    # 1. Load Underlying Equity Bars
    logger.info("Step 1: Loading underlying equity bars...")
    daily_bars = load_daily_bars(equity_path)
    logger.info("Loaded %d daily bars across symbols.", len(daily_bars))

    # 2. Extract Options Feature Matrix
    logger.info("Step 2: Building multi-factor options dataset from %s...", outcomes_path)
    if not outcomes_path.is_file():
        raise FileNotFoundError(f"Options outcomes dataset not found at {outcomes_path}")

    df_options, feature_cols = build_options_dataset(outcomes_path, daily_bars=daily_bars)
    logger.info(
        "Extracted %d executed trades with %d feature dimensions across %d trading days.",
        len(df_options),
        len(feature_cols),
        df_options["day"].nunique(),
    )

    # 3. Train Tier 1 Options Alpha Model (CPCV + DSR)
    logger.info("Step 3: Training Tier 1 Options Alpha GBDT with %d-fold CPCV...", n_splits)
    alpha_model = OptionAlphaModel(
        model_id="cipher_option_alpha_v1",
        version="1.0.0",
    )
    alpha_metrics = alpha_model.fit_cpcv(
        df_options,
        feature_cols=feature_cols,
        n_splits=n_splits,
    )
    alpha_weights_path = alpha_model.save()
    logger.info(
        "Tier 1 Model Complete: OOS AUC-ROC = %.4f, CPCV Sharpe = %.2f, DSR p-value = %.4f",
        alpha_metrics["auc_roc"],
        alpha_metrics["cpcv_sharpe"],
        alpha_metrics["dsr_pvalue"],
    )

    # Register Tier 1 Model
    reg_alpha = register_model(
        model_id=alpha_model.model_id,
        name="Cipher Options Alpha & Edge Classifier",
        tier="Tier 1",
        algorithm="LightGBM GBDT",
        version=alpha_model.version,
        weights_path=alpha_weights_path,
        feature_names=alpha_model.feature_names,
        hyperparameters=alpha_model.params,
        dataset_sample_count=alpha_metrics["sample_count"],
        dataset_market_dates=alpha_metrics["market_dates"],
        auc_roc=alpha_metrics["auc_roc"],
        brier_score=alpha_metrics["brier_score"],
        cpcv_sharpe=alpha_metrics["cpcv_sharpe"],
        dsr_pvalue=alpha_metrics["dsr_pvalue"],
        feature_importances=alpha_metrics["feature_importances"],
        validation_folds=alpha_metrics["fold_results"],
        status="active_production",
        metadata={"feature_count": len(feature_cols)},
    )
    logger.info("Tier 1 Model registered: %s (SHA-256: %s)", reg_alpha["model_id"], reg_alpha["weights_sha256"][:12])

    # 4. Train Tier 2 GEX Pinning Model
    logger.info("Step 4: Training Tier 2 GEX Pinning & Volatility Dynamics Model...")
    gex_df = load_gex_training_samples(gex_path, limit=50000)
    gex_reg = None
    if not gex_df.empty:
        logger.info("Loaded %d GEX strike observation cells.", len(gex_df))
        gex_model = GexPinningModel(
            model_id="cipher_gex_pinning_v1",
            version="1.0.0",
        )
        gex_metrics = gex_model.fit(gex_df)
        gex_weights_path = gex_model.save()
        logger.info("Tier 2 GEX Model Complete: AUC-ROC = %.4f", gex_metrics["auc_roc"])

        gex_reg = register_model(
            model_id=gex_model.model_id,
            name="Cipher GEX Strike Pinning & Volatility Model",
            tier="Tier 2",
            algorithm="LightGBM GBDT",
            version=gex_model.version,
            weights_path=gex_weights_path,
            feature_names=gex_model.feature_names,
            hyperparameters=gex_model.params,
            dataset_sample_count=gex_metrics["sample_count"],
            dataset_market_dates=int(gex_df["date"].nunique()) if "date" in gex_df.columns else 1,
            auc_roc=gex_metrics["auc_roc"],
            feature_importances=gex_metrics["feature_importances"],
            status="active_production",
        )
        logger.info("Tier 2 Model registered: %s", gex_reg["model_id"])

    elapsed = time.monotonic() - start_time
    logger.info("=== Full Training Pipeline Finished in %.2f seconds ===", elapsed)

    return {
        "tier1_alpha": {
            "model_id": alpha_model.model_id,
            "metrics": alpha_metrics,
            "registration": reg_alpha,
        },
        "tier2_gex": {
            "model_id": "cipher_gex_pinning_v1" if gex_reg else None,
            "metrics": gex_metrics if gex_reg else None,
            "registration": gex_reg,
        },
        "elapsed_seconds": round(elapsed, 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cipher Quantitative Training Pipeline")
    parser.add_argument("--splits", type=int, default=5, help="Number of CPCV folds")
    args = parser.parse_args()

    results = run_full_training(n_splits=args.splits)
    print("\n" + "=" * 60)
    print("TRAINING SUMMARY REPORT")
    print("=" * 60)
    print(json.dumps(results, indent=2, default=str))
