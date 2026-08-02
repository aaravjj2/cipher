from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bootstrap import ResearchPlatform
from .canonical_exports import CanonicalSQLiteExporter
from .cloud_deploy import CloudDeploymentService
from .config import ResearchPlatformConfig
from .current_evidence import CurrentEvidenceImporter
from .experiments import ExperimentRunner, LegacyJsonReportAdapter, runtime_environment_id
from .factors import FactorCandidate, FactorResearchService
from .features import FeatureService
from .hashing import sha256_file, stable_id
from .lean import LeanEvidenceService
from .news import FinBertSentimentProvider, NewsDocument, NewsFeatureService
from .models import (
    AllowedUse,
    EngineKind,
    ExperimentManifest,
    FeatureSpec,
    PromotionState,
    StrategySpec,
    utc_now,
)
from .promotion import PromotionService
from .prospective import ProspectiveObservation, ProspectiveRegistration, ProspectiveService
from .reconciliation import EvidenceReconciliationService

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "cipher-system" / "config" / "research-platform.json"


def _json_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _platform(config_path: str | None) -> ResearchPlatform:
    config = ResearchPlatformConfig.load(config_path or DEFAULT_CONFIG, ROOT)
    return ResearchPlatform(config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cipher research governance and strategy-graduation platform")
    parser.add_argument("--config", help=f"Configuration JSON (default: {DEFAULT_CONFIG})")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize governance, inventory, model policies, and canonical schema")
    init.add_argument("--skip-catalog", action="store_true", help="Do not catalog existing operational databases")

    sub.add_parser("status", help="Show registry and execution-boundary status")
    sub.add_parser("ddl", help="Print canonical BigQuery DDL")
    sub.add_parser("cloud-plan", help="Show explicitly gated GCP deployment commands")

    deploy_schema = sub.add_parser("deploy-warehouse-schema", help="Apply BigQuery DDL when cloud writes are explicitly enabled")
    deploy_schema.add_argument("--confirmation", required=True)

    load_exports = sub.add_parser("load-warehouse-exports", help="Load a staged canonical export manifest into BigQuery")
    load_exports.add_argument("manifest")
    load_exports.add_argument("--confirmation", required=True)

    upload_artifact = sub.add_parser("upload-cloud-artifact", help="Upload one immutable artifact when cloud writes are explicitly enabled")
    upload_artifact.add_argument("path")
    upload_artifact.add_argument("--destination-name")
    upload_artifact.add_argument("--confirmation", required=True)

    current = sub.add_parser("import-current-evidence", help="Import existing forward tests and authoritative status files")
    current.add_argument(
        "--runtime-root",
        default="/home/aarav/Aarav/cipher-system/CipherCapture",
        help="CipherCapture runtime root",
    )

    canonical = sub.add_parser("export-canonical-sqlite", help="Stage canonical warehouse JSONL from a frozen SQLite dataset")
    canonical.add_argument("path")
    canonical.add_argument("--dataset-id", required=True)
    canonical.add_argument("--kind", choices=["historical_bars", "gex_history", "tradier_stream"], required=True)
    canonical.add_argument("--after")
    canonical.add_argument("--limit", type=int)

    catalog = sub.add_parser("catalog-sqlite", help="Catalog a mutable operational SQLite database")
    catalog.add_argument("path")
    catalog.add_argument("--name", required=True)
    catalog.add_argument("--source", required=True)
    catalog.add_argument("--row-counts", action="store_true")

    freeze = sub.add_parser("freeze-sqlite", help="Create an immutable SQLite research snapshot")
    freeze.add_argument("path")
    freeze.add_argument("--name", required=True)
    freeze.add_argument("--source", required=True)
    freeze.add_argument("--availability-cutoff", required=True)
    freeze.add_argument("--universe-id", required=True)
    freeze.add_argument("--corporate-action-version", required=True)
    freeze.add_argument("--normalizer-version", required=True)
    freeze.add_argument("--schema-name", required=True)

    feature = sub.add_parser("register-feature", help="Register a feature specification JSON")
    feature.add_argument("spec")

    factor = sub.add_parser("register-factor", help="Validate and register a raw-column factor DSL specification")
    factor.add_argument("spec")

    sub.add_parser("finbert-status", help="Report whether local FinBERT inference dependencies are available")
    news = sub.add_parser("process-news", help="Process one point-in-time news document with local FinBERT")
    news.add_argument("document")
    news.add_argument("--model-id", default="ProsusAI/finbert")
    news.add_argument("--device", type=int, default=-1)
    news.add_argument("--chunk-words", type=int, default=360)
    news.add_argument("--overlap-words", type=int, default=60)

    strategy = sub.add_parser("register-strategy", help="Register a strategy specification JSON")
    strategy.add_argument("spec")

    lean_audit = sub.add_parser("import-lean-audit", help="Validate and register a research-grade LEAN audit JSON")
    lean_audit.add_argument("audit")

    experiment = sub.add_parser("import-experiment", help="Normalize and register an existing JSON backtest report")
    experiment.add_argument("--strategy-spec", required=True)
    experiment.add_argument("--dataset-id", required=True)
    experiment.add_argument("--feature-set-id", default="feature_set_none")
    experiment.add_argument("--report", required=True)
    experiment.add_argument("--engine", choices=[value.value for value in EngineKind], default=EngineKind.IMPORTED.value)
    experiment.add_argument("--seed", type=int, default=42)
    experiment.add_argument("--hypothesis", default="Imported existing Cipher research report")
    experiment.add_argument("--parameters", help="JSON object or path")

    promote = sub.add_parser("promote", help="Apply an evidence-gated strategy promotion")
    promote.add_argument("strategy_id")
    promote.add_argument("to_state", choices=[value.value for value in PromotionState])
    promote.add_argument("--actor", required=True)
    promote.add_argument("--reason", required=True)
    promote.add_argument("--evidence-id", action="append", default=[])
    promote.add_argument("--metadata", default="{}", help="JSON object or path")

    preg = sub.add_parser("prospective-register", help="Register a locked prospective test")
    preg.add_argument("--strategy-id", required=True)
    preg.add_argument("--name", required=True)
    preg.add_argument("--minimum-sample", type=int, required=True)
    preg.add_argument("--configuration", required=True, help="JSON object or path")
    preg.add_argument("--criteria", required=True, help="JSON object or path")

    pappend = sub.add_parser("prospective-append", help="Append an immutable prospective observation")
    pappend.add_argument("observation", help="Observation JSON")

    peval = sub.add_parser("prospective-evaluate", help="Evaluate a prospective test against locked criteria")
    peval.add_argument("prospective_test_id")
    peval.add_argument("--keep-open", action="store_true")

    reconcile = sub.add_parser("reconcile", help="Reconcile fast, LEAN, prospective, and paper evidence")
    reconcile.add_argument("--strategy-id", required=True)
    reconcile.add_argument("--fast-experiment-id", required=True)
    reconcile.add_argument("--lean-experiment-id")
    reconcile.add_argument("--prospective-test-id")
    reconcile.add_argument("--paper-metrics", help="JSON object or path")

    return parser


def _json_argument(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    candidate = Path(value)
    if candidate.exists():
        return _json_file(candidate)
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("argument must be a JSON object")
    return payload


def _feature_spec(payload: dict[str, Any]) -> FeatureSpec:
    return FeatureSpec(
        name=payload["name"],
        version=payload["version"],
        inputs=tuple(payload["inputs"]),
        lookback=payload["lookback"],
        availability_lag_seconds=int(payload["availability_lag_seconds"]),
        missing_value_policy=payload["missing_value_policy"],
        allowed_use=AllowedUse(payload["allowed_use"]),
        implementation_hash=payload["implementation_hash"],
        training_cutoff=_timestamp(payload["training_cutoff"]) if payload.get("training_cutoff") else None,
        model_artifact_id=payload.get("model_artifact_id"),
        leakage_checks=payload.get("leakage_checks") or {},
        description=payload.get("description") or "",
        feature_id=payload.get("feature_id") or "",
    )


def _strategy_spec(payload: dict[str, Any]) -> StrategySpec:
    return StrategySpec(
        name=payload["name"],
        version=payload["version"],
        signal_rule=payload["signal_rule"],
        instrument_rule=payload["instrument_rule"],
        contract_selection_rule=payload["contract_selection_rule"],
        entry_rule=payload["entry_rule"],
        exit_rule=payload["exit_rule"],
        sizing_rule=payload["sizing_rule"],
        portfolio_constraints=payload["portfolio_constraints"],
        required_feature_ids=tuple(payload.get("required_feature_ids") or ()),
        fill_model=payload["fill_model"],
        benchmark=payload["benchmark"],
        statistical_plan=payload["statistical_plan"],
        promotion_thresholds=payload["promotion_thresholds"],
        description=payload.get("description") or "",
        strategy_id=payload.get("strategy_id") or "",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    platform = _platform(args.config)

    if args.command == "init":
        result = platform.bootstrap(catalog_current_data=not args.skip_catalog)
    elif args.command == "status":
        result = platform.status()
    elif args.command == "ddl":
        print(platform.warehouse.ddl())
        return 0
    elif args.command == "cloud-plan":
        result = CloudDeploymentService(platform).plan()
    elif args.command == "deploy-warehouse-schema":
        result = CloudDeploymentService(platform).deploy_schema(confirmation=args.confirmation)
    elif args.command == "load-warehouse-exports":
        result = CloudDeploymentService(platform).load_export_manifest(
            args.manifest,
            confirmation=args.confirmation,
        )
    elif args.command == "upload-cloud-artifact":
        result = CloudDeploymentService(platform).upload_artifact(
            args.path,
            confirmation=args.confirmation,
            destination_name=args.destination_name,
        )
    elif args.command == "import-current-evidence":
        result = CurrentEvidenceImporter(platform).import_all(runtime_root=args.runtime_root)
    elif args.command == "export-canonical-sqlite":
        exports = CanonicalSQLiteExporter(platform.registry, platform.warehouse).export(
            dataset_id=args.dataset_id,
            sqlite_path=args.path,
            kind=args.kind,
            after=args.after,
            limit=args.limit,
        )
        result = {"exports": [item.to_dict() for item in exports], "loaded": False}
    elif args.command == "catalog-sqlite":
        manifest, profile, raw, artifact = platform.datasets.catalog_operational_sqlite(
            args.path,
            dataset_name=args.name,
            source_name=args.source,
            include_row_counts=args.row_counts,
            include_timestamp_ranges=args.row_counts,
        )
        result = {
            "dataset": manifest.to_dict(),
            "profile": profile.to_dict(),
            "raw_object": raw.manifest.to_dict(),
            "profile_artifact": artifact.to_dict(),
        }
    elif args.command == "freeze-sqlite":
        manifest, profile, raw, artifact = platform.datasets.freeze_sqlite(
            args.path,
            dataset_name=args.name,
            source_name=args.source,
            availability_cutoff=_timestamp(args.availability_cutoff),
            symbol_universe_id=args.universe_id,
            corporate_action_version=args.corporate_action_version,
            normalizer_version=args.normalizer_version,
            schema_name=args.schema_name,
        )
        result = {
            "dataset": manifest.to_dict(),
            "profile": profile.to_dict(),
            "raw_object": raw.manifest.to_dict(),
            "profile_artifact": artifact.to_dict(),
        }
    elif args.command == "register-feature":
        spec = _feature_spec(_json_file(args.spec))
        inserted = platform.registry.register_feature(spec)
        result = {"inserted": inserted, "feature": spec.to_dict()}
    elif args.command == "register-factor":
        payload = _json_file(args.spec)
        candidate = FactorCandidate(
            name=payload["name"],
            version=payload["version"],
            expression=payload["expression"],
            hypothesis=payload["hypothesis"],
            expected_direction=payload["expected_direction"],
            availability_lag_seconds=int(payload.get("availability_lag_seconds", 0)),
            missing_value_policy=payload.get("missing_value_policy", "unavailable"),
            allowed_use=AllowedUse(payload.get("allowed_use", "context")),
            metadata=payload.get("metadata") or {},
            candidate_id=payload.get("candidate_id") or "",
        )
        compiled, feature_spec, artifact = FactorResearchService(
            platform.registry,
            platform.artifacts,
        ).register_candidate(candidate)
        result = {
            "candidate": candidate.to_dict(),
            "feature": feature_spec.to_dict(),
            "required_columns": list(compiled.required_columns),
            "implementation_hash": compiled.implementation_hash,
            "artifact": artifact.to_dict(),
        }
    elif args.command == "finbert-status":
        import importlib.util

        result = {
            "transformers_available": importlib.util.find_spec("transformers") is not None,
            "local_inference_only": True,
            "remote_inference_enabled": False,
        }
    elif args.command == "process-news":
        payload = _json_file(args.document)
        document = NewsDocument(
            source=payload["source"],
            external_id=payload["external_id"],
            title=payload["title"],
            text=payload["text"],
            publication_time=_timestamp(payload["publication_time"]),
            received_at=_timestamp(payload["received_at"]),
            available_at=_timestamp(payload["available_at"]),
            symbols=tuple(payload.get("symbols") or ()),
            url_hash=payload.get("url_hash"),
            raw_object_id=payload.get("raw_object_id"),
            metadata=payload.get("metadata") or {},
        )
        provider = FinBertSentimentProvider(args.model_id, device=args.device)
        record, artifact = NewsFeatureService(platform.registry, platform.artifacts).process(
            document,
            provider,
            chunk_words=args.chunk_words,
            overlap_words=args.overlap_words,
        )
        result = {"news_event": record.to_dict(), "artifact": artifact.to_dict()}
    elif args.command == "register-strategy":
        spec = _strategy_spec(_json_file(args.spec))
        inserted = platform.registry.register_strategy(spec)
        result = {"inserted": inserted, "strategy": spec.to_dict(), "state": platform.registry.current_state(spec.strategy_id).value}
    elif args.command == "import-lean-audit":
        manifest, lean_result, validation = LeanEvidenceService(
            platform.registry,
            platform.artifacts,
        ).import_audit(args.audit)
        result = {
            "manifest": manifest.to_dict(),
            "result": lean_result.to_dict(),
            "validation": {
                "research_grade": validation.research_grade,
                "reconciliation_passed": validation.reconciliation_passed,
                "promotable": validation.promotable,
                "failures": list(validation.failures),
                "warnings": list(validation.warnings),
                "checks": dict(validation.checks),
            },
        }
    elif args.command == "import-experiment":
        strategy = _strategy_spec(_json_file(args.strategy_spec))
        platform.registry.register_strategy(strategy)
        report_path = Path(args.report)
        manifest = ExperimentManifest(
            strategy_id=strategy.strategy_id,
            dataset_id=args.dataset_id,
            feature_set_id=args.feature_set_id,
            parameter_set=_json_argument(args.parameters),
            engine=EngineKind(args.engine),
            code_hash=sha256_file(report_path),
            runtime_environment_id=runtime_environment_id(ROOT / "requirements.txt"),
            random_seed=args.seed,
            started_at=utc_now(),
            preregistered=False,
            hypothesis=args.hypothesis,
        )
        runner = ExperimentRunner(registry=platform.registry, artifact_store=platform.artifacts)
        experiment_result = runner.run(manifest, strategy=strategy, adapter=LegacyJsonReportAdapter(report_path))
        result = {"manifest": manifest.to_dict(), "result": experiment_result.to_dict()}
    elif args.command == "promote":
        service = PromotionService(platform.registry)
        event = service.promote(
            args.strategy_id,
            PromotionState(args.to_state),
            actor=args.actor,
            reason=args.reason,
            evidence_ids=args.evidence_id,
            metadata=_json_argument(args.metadata),
        )
        result = event.to_dict()
    elif args.command == "prospective-register":
        service = ProspectiveService(platform.registry)
        registration = ProspectiveRegistration(
            strategy_id=args.strategy_id,
            name=args.name,
            configuration=_json_argument(args.configuration),
            minimum_sample=args.minimum_sample,
            acceptance_criteria=_json_argument(args.criteria),
            created_at=utc_now(),
        )
        service.register(registration)
        result = registration.to_dict()
    elif args.command == "prospective-append":
        payload = _json_file(args.observation)
        observation = ProspectiveObservation(
            prospective_test_id=payload["prospective_test_id"],
            signal_id=payload["signal_id"],
            signal_time=_timestamp(payload["signal_time"]),
            available_at=_timestamp(payload["available_at"]),
            symbol=payload["symbol"],
            direction=payload["direction"],
            feature_snapshot_ids=tuple(payload.get("feature_snapshot_ids") or ()),
            contract_candidates=tuple(payload.get("contract_candidates") or ()),
            selected_instrument=payload.get("selected_instrument"),
            simulated_entry=payload.get("simulated_entry"),
            rejection_reasons=tuple(payload.get("rejection_reasons") or ()),
            outcome=payload.get("outcome"),
            metadata=payload.get("metadata") or {},
            observation_id=payload.get("observation_id") or "",
        )
        inserted = ProspectiveService(platform.registry).append(observation)
        result = {"inserted": inserted, "observation": observation.to_dict()}
    elif args.command == "prospective-evaluate":
        result = ProspectiveService(platform.registry).evaluate(
            args.prospective_test_id,
            close_when_minimum_reached=not args.keep_open,
        )
    elif args.command == "reconcile":
        service = EvidenceReconciliationService(registry=platform.registry, artifact_store=platform.artifacts)
        payload, artifact = service.reconcile(
            strategy_id=args.strategy_id,
            fast_experiment_id=args.fast_experiment_id,
            lean_experiment_id=args.lean_experiment_id,
            prospective_test_id=args.prospective_test_id,
            paper_metrics=_json_argument(args.paper_metrics),
        )
        result = {"reconciliation": payload, "artifact": artifact.to_dict()}
    else:  # pragma: no cover
        raise AssertionError(args.command)

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
