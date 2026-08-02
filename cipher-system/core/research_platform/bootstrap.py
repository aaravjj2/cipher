from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore
from .config import ResearchPlatformConfig
from .datasets import DatasetService
from .features import FeatureService, ModelArtifactManifest
from .hashing import sha256_file, stable_id
from .inventory import SystemInventoryBuilder
from .models import AllowedUse, StrategySpec
from .raw_lake import RawLake
from .registry import ResearchRegistry
from .warehouse import BigQueryWarehousePlan


class ResearchPlatform:
    def __init__(self, config: ResearchPlatformConfig):
        config.validate()
        config.ensure_directories()
        self.config = config
        self.registry = ResearchRegistry(config.registry_path)
        self.artifacts = ArtifactStore(config.artifact_root)
        self.raw_lake = RawLake(
            config.raw_lake_root,
            registry=self.registry,
            artifact_store=self.artifacts,
            gcs_bucket=config.gcs_bucket,
        )
        self.datasets = DatasetService(
            registry=self.registry,
            raw_lake=self.raw_lake,
            artifact_store=self.artifacts,
            snapshot_root=config.snapshot_root,
        )
        self.features = FeatureService(self.registry, self.artifacts)
        self.warehouse = BigQueryWarehousePlan(
            project=config.bigquery_project,
            dataset=config.bigquery_dataset,
            export_root=config.warehouse_export_root,
            artifact_store=self.artifacts,
            registry=self.registry,
        )

    def bootstrap(self, *, catalog_current_data: bool = True) -> dict[str, Any]:
        inventory, inventory_artifact = self._inventory()
        model_summary = self._model_policies()
        strategy_summary = self._current_strategy_specs(model_summary)
        ddl_artifact = self.warehouse.write_ddl()
        datasets = self._catalog_current_data() if catalog_current_data else []
        summary = {
            "schema_version": 1,
            "config": self.config.to_dict(),
            "inventory_id": inventory["inventory_id"],
            "inventory_artifact_id": inventory_artifact.artifact_id,
            "models": model_summary,
            "strategies": strategy_summary,
            "warehouse_schema_artifact_id": ddl_artifact.artifact_id,
            "cataloged_datasets": datasets,
            "registry_counts": self.registry.counts(),
            "execution_boundary": {
                "live_order_code_added": False,
                "broker_adapter_added": False,
                "maximum_state": "LIVE_REVIEW_REQUIRED",
            },
        }
        self.config.inventory_output_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary

    def status(self) -> dict[str, Any]:
        with self.registry.connect() as db:
            strategies = [
                dict(row)
                for row in db.execute(
                    "select strategy_id, name, version, current_state, created_at from strategies order by name, version"
                ).fetchall()
            ]
            features = [
                dict(row)
                for row in db.execute(
                    "select feature_id, name, version, allowed_use from features order by name, version"
                ).fetchall()
            ]
            recent_experiments = [
                dict(row)
                for row in db.execute(
                    """
                    select experiment_id, strategy_id, engine, status, verdict, started_at, completed_at
                    from experiments order by started_at desc limit 20
                    """
                ).fetchall()
            ]
        return {
            "config": self.config.to_dict(),
            "counts": self.registry.counts(),
            "strategies": strategies,
            "features": features,
            "recent_experiments": recent_experiments,
            "cloud_writes_enabled": self.config.cloud_writes_enabled,
            "live_execution_present": False,
        }

    def _inventory(self):
        builder = SystemInventoryBuilder(
            self.config.repository_root,
            registry=self.registry,
            artifact_store=self.artifacts,
            active_paths=(
                "cipher-system/core/app.py",
                "cipher-system/core/scanner.py",
                "cipher-system/core/cluster_backtest.py",
                "cipher-system/core/gex_capture.py",
                "cipher-system/core/ranking_lab.py",
                "cipher-system/core/weight_lab.py",
                "cipher-system/core/research_platform",
                "cipher-system/app",
                "infra/gcp-cipher-vm",
            ),
            shadow_paths=(
                "cipher-system/core/paper_executor",
                "cipher-system/core/cluster_kronos_forward.py",
                "cipher-system/core/flow_forward_test.py",
                "cipher-system/core/paper_trade_engine.py",
            ),
            archived_prefixes=("cipher-system/previous-work",),
        )
        return builder.build()

    def _model_policies(self) -> dict[str, Any]:
        root = self.config.repository_root
        kronos_path = root / "cipher-system" / "core" / "kronos_research.py"
        timesfm_path = root / "cipher-system" / "core" / "timesfm_walkforward.py"
        kronos_hash = sha256_file(kronos_path)
        timesfm_hash = sha256_file(timesfm_path)
        kronos_feature, timesfm_feature = self.features.bootstrap_cipher_model_policies(
            kronos_implementation_hash=kronos_hash,
            timesfm_implementation_hash=timesfm_hash,
        )
        kronos_manifest = ModelArtifactManifest(
            model_name="Kronos",
            model_version="NeoQuasar/Kronos-small locked prospective context",
            implementation_hash=kronos_hash,
            weights_artifact_id=None,
            training_dataset_id=None,
            training_cutoff=None,
            validation_plan={
                "mode": "prospective",
                "minimum_scored": 100,
                "current_policy": "context_only",
                "entry_gate": False,
                "sizing": False,
            },
            allowed_use=AllowedUse.CONTEXT,
            runtime_requirements=("torch", "huggingface_hub", "safetensors"),
            status="CONTEXT_ONLY_PROSPECTIVE",
            blockers=("minimum prospective sample not yet reached", "historical checkpoint effect was unstable"),
        )
        timesfm_weights = root / "cipher-system" / "data" / "timesfm_model" / "timesfm_gex_finetuned.pt"
        timesfm_provenance = root / "cipher-system" / "data" / "timesfm_model" / "manifest.json"
        blockers: list[str] = []
        if not timesfm_weights.exists():
            blockers.append("project-specific weights absent")
        if not timesfm_provenance.exists():
            blockers.append("training provenance manifest absent")
        timesfm_manifest = ModelArtifactManifest(
            model_name="TimesFM",
            model_version="google/timesfm-2.5-200m-pytorch bridge",
            implementation_hash=timesfm_hash,
            weights_artifact_id=None,
            training_dataset_id=None,
            training_cutoff=None,
            validation_plan={
                "required": "explicit walk-forward with point-in-time GEX",
                "current_policy": "blocked",
            },
            allowed_use=AllowedUse.CONTEXT,
            runtime_requirements=("timesfm", "torch"),
            status="BLOCKED" if blockers else "MANIFEST_REVIEW_REQUIRED",
            blockers=tuple(blockers),
        )
        kronos_artifact = self.features.register_model_manifest(kronos_manifest)
        timesfm_artifact = self.features.register_model_manifest(timesfm_manifest)
        return {
            "kronos": {
                "feature_id": kronos_feature.feature_id,
                "manifest_id": kronos_manifest.model_artifact_manifest_id,
                "artifact_id": kronos_artifact.artifact_id,
                "status": kronos_manifest.status,
                "allowed_use": AllowedUse.CONTEXT.value,
            },
            "timesfm": {
                "feature_id": timesfm_feature.feature_id,
                "manifest_id": timesfm_manifest.model_artifact_manifest_id,
                "artifact_id": timesfm_artifact.artifact_id,
                "status": timesfm_manifest.status,
                "blockers": list(timesfm_manifest.blockers),
                "allowed_use": AllowedUse.CONTEXT.value,
            },
        }

    def _current_strategy_specs(self, models: dict[str, Any]) -> list[dict[str, Any]]:
        cluster = StrategySpec(
            name="cluster_directional_debit_spread_shadow",
            version="prospective-v1",
            signal_rule={
                "source": "AccessObsidian normalized Cluster capture",
                "rank_scope": "locked forward-test configuration",
                "direction": "captured cluster direction",
                "kronos_role": "context only; cannot gate or size",
            },
            instrument_rule={"structure": "defined-risk debit spread", "mode": "virtual"},
            contract_selection_rule={
                "point_in_time_quotes_required": True,
                "maximum_width": "configuration locked",
                "no_fractional_contracts": True,
            },
            entry_rule={"entry_time": "capture availability time", "entry_at": "conservative executable debit"},
            exit_rule={"profiles": "preregistered multi-profile forward test"},
            sizing_rule={"quantity": 1, "capital_effect": "none; shadow only"},
            portfolio_constraints={"live_capital": False, "broker_orders": False},
            required_feature_ids=(models["kronos"]["feature_id"],),
            fill_model={"entry": "ask-side debit", "exit": "bid-side credit", "stale_quotes_block": True},
            benchmark="unfiltered registered Cluster candidates",
            statistical_plan={
                "prospective_minimum": 100,
                "compare": "Kronos agreed vs disagreed",
                "rule_changes_before_minimum": False,
            },
            promotion_thresholds={
                "minimum_trades": 100,
                "minimum_profit_factor": 1.0,
                "maximum_drawdown_pct": 35.0,
                "required_quality_checks": ["point_in_time_validated"],
                "require_walk_forward": True,
                "maximum_exclusion_ratio": 0.2,
            },
            description="Current Cluster prospective architecture registered without changing its locked rule.",
        )
        flash = StrategySpec(
            name="flash_defined_risk_shadow_executor",
            version="shadow-v1",
            signal_rule={"source": "normalized Flash/Flash Agentic captures", "patterns": "configuration allowlist"},
            instrument_rule={"structure": "long option or debit spread", "live_orders": False},
            contract_selection_rule={
                "dte": [1, 3],
                "minimum_open_interest": 100,
                "minimum_volume": 10,
                "maximum_spread_pct": 12,
                "maximum_cost": 700,
            },
            entry_rule={"entry": "simulated ask plus slippage", "fresh_quote_seconds": 2},
            exit_rule={"take_profit_pct": 20, "stop_loss_pct": 15, "maximum_hold_minutes": 45},
            sizing_rule={"quantity": 1},
            portfolio_constraints={
                "maximum_positions": 3,
                "maximum_positions_per_ticker": 1,
                "daily_loss_stop_count": 2,
            },
            required_feature_ids=(),
            fill_model={"entry": "ask", "exit": "bid", "minimum_slippage_dollars": 0.01},
            benchmark="same signals without pattern filter",
            statistical_plan={"required": "historical, LEAN, and preregistered prospective evidence"},
            promotion_thresholds={
                "minimum_trades": 100,
                "minimum_profit_factor": 1.1,
                "maximum_drawdown_pct": 25.0,
                "require_walk_forward": True,
                "require_best_trade_exclusion": True,
            },
            description="Existing shadow executor policy registered as research evidence; no broker path.",
        )
        output: list[dict[str, Any]] = []
        for strategy in (cluster, flash):
            self.registry.register_strategy(strategy)
            output.append({"strategy_id": strategy.strategy_id, "name": strategy.name, "state": "IDEA"})
        return output

    def _catalog_current_data(self) -> list[dict[str, Any]]:
        root = self.config.repository_root / "cipher-system" / "data"
        candidates = (
            ("tradier_stream", "tradier_production", root / "tradier_stream.sqlite"),
            ("gex_history", "alpaca_opra", root / "gex_history.sqlite"),
            ("historical_bars", "mixed_market_bars", root / "historical_bars.sqlite"),
            ("flow_forward_test", "cipher_forward_test", root / "flow_forward_test.sqlite"),
            ("paper_trades_legacy", "cipher_paper_simulation", root / "paper_trades" / "paper_trades.sqlite"),
        )
        output: list[dict[str, Any]] = []
        for dataset_name, source_name, path in candidates:
            if not path.exists() or path.stat().st_size == 0:
                continue
            collect_counts = path.stat().st_size <= 256 * 1024 * 1024
            try:
                manifest, profile, _, artifact = self.datasets.catalog_operational_sqlite(
                    path,
                    dataset_name=dataset_name,
                    source_name=source_name,
                    include_row_counts=collect_counts,
                    include_timestamp_ranges=collect_counts,
                    integrity_mode="quick" if collect_counts else "skip",
                )
                output.append(
                    {
                        "dataset_id": manifest.dataset_id,
                        "name": dataset_name,
                        "path": str(path),
                        "size_bytes": path.stat().st_size,
                        "table_count": len(profile.tables),
                        "row_counts_collected": collect_counts,
                        "integrity_ok": profile.integrity_ok,
                        "profile_artifact_id": artifact.artifact_id,
                    }
                )
            except Exception as exc:
                output.append(
                    {
                        "name": dataset_name,
                        "path": str(path),
                        "size_bytes": path.stat().st_size,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return output
