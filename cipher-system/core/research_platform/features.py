from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import stable_id
from .models import AllowedUse, FeatureSnapshot, FeatureSpec, utc_now
from .registry import ResearchRegistry


@dataclass(frozen=True)
class ModelArtifactManifest:
    model_name: str
    model_version: str
    implementation_hash: str
    weights_artifact_id: str | None
    training_dataset_id: str | None
    training_cutoff: datetime | None
    validation_plan: Mapping[str, Any]
    allowed_use: AllowedUse
    runtime_requirements: tuple[str, ...]
    status: str
    blockers: tuple[str, ...] = ()
    model_artifact_manifest_id: str = ""

    def __post_init__(self) -> None:
        cutoff = self.training_cutoff
        if cutoff is not None:
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError("training_cutoff must be timezone-aware")
            cutoff = cutoff.astimezone(timezone.utc)
        payload = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "implementation_hash": self.implementation_hash,
            "weights_artifact_id": self.weights_artifact_id,
            "training_dataset_id": self.training_dataset_id,
            "training_cutoff": cutoff.isoformat() if cutoff else None,
            "validation_plan": dict(self.validation_plan),
            "allowed_use": self.allowed_use.value,
            "runtime_requirements": tuple(self.runtime_requirements),
            "status": self.status,
            "blockers": tuple(self.blockers),
        }
        object.__setattr__(self, "training_cutoff", cutoff)
        object.__setattr__(self, "validation_plan", dict(self.validation_plan))
        object.__setattr__(self, "runtime_requirements", tuple(self.runtime_requirements))
        object.__setattr__(self, "blockers", tuple(self.blockers))
        object.__setattr__(
            self,
            "model_artifact_manifest_id",
            self.model_artifact_manifest_id or stable_id("model_manifest", payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_artifact_manifest_id": self.model_artifact_manifest_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "implementation_hash": self.implementation_hash,
            "weights_artifact_id": self.weights_artifact_id,
            "training_dataset_id": self.training_dataset_id,
            "training_cutoff": self.training_cutoff.isoformat() if self.training_cutoff else None,
            "validation_plan": dict(self.validation_plan),
            "allowed_use": self.allowed_use.value,
            "runtime_requirements": list(self.runtime_requirements),
            "status": self.status,
            "blockers": list(self.blockers),
        }


class FeaturePolicyError(RuntimeError):
    pass


class FeatureService:
    def __init__(self, registry: ResearchRegistry, artifact_store: ArtifactStore):
        self.registry = registry
        self.artifact_store = artifact_store

    def register_model_manifest(self, manifest: ModelArtifactManifest) -> ArtifactReference:
        reference = self.artifact_store.put_json(
            manifest.to_dict(),
            metadata={
                "kind": "model_artifact_manifest",
                "model_name": manifest.model_name,
                "model_manifest_id": manifest.model_artifact_manifest_id,
            },
        )
        self.registry.register_artifact(reference.to_dict())
        return reference

    def publish(
        self,
        *,
        feature_id: str,
        symbol: str,
        event_time: datetime,
        computed_at: datetime,
        dataset_id: str,
        value: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> FeatureSnapshot:
        spec = self.registry.get_payload("features", "feature_id", feature_id)
        computed = self._aware(computed_at, "computed_at")
        event = self._aware(event_time, "event_time")
        lag = int(spec.get("availability_lag_seconds") or 0)
        earliest = event + timedelta(seconds=lag)
        available_at = max(computed, earliest)
        cutoff_text = spec.get("training_cutoff")
        if cutoff_text:
            cutoff = datetime.fromisoformat(str(cutoff_text)).astimezone(timezone.utc)
            if event <= cutoff and metadata and metadata.get("evaluation_split") == "holdout":
                raise FeaturePolicyError("holdout feature event overlaps the declared training period")
        snapshot = FeatureSnapshot(
            feature_id=feature_id,
            symbol=symbol,
            event_time=event,
            available_at=available_at,
            value=value,
            dataset_id=dataset_id,
            metadata={
                **dict(metadata or {}),
                "allowed_use": spec["allowed_use"],
                "availability_lag_seconds": lag,
            },
        )
        self.registry.register_feature_snapshot(snapshot)
        return snapshot

    def decision_snapshot(
        self,
        *,
        feature_ids: Iterable[str],
        symbol: str,
        decision_time: datetime,
        requested_use: AllowedUse,
    ) -> dict[str, dict[str, Any]]:
        decision = self._aware(decision_time, "decision_time")
        ids = list(feature_ids)
        for feature_id in ids:
            spec = self.registry.get_payload("features", "feature_id", feature_id)
            allowed = AllowedUse(spec["allowed_use"])
            if self._use_rank(requested_use) > self._use_rank(allowed):
                raise FeaturePolicyError(
                    f"feature {feature_id} is allowed only for {allowed.value}, not {requested_use.value}"
                )
        return self.registry.point_in_time_features(
            feature_ids=ids,
            symbol=symbol,
            decision_time=decision.isoformat(),
        )

    def bootstrap_cipher_model_policies(
        self,
        *,
        kronos_implementation_hash: str,
        timesfm_implementation_hash: str,
    ) -> tuple[FeatureSpec, FeatureSpec]:
        kronos = FeatureSpec(
            name="kronos_candlestick_forecast",
            version="prospective-v1",
            inputs=("market_bars_ohlcv",),
            lookback="locked by preregistration",
            availability_lag_seconds=0,
            missing_value_policy="unavailable; never impute a forecast",
            allowed_use=AllowedUse.CONTEXT,
            implementation_hash=kronos_implementation_hash,
            leakage_checks={
                "prospective_only": True,
                "minimum_scored_before_review": 100,
                "entry_gate_allowed": False,
                "sizing_allowed": False,
            },
            description="Kronos remains context-only until a locked prospective analysis promotes it.",
        )
        timesfm = FeatureSpec(
            name="timesfm_gex_forecast",
            version="blocked-v1",
            inputs=("point_in_time_gex_history",),
            lookback="manifest-defined",
            availability_lag_seconds=0,
            missing_value_policy="blocked without project checkpoint and provenance manifest",
            allowed_use=AllowedUse.CONTEXT,
            implementation_hash=timesfm_implementation_hash,
            leakage_checks={
                "weights_manifest_required": True,
                "training_cutoff_required": True,
                "point_in_time_validation_required": True,
                "currently_blocked": True,
            },
            description="TimesFM may not emit decision features until weights and provenance are valid.",
        )
        self.registry.register_feature(kronos)
        self.registry.register_feature(timesfm)
        return kronos, timesfm

    @staticmethod
    def _use_rank(value: AllowedUse) -> int:
        return {
            AllowedUse.CONTEXT: 0,
            AllowedUse.FILTER: 1,
            AllowedUse.RANKING: 2,
            AllowedUse.SIZING: 3,
            AllowedUse.EXECUTION: 4,
        }[value]

    @staticmethod
    def _aware(value: datetime, field_name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)
