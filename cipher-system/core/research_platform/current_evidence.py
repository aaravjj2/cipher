from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bootstrap import ResearchPlatform
from .models import AuditEvent, utc_now
from .prospective import ProspectiveObservation, ProspectiveRegistration, ProspectiveService
from .registry import RegistryNotFoundError


class CurrentEvidenceImporter:
    """Import existing Cipher evidence without changing its research verdicts."""

    def __init__(self, platform: ResearchPlatform):
        self.platform = platform

    def import_all(
        self,
        *,
        runtime_root: str | Path = "/home/aarav/Aarav/cipher-system/CipherCapture",
    ) -> dict[str, Any]:
        root = Path(runtime_root)
        return {
            "cluster_kronos": self.import_cluster_kronos(root),
            "cluster_exit_profiles": self.import_cluster_exit_profiles(root),
            "options_research_status": self.import_json_evidence(
                self.platform.config.repository_root / "cipher-system" / "data" / "OPTIONS_RESEARCH_STATUS.json",
                kind="authoritative_options_research_status",
            ),
            "quantconnect_status": self.import_json_evidence(
                self.platform.config.repository_root
                / "cipher-system"
                / "data"
                / "quantconnect"
                / "quantconnect_execution_status.json",
                kind="quantconnect_execution_status",
            ),
        }

    def import_cluster_kronos(self, runtime_root: Path) -> dict[str, Any]:
        report_path = runtime_root / "data" / "cluster_kronos_forward" / "latest_cluster_kronos_forward.json"
        database_path = runtime_root / "data" / "cluster_kronos_forward" / "cluster_kronos_forward.sqlite"
        if not report_path.exists() or not database_path.exists():
            return {"status": "unavailable", "report": str(report_path), "database": str(database_path)}
        report = json.loads(report_path.read_text(encoding="utf-8"))
        prereg = dict(report.get("pre_registration") or {})
        config_id = str(prereg.get("config_id") or "")
        if not config_id:
            raise ValueError("Cluster/Kronos report does not contain a preregistered config_id")
        strategy_id = self._strategy_id("cluster_directional_debit_spread_shadow")
        feature_id = self._feature_id("kronos_candlestick_forecast")
        report_artifact = self.platform.artifacts.put_file(
            report_path,
            content_type="application/json",
            metadata={"kind": "cluster_kronos_forward_report", "config_id": config_id},
        )
        self.platform.registry.register_artifact(report_artifact.to_dict())
        cutoff = _timestamp(report.get("generated_at") or report_path.stat().st_mtime)
        dataset, profile, _, profile_artifact = self.platform.datasets.freeze_sqlite(
            database_path,
            dataset_name=f"cluster_kronos_forward_{config_id}",
            source_name="cipher_cluster_kronos_forward",
            availability_cutoff=cutoff,
            symbol_universe_id="cluster_top_ranked_capture_universe",
            corporate_action_version="not_applicable_intraday_observed_prices",
            normalizer_version="cluster_kronos_forward_schema_v1",
            schema_name="cluster_kronos_forward_sqlite_v1",
            request_metadata={
                "config_id": config_id,
                "source_report_artifact_id": report_artifact.artifact_id,
                "context_only": True,
            },
        )
        registration = ProspectiveRegistration(
            strategy_id=strategy_id,
            name=f"Cluster + Kronos context-only {config_id}",
            configuration=prereg,
            minimum_sample=int(prereg.get("minimum_scored_sample") or 100),
            acceptance_criteria={
                "manual_review_required": True,
                "locked_analysis_required": True,
                "automatic_strategy_gate": False,
                "automatic_sizing_change": False,
                "decision_rule": prereg.get("decision_rule") or report.get("deployment_rule"),
            },
            created_at=_timestamp(prereg.get("registered_at") or report.get("generated_at")),
        )
        service = ProspectiveService(self.platform.registry)
        service.register(registration)
        inserted = 0
        duplicates = 0
        feature_snapshots = 0
        with self.platform.registry.connect() as registry_db:
            existing_signal_ids = {
                str(json.loads(row["payload_json"]).get("signal_id"))
                for row in registry_db.execute(
                    "select payload_json from prospective_observations where prospective_test_id = ?",
                    (registration.registration_id,),
                ).fetchall()
            }
        source_uri = f"file:{database_path.as_posix()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True, timeout=30) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                select p.*, o.scored_at, o.horizon_end_at, o.actual_close,
                       o.actual_return_pct, o.cluster_directional_return_pct,
                       o.cluster_direction_positive, o.kronos_correct,
                       o.payload_json as outcome_payload_json
                from predictions p
                left join outcomes o on o.prediction_id = p.id
                where p.config_id = ? and p.prospective_eligible = 1
                order by p.captured_at, p.rank, p.ticker
                """,
                (config_id,),
            ).fetchall()
        for row in rows:
            prediction = dict(row)
            if str(prediction["id"]) in existing_signal_ids:
                duplicates += 1
                continue
            captured_at = _timestamp(prediction["captured_at"])
            generated_at = _timestamp(prediction["generated_at"])
            snapshot_ids: list[str] = []
            if prediction.get("pred_return_pct") is not None:
                snapshot = self.platform.features.publish(
                    feature_id=feature_id,
                    symbol=str(prediction["ticker"]),
                    event_time=captured_at,
                    computed_at=generated_at,
                    dataset_id=dataset.dataset_id,
                    value={
                        "predicted_close": prediction.get("pred_close"),
                        "predicted_return_pct": prediction.get("pred_return_pct"),
                        "direction": prediction.get("kronos_direction"),
                    },
                    metadata={
                        "config_id": config_id,
                        "prediction_id": prediction["id"],
                        "evaluation_group": prediction.get("evaluation_group"),
                        "generation_delay_seconds": prediction.get("generation_delay_seconds"),
                        "prospective_eligible": True,
                        "source": "existing_cluster_kronos_forward_runtime",
                    },
                )
                snapshot_ids.append(snapshot.snapshot_id)
                feature_snapshots += 1
            outcome = None
            if prediction.get("cluster_directional_return_pct") is not None:
                outcome = {
                    "return_pct": float(prediction["cluster_directional_return_pct"]),
                    "actual_return_pct": prediction.get("actual_return_pct"),
                    "actual_close": prediction.get("actual_close"),
                    "horizon_end_at": prediction.get("horizon_end_at"),
                    "cluster_direction_positive": bool(prediction.get("cluster_direction_positive")),
                    "kronos_correct": None
                    if prediction.get("kronos_correct") is None
                    else bool(prediction.get("kronos_correct")),
                    "scored_at": prediction.get("scored_at"),
                }
            observation = ProspectiveObservation(
                prospective_test_id=registration.registration_id,
                signal_id=str(prediction["id"]),
                signal_time=captured_at,
                available_at=generated_at,
                symbol=str(prediction["ticker"]),
                direction=str(prediction["cluster_direction"]),
                feature_snapshot_ids=tuple(snapshot_ids),
                contract_candidates=(),
                selected_instrument=None,
                simulated_entry=None,
                rejection_reasons=(),
                outcome=outcome,
                metadata={
                    "config_id": config_id,
                    "rank": prediction.get("rank"),
                    "setup": prediction.get("setup"),
                    "spot": prediction.get("spot"),
                    "target": prediction.get("target"),
                    "strength": prediction.get("strength"),
                    "reference_close": prediction.get("reference_close"),
                    "evaluation_group": prediction.get("evaluation_group"),
                    "generation_delay_seconds": prediction.get("generation_delay_seconds"),
                    "capture_file": prediction.get("capture_file"),
                    "context_only": True,
                    "instrument_evaluation": False,
                },
            )
            if service.append(observation):
                inserted += 1
            else:
                duplicates += 1
        evaluation = service.evaluate(registration.registration_id, close_when_minimum_reached=True)
        self.platform.registry.audit(
            AuditEvent(
                event_type="CURRENT_CLUSTER_KRONOS_IMPORTED",
                entity_type="prospective_test",
                entity_id=registration.registration_id,
                occurred_at=utc_now(),
                payload={
                    "config_id": config_id,
                    "dataset_id": dataset.dataset_id,
                    "report_artifact_id": report_artifact.artifact_id,
                    "profile_artifact_id": profile_artifact.artifact_id,
                    "inserted_observations": inserted,
                    "duplicate_observations": duplicates,
                    "deployment_rule": report.get("deployment_rule"),
                },
            )
        )
        return {
            "status": "imported",
            "strategy_id": strategy_id,
            "feature_id": feature_id,
            "config_id": config_id,
            "dataset_id": dataset.dataset_id,
            "report_artifact_id": report_artifact.artifact_id,
            "profile_artifact_id": profile_artifact.artifact_id,
            "prospective_test_id": registration.registration_id,
            "eligible_rows": len(rows),
            "inserted_observations": inserted,
            "duplicate_observations": duplicates,
            "feature_snapshots_processed": feature_snapshots,
            "evaluation": evaluation,
            "source_profile": profile.to_dict(),
        }

    def import_cluster_exit_profiles(self, runtime_root: Path) -> dict[str, Any]:
        report_path = runtime_root / "data" / "cluster_forward_tests" / "latest_cluster_forward_test.json"
        if not report_path.exists():
            return {"status": "unavailable", "path": str(report_path)}
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        artifact = self.platform.artifacts.put_file(
            report_path,
            content_type="application/json",
            metadata={
                "kind": "cluster_exit_profile_forward_report",
                "closed_positions": payload.get("closed_positions"),
                "open_positions": payload.get("open_positions"),
            },
        )
        self.platform.registry.register_artifact(artifact.to_dict())
        strategy_id = self._strategy_id("cluster_directional_debit_spread_shadow")
        self.platform.registry.audit(
            AuditEvent(
                event_type="CURRENT_CLUSTER_EXIT_PROFILES_IMPORTED",
                entity_type="strategy",
                entity_id=strategy_id,
                occurred_at=utc_now(),
                payload={
                    "artifact_id": artifact.artifact_id,
                    "updated_at": payload.get("updated_at"),
                    "closed_positions": payload.get("closed_positions"),
                    "open_positions": payload.get("open_positions"),
                    "research_only": True,
                },
            )
        )
        return {
            "status": "imported",
            "strategy_id": strategy_id,
            "artifact_id": artifact.artifact_id,
            "updated_at": payload.get("updated_at"),
            "closed_positions": payload.get("closed_positions"),
            "open_positions": payload.get("open_positions"),
            "by_profile": payload.get("by_profile"),
        }

    def import_json_evidence(self, path: str | Path, *, kind: str) -> dict[str, Any]:
        candidate = Path(path)
        if not candidate.exists():
            return {"status": "unavailable", "path": str(candidate), "kind": kind}
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        artifact = self.platform.artifacts.put_file(
            candidate,
            content_type="application/json",
            metadata={"kind": kind},
        )
        self.platform.registry.register_artifact(artifact.to_dict())
        entity_id = str(payload.get("status") or artifact.artifact_id)
        self.platform.registry.audit(
            AuditEvent(
                event_type="CURRENT_RESEARCH_EVIDENCE_IMPORTED",
                entity_type="research_evidence",
                entity_id=entity_id,
                occurred_at=utc_now(),
                payload={
                    "kind": kind,
                    "artifact_id": artifact.artifact_id,
                    "status": payload.get("status"),
                    "research_claims_allowed": payload.get("research_claims_allowed"),
                    "live_deployment_authorized": payload.get("live_deployment_authorized"),
                },
            )
        )
        return {
            "status": "imported",
            "kind": kind,
            "artifact_id": artifact.artifact_id,
            "source_status": payload.get("status"),
            "research_claims_allowed": payload.get("research_claims_allowed"),
            "live_deployment_authorized": payload.get("live_deployment_authorized"),
        }

    def _strategy_id(self, name: str) -> str:
        with self.platform.registry.connect() as db:
            row = db.execute(
                "select strategy_id from strategies where name = ? order by created_at limit 1",
                (name,),
            ).fetchone()
        if not row:
            raise RegistryNotFoundError(name)
        return str(row["strategy_id"])

    def _feature_id(self, name: str) -> str:
        with self.platform.registry.connect() as db:
            row = db.execute(
                "select feature_id from features where name = ? order by version limit 1",
                (name,),
            ).fetchone()
        if not row:
            raise RegistryNotFoundError(name)
        return str(row["feature_id"])


def _timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp lacks timezone: {value}")
    return parsed.astimezone(timezone.utc)
