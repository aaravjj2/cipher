from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.research_platform.artifact_store import ArtifactStore
from core.research_platform.models import (
    AllowedUse,
    DataDisposition,
    DatasetManifest,
    FeatureSnapshot,
    FeatureSpec,
    PromotionState,
    RawObjectManifest,
    StrategySpec,
)
from core.research_platform.promotion import PromotionBlockedError, PromotionService
from core.research_platform.registry import RegistryConflictError, ResearchRegistry

NOW = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)


def raw_manifest(uri: str = "file:///tmp/raw.json") -> RawObjectManifest:
    return RawObjectManifest(
        source="test_source",
        dataset="test_dataset",
        uri=uri,
        checksum="a" * 64,
        checksum_method="sha256",
        size_bytes=10,
        received_at=NOW,
        available_at=NOW,
        ingestion_run_id="ingest_test",
        content_type="application/json",
        disposition=DataDisposition.IMMUTABLE_RAW,
    )


def dataset_manifest(raw_id: str, *, passed: bool = True) -> DatasetManifest:
    return DatasetManifest(
        name="frozen_test",
        created_at=NOW + timedelta(minutes=1),
        availability_cutoff=NOW,
        sources=("test_source",),
        raw_object_ids=(raw_id,),
        symbol_universe_id="universe_test",
        corporate_action_version="ca_test",
        normalizer_version="normalizer_test",
        schema_name="test_v1",
        row_counts={"rows": 1},
        quality_checks={"passed": passed, "failures": [] if passed else ["bad"]},
        frozen=True,
    )


def feature_spec() -> FeatureSpec:
    return FeatureSpec(
        name="momentum",
        version="v1",
        inputs=("market_bars",),
        lookback="20 sessions",
        availability_lag_seconds=60,
        missing_value_policy="unavailable",
        allowed_use=AllowedUse.FILTER,
        implementation_hash="b" * 64,
        leakage_checks={"point_in_time": True},
    )


def strategy_spec(feature_id: str = "") -> StrategySpec:
    return StrategySpec(
        name="test_strategy",
        version="v1",
        signal_rule={"rule": "test"},
        instrument_rule={"structure": "debit_spread"},
        contract_selection_rule={"dte": [7, 14]},
        entry_rule={"timing": "next_bar"},
        exit_rule={"maximum_hold": 5},
        sizing_rule={"quantity": 1},
        portfolio_constraints={"maximum_positions": 1},
        required_feature_ids=(feature_id,) if feature_id else (),
        fill_model={"entry": "ask", "exit": "bid"},
        benchmark="SPY",
        statistical_plan={"holdout": True},
        promotion_thresholds={"minimum_trades": 5},
    )


def test_content_ids_are_stable_and_timezones_are_required():
    first = raw_manifest()
    second = raw_manifest()
    assert first.raw_object_id == second.raw_object_id
    with pytest.raises(ValueError, match="timezone-aware"):
        RawObjectManifest(
            source="x",
            dataset="y",
            uri="file:///x",
            checksum="c" * 64,
            checksum_method="sha256",
            size_bytes=1,
            received_at=datetime(2026, 1, 1),
            available_at=NOW,
            ingestion_run_id="run",
        )


def test_artifact_store_is_content_addressed_and_verified(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts")
    first = store.put_json({"b": 2, "a": 1})
    second = store.put_json({"a": 1, "b": 2})
    assert first.sha256 == second.sha256
    assert first.artifact_id == second.artifact_id
    assert store.verify(first.sha256)


def test_artifact_registration_tolerates_locator_drift_for_identical_content(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    reference = ArtifactStore(tmp_path / "first" / "artifacts").put_json({"same": "bytes"})
    assert registry.register_artifact(reference.to_dict())

    relocated = {
        **reference.to_dict(),
        "data_path": str(tmp_path / "mounted-runtime" / reference.sha256),
        "metadata_path": str(tmp_path / "mounted-runtime" / f"{reference.sha256}.metadata.json"),
    }

    assert not registry.register_artifact(relocated)


def test_registry_is_immutable_and_point_in_time(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    raw = raw_manifest()
    assert registry.register_raw_object(raw)
    assert not registry.register_raw_object(raw)
    dataset = dataset_manifest(raw.raw_object_id)
    registry.register_dataset(dataset)
    feature = feature_spec()
    registry.register_feature(feature)
    before = FeatureSnapshot(
        feature_id=feature.feature_id,
        symbol="spy",
        event_time=NOW,
        available_at=NOW + timedelta(minutes=1),
        value=1.0,
        dataset_id=dataset.dataset_id,
    )
    after = FeatureSnapshot(
        feature_id=feature.feature_id,
        symbol="SPY",
        event_time=NOW + timedelta(minutes=2),
        available_at=NOW + timedelta(minutes=3),
        value=2.0,
        dataset_id=dataset.dataset_id,
    )
    registry.register_feature_snapshot(before)
    registry.register_feature_snapshot(after)
    values = registry.point_in_time_features(
        feature_ids=[feature.feature_id],
        symbol="SPY",
        decision_time=(NOW + timedelta(minutes=2)).isoformat(),
    )
    assert values[feature.feature_id]["value"] == 1.0

    changed = RawObjectManifest(
        **{
            **raw.__dict__,
            "request_metadata": {"different": True},
            "raw_object_id": raw.raw_object_id,
        }
    )
    with pytest.raises(RegistryConflictError):
        registry.register_raw_object(changed)


def test_promotion_requires_declared_dataset_quality(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    raw = raw_manifest()
    registry.register_raw_object(raw)
    failed_dataset = dataset_manifest(raw.raw_object_id, passed=False)
    registry.register_dataset(failed_dataset)
    strategy = strategy_spec()
    registry.register_strategy(strategy)
    service = PromotionService(registry)
    service.promote(strategy.strategy_id, PromotionState.SPECIFIED, actor="test", reason="fully specified")
    with pytest.raises(PromotionBlockedError, match="quality checks"):
        service.promote(
            strategy.strategy_id,
            PromotionState.DATA_VALIDATED,
            actor="test",
            reason="attempt",
            metadata={"dataset_id": failed_dataset.dataset_id},
        )
    assert registry.current_state(strategy.strategy_id) == PromotionState.SPECIFIED


def test_registry_requires_registered_features_for_strategy(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    strategy = strategy_spec("feature_missing")
    with pytest.raises(KeyError):
        registry.register_strategy(strategy)
