"""Model Governance and Registry for Cipher Quantitative Models.

Persists model fingerprints, training provenance, out-of-sample CPCV metrics,
and Deflated Sharpe Ratio (DSR) verification to SQLite.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DB = ROOT / "data" / "governance" / "model_registry.sqlite"
MODELS_DIR = ROOT / "data" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Path = REGISTRY_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS registered_models (
                model_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tier TEXT NOT NULL,
                algorithm TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                weights_path TEXT NOT NULL,
                weights_sha256 TEXT NOT NULL,
                feature_names_json TEXT NOT NULL,
                hyperparameters_json TEXT NOT NULL,
                dataset_sample_count INTEGER NOT NULL,
                dataset_market_dates INTEGER NOT NULL,
                auc_roc REAL,
                brier_score REAL,
                cpcv_sharpe REAL,
                dsr_pvalue REAL,
                promoted_at TEXT,
                created_at TEXT NOT NULL,
                metadata_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS validation_runs (
                run_id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                fold_index INTEGER NOT NULL,
                train_samples INTEGER NOT NULL,
                test_samples INTEGER NOT NULL,
                test_start_date TEXT NOT NULL,
                test_end_date TEXT NOT NULL,
                auc_roc REAL,
                log_loss REAL,
                sharpe REAL,
                profit_factor REAL,
                win_rate REAL,
                evaluated_at TEXT NOT NULL,
                FOREIGN KEY (model_id) REFERENCES registered_models (model_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feature_importance (
                model_id TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                importance_score REAL NOT NULL,
                importance_type TEXT NOT NULL,
                PRIMARY KEY (model_id, feature_name),
                FOREIGN KEY (model_id) REFERENCES registered_models (model_id)
            )
        """)
    return conn


def register_model(
    *,
    model_id: str,
    name: str,
    tier: str,
    algorithm: str,
    version: str,
    weights_path: Path,
    feature_names: Sequence[str],
    hyperparameters: Mapping[str, Any],
    dataset_sample_count: int,
    dataset_market_dates: int,
    auc_roc: float | None = None,
    brier_score: float | None = None,
    cpcv_sharpe: float | None = None,
    dsr_pvalue: float | None = None,
    feature_importances: Mapping[str, float] | None = None,
    validation_folds: Sequence[Mapping[str, Any]] | None = None,
    status: str = "trained",
    metadata: Mapping[str, Any] | None = None,
    db_path: Path = REGISTRY_DB,
) -> dict[str, Any]:
    """Register a trained model artifact with verified provenance."""
    raw_weights = weights_path.read_bytes()
    weights_sha256 = hashlib.sha256(raw_weights).hexdigest()
    now_utc = datetime.now(timezone.utc).isoformat()

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO registered_models (
                model_id, name, tier, algorithm, version, status,
                weights_path, weights_sha256, feature_names_json,
                hyperparameters_json, dataset_sample_count, dataset_market_dates,
                auc_roc, brier_score, cpcv_sharpe, dsr_pvalue, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_id,
                name,
                tier,
                algorithm,
                version,
                status,
                str(weights_path),
                weights_sha256,
                json.dumps(list(feature_names)),
                json.dumps(dict(hyperparameters)),
                dataset_sample_count,
                dataset_market_dates,
                auc_roc,
                brier_score,
                cpcv_sharpe,
                dsr_pvalue,
                now_utc,
                json.dumps(metadata or {}),
            ),
        )

        if feature_importances:
            for feat, score in feature_importances.items():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO feature_importance (
                        model_id, feature_name, importance_score, importance_type
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (model_id, feat, float(score), "gain"),
                )

        if validation_folds:
            for idx, fold in enumerate(validation_folds):
                run_id = f"{model_id}_fold_{idx}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO validation_runs (
                        run_id, model_id, fold_index, train_samples, test_samples,
                        test_start_date, test_end_date, auc_roc, log_loss,
                        sharpe, profit_factor, win_rate, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        model_id,
                        idx,
                        fold.get("train_samples", 0),
                        fold.get("test_samples", 0),
                        str(fold.get("test_start_date", "")),
                        str(fold.get("test_end_date", "")),
                        fold.get("auc_roc"),
                        fold.get("log_loss"),
                        fold.get("sharpe"),
                        fold.get("profit_factor"),
                        fold.get("win_rate"),
                        now_utc,
                    ),
                )

    return {
        "model_id": model_id,
        "name": name,
        "tier": tier,
        "status": status,
        "weights_sha256": weights_sha256,
        "registered_at": now_utc,
    }


def list_registered_models(
    db_path: Path = REGISTRY_DB, status: str | None = None
) -> list[dict[str, Any]]:
    """List all registered models."""
    with get_connection(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM registered_models WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM registered_models ORDER BY created_at DESC"
            ).fetchall()

        results = []
        for r in rows:
            results.append({
                "model_id": r["model_id"],
                "name": r["name"],
                "tier": r["tier"],
                "algorithm": r["algorithm"],
                "version": r["version"],
                "status": r["status"],
                "auc_roc": r["auc_roc"],
                "cpcv_sharpe": r["cpcv_sharpe"],
                "dsr_pvalue": r["dsr_pvalue"],
                "dataset_sample_count": r["dataset_sample_count"],
                "weights_sha256": r["weights_sha256"],
                "created_at": r["created_at"],
            })
        return results


def get_active_model(
    tier: str = "Tier 1", db_path: Path = REGISTRY_DB
) -> dict[str, Any] | None:
    """Retrieve active production model for given tier."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM registered_models
            WHERE tier = ? AND status IN ('active_production', 'trained')
            ORDER BY cpcv_sharpe DESC, created_at DESC LIMIT 1
            """,
            (tier,),
        ).fetchone()
        if not row:
            return None
        return dict(row)
