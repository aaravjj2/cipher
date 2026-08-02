from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .hashing import stable_id


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _non_empty(value: str, field_name: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{field_name} must be non-empty")
    return result


def _plain_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


class AllowedUse(str, Enum):
    CONTEXT = "context"
    FILTER = "filter"
    RANKING = "ranking"
    SIZING = "sizing"
    EXECUTION = "execution"


class PromotionState(str, Enum):
    IDEA = "IDEA"
    SPECIFIED = "SPECIFIED"
    DATA_VALIDATED = "DATA_VALIDATED"
    FAST_BACKTESTED = "FAST_BACKTESTED"
    WALK_FORWARD_PASSED = "WALK_FORWARD_PASSED"
    LEAN_REPLICATED = "LEAN_REPLICATED"
    PROSPECTIVE_SHADOW = "PROSPECTIVE_SHADOW"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    LIVE_REVIEW_REQUIRED = "LIVE_REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class ExperimentVerdict(str, Enum):
    PASS = "PASS"
    CONDITIONAL_PASS = "CONDITIONAL_PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class EngineKind(str, Enum):
    CIPHER_FAST = "cipher_fast"
    LEAN = "lean"
    PROSPECTIVE = "prospective"
    PAPER = "paper"
    IMPORTED = "imported"


class DataDisposition(str, Enum):
    IMMUTABLE_RAW = "immutable_raw"
    FROZEN_SNAPSHOT = "frozen_snapshot"
    MUTABLE_OPERATIONAL = "mutable_operational"
    DERIVED_RESEARCH = "derived_research"


@dataclass(frozen=True)
class RawObjectManifest:
    source: str
    dataset: str
    uri: str
    checksum: str
    checksum_method: str
    size_bytes: int
    received_at: datetime
    available_at: datetime
    ingestion_run_id: str
    content_type: str = "application/octet-stream"
    event_time_start: datetime | None = None
    event_time_end: datetime | None = None
    request_metadata: Mapping[str, Any] = field(default_factory=dict)
    disposition: DataDisposition = DataDisposition.IMMUTABLE_RAW
    schema_version: int = 1
    raw_object_id: str = ""

    def __post_init__(self) -> None:
        source = _non_empty(self.source, "source")
        dataset = _non_empty(self.dataset, "dataset")
        uri = _non_empty(self.uri, "uri")
        checksum = _non_empty(self.checksum, "checksum")
        checksum_method = _non_empty(self.checksum_method, "checksum_method")
        ingestion_run_id = _non_empty(self.ingestion_run_id, "ingestion_run_id")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")
        if self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        received_at = _require_aware(self.received_at, "received_at")
        available_at = _require_aware(self.available_at, "available_at")
        if available_at < received_at:
            raise ValueError("available_at cannot precede received_at")
        start = _require_aware(self.event_time_start, "event_time_start") if self.event_time_start else None
        end = _require_aware(self.event_time_end, "event_time_end") if self.event_time_end else None
        if start and end and end < start:
            raise ValueError("event_time_end cannot precede event_time_start")
        payload = {
            "source": source,
            "dataset": dataset,
            "uri": uri,
            "checksum": checksum,
            "checksum_method": checksum_method,
            "size_bytes": self.size_bytes,
            "received_at": received_at.isoformat(),
            "available_at": available_at.isoformat(),
            "ingestion_run_id": ingestion_run_id,
            "content_type": self.content_type,
            "event_time_start": start.isoformat() if start else None,
            "event_time_end": end.isoformat() if end else None,
            "request_metadata": _plain_mapping(self.request_metadata),
            "disposition": self.disposition.value,
            "schema_version": self.schema_version,
        }
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "dataset", dataset)
        object.__setattr__(self, "uri", uri)
        object.__setattr__(self, "checksum", checksum)
        object.__setattr__(self, "checksum_method", checksum_method)
        object.__setattr__(self, "ingestion_run_id", ingestion_run_id)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "event_time_start", start)
        object.__setattr__(self, "event_time_end", end)
        object.__setattr__(self, "request_metadata", _plain_mapping(self.request_metadata))
        object.__setattr__(self, "raw_object_id", self.raw_object_id or stable_id("raw", payload))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class DatasetManifest:
    name: str
    created_at: datetime
    availability_cutoff: datetime
    sources: Sequence[str]
    raw_object_ids: Sequence[str]
    symbol_universe_id: str
    corporate_action_version: str
    normalizer_version: str
    schema_name: str
    row_counts: Mapping[str, int]
    quality_checks: Mapping[str, Any]
    frozen: bool = True
    schema_version: int = 1
    dataset_id: str = ""

    def __post_init__(self) -> None:
        name = _non_empty(self.name, "name")
        created_at = _require_aware(self.created_at, "created_at")
        cutoff = _require_aware(self.availability_cutoff, "availability_cutoff")
        if cutoff > created_at:
            raise ValueError("availability_cutoff cannot exceed created_at")
        sources = tuple(sorted({_non_empty(v, "sources item") for v in self.sources}))
        raw_ids = tuple(sorted({_non_empty(v, "raw_object_ids item") for v in self.raw_object_ids}))
        if not sources:
            raise ValueError("sources cannot be empty")
        if self.frozen and not raw_ids:
            raise ValueError("frozen datasets require at least one raw object")
        counts = {str(k): int(v) for k, v in self.row_counts.items()}
        if any(value < 0 for value in counts.values()):
            raise ValueError("row counts cannot be negative")
        payload = {
            "name": name,
            "created_at": created_at.isoformat(),
            "availability_cutoff": cutoff.isoformat(),
            "sources": sources,
            "raw_object_ids": raw_ids,
            "symbol_universe_id": _non_empty(self.symbol_universe_id, "symbol_universe_id"),
            "corporate_action_version": _non_empty(self.corporate_action_version, "corporate_action_version"),
            "normalizer_version": _non_empty(self.normalizer_version, "normalizer_version"),
            "schema_name": _non_empty(self.schema_name, "schema_name"),
            "row_counts": counts,
            "quality_checks": _plain_mapping(self.quality_checks),
            "frozen": bool(self.frozen),
            "schema_version": self.schema_version,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "availability_cutoff", cutoff)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "raw_object_ids", raw_ids)
        object.__setattr__(self, "row_counts", counts)
        object.__setattr__(self, "quality_checks", _plain_mapping(self.quality_checks))
        object.__setattr__(self, "dataset_id", self.dataset_id or stable_id("ds", payload))

    @property
    def quality_passed(self) -> bool:
        explicit = self.quality_checks.get("passed")
        if explicit is not None:
            return bool(explicit)
        failures = self.quality_checks.get("failures") or []
        return not failures

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    version: str
    inputs: Sequence[str]
    lookback: str
    availability_lag_seconds: int
    missing_value_policy: str
    allowed_use: AllowedUse
    implementation_hash: str
    training_cutoff: datetime | None = None
    model_artifact_id: str | None = None
    leakage_checks: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    feature_id: str = ""

    def __post_init__(self) -> None:
        if self.availability_lag_seconds < 0:
            raise ValueError("availability_lag_seconds cannot be negative")
        cutoff = _require_aware(self.training_cutoff, "training_cutoff") if self.training_cutoff else None
        inputs = tuple(sorted({_non_empty(v, "inputs item") for v in self.inputs}))
        if not inputs:
            raise ValueError("inputs cannot be empty")
        payload = {
            "name": _non_empty(self.name, "name"),
            "version": _non_empty(self.version, "version"),
            "inputs": inputs,
            "lookback": _non_empty(self.lookback, "lookback"),
            "availability_lag_seconds": self.availability_lag_seconds,
            "missing_value_policy": _non_empty(self.missing_value_policy, "missing_value_policy"),
            "allowed_use": self.allowed_use.value,
            "implementation_hash": _non_empty(self.implementation_hash, "implementation_hash"),
            "training_cutoff": cutoff.isoformat() if cutoff else None,
            "model_artifact_id": self.model_artifact_id,
            "leakage_checks": _plain_mapping(self.leakage_checks),
            "description": self.description.strip(),
        }
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "training_cutoff", cutoff)
        object.__setattr__(self, "leakage_checks", _plain_mapping(self.leakage_checks))
        object.__setattr__(self, "feature_id", self.feature_id or stable_id("feature", payload))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class FeatureSnapshot:
    feature_id: str
    symbol: str
    event_time: datetime
    available_at: datetime
    value: Any
    dataset_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    snapshot_id: str = ""

    def __post_init__(self) -> None:
        event_time = _require_aware(self.event_time, "event_time")
        available_at = _require_aware(self.available_at, "available_at")
        if available_at < event_time:
            raise ValueError("available_at cannot precede event_time")
        payload = {
            "feature_id": _non_empty(self.feature_id, "feature_id"),
            "symbol": _non_empty(self.symbol, "symbol").upper(),
            "event_time": event_time.isoformat(),
            "available_at": available_at.isoformat(),
            "value": self.value,
            "dataset_id": _non_empty(self.dataset_id, "dataset_id"),
            "metadata": _plain_mapping(self.metadata),
        }
        object.__setattr__(self, "symbol", payload["symbol"])
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "metadata", _plain_mapping(self.metadata))
        object.__setattr__(self, "snapshot_id", self.snapshot_id or stable_id("fs", payload))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class StrategySpec:
    name: str
    version: str
    signal_rule: Mapping[str, Any]
    instrument_rule: Mapping[str, Any]
    contract_selection_rule: Mapping[str, Any]
    entry_rule: Mapping[str, Any]
    exit_rule: Mapping[str, Any]
    sizing_rule: Mapping[str, Any]
    portfolio_constraints: Mapping[str, Any]
    required_feature_ids: Sequence[str]
    fill_model: Mapping[str, Any]
    benchmark: str
    statistical_plan: Mapping[str, Any]
    promotion_thresholds: Mapping[str, Any]
    description: str = ""
    strategy_id: str = ""

    def __post_init__(self) -> None:
        required = tuple(sorted({_non_empty(v, "required_feature_ids item") for v in self.required_feature_ids}))
        payload = {
            "name": _non_empty(self.name, "name"),
            "version": _non_empty(self.version, "version"),
            "signal_rule": _plain_mapping(self.signal_rule),
            "instrument_rule": _plain_mapping(self.instrument_rule),
            "contract_selection_rule": _plain_mapping(self.contract_selection_rule),
            "entry_rule": _plain_mapping(self.entry_rule),
            "exit_rule": _plain_mapping(self.exit_rule),
            "sizing_rule": _plain_mapping(self.sizing_rule),
            "portfolio_constraints": _plain_mapping(self.portfolio_constraints),
            "required_feature_ids": required,
            "fill_model": _plain_mapping(self.fill_model),
            "benchmark": _non_empty(self.benchmark, "benchmark"),
            "statistical_plan": _plain_mapping(self.statistical_plan),
            "promotion_thresholds": _plain_mapping(self.promotion_thresholds),
            "description": self.description.strip(),
        }
        for key in (
            "signal_rule",
            "instrument_rule",
            "contract_selection_rule",
            "entry_rule",
            "exit_rule",
            "sizing_rule",
            "portfolio_constraints",
            "fill_model",
            "statistical_plan",
            "promotion_thresholds",
        ):
            object.__setattr__(self, key, payload[key])
        object.__setattr__(self, "required_feature_ids", required)
        object.__setattr__(self, "strategy_id", self.strategy_id or stable_id("strategy", payload))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class ExperimentManifest:
    strategy_id: str
    dataset_id: str
    feature_set_id: str
    parameter_set: Mapping[str, Any]
    engine: EngineKind
    code_hash: str
    runtime_environment_id: str
    random_seed: int
    started_at: datetime
    preregistered: bool = True
    hypothesis: str = ""
    parent_experiment_id: str | None = None
    experiment_id: str = ""

    def __post_init__(self) -> None:
        started = _require_aware(self.started_at, "started_at")
        payload = {
            "strategy_id": _non_empty(self.strategy_id, "strategy_id"),
            "dataset_id": _non_empty(self.dataset_id, "dataset_id"),
            "feature_set_id": _non_empty(self.feature_set_id, "feature_set_id"),
            "parameter_set": _plain_mapping(self.parameter_set),
            "engine": self.engine.value,
            "code_hash": _non_empty(self.code_hash, "code_hash"),
            "runtime_environment_id": _non_empty(self.runtime_environment_id, "runtime_environment_id"),
            "random_seed": int(self.random_seed),
            "started_at": started.isoformat(),
            "preregistered": bool(self.preregistered),
            "hypothesis": self.hypothesis.strip(),
            "parent_experiment_id": self.parent_experiment_id,
        }
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "parameter_set", _plain_mapping(self.parameter_set))
        object.__setattr__(self, "experiment_id", self.experiment_id or stable_id("exp", payload))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    completed_at: datetime
    verdict: ExperimentVerdict
    metrics: Mapping[str, Any]
    statistical_tests: Mapping[str, Any]
    exclusions: Sequence[Mapping[str, Any]]
    quality_checks: Mapping[str, Any]
    artifacts: Mapping[str, str]
    notes: Sequence[str] = ()
    result_id: str = ""

    def __post_init__(self) -> None:
        completed = _require_aware(self.completed_at, "completed_at")
        exclusions = tuple(dict(item) for item in self.exclusions)
        notes = tuple(str(item) for item in self.notes)
        payload = {
            "experiment_id": _non_empty(self.experiment_id, "experiment_id"),
            "completed_at": completed.isoformat(),
            "verdict": self.verdict.value,
            "metrics": _plain_mapping(self.metrics),
            "statistical_tests": _plain_mapping(self.statistical_tests),
            "exclusions": exclusions,
            "quality_checks": _plain_mapping(self.quality_checks),
            "artifacts": dict(self.artifacts),
            "notes": notes,
        }
        object.__setattr__(self, "completed_at", completed)
        object.__setattr__(self, "metrics", _plain_mapping(self.metrics))
        object.__setattr__(self, "statistical_tests", _plain_mapping(self.statistical_tests))
        object.__setattr__(self, "exclusions", exclusions)
        object.__setattr__(self, "quality_checks", _plain_mapping(self.quality_checks))
        object.__setattr__(self, "artifacts", dict(self.artifacts))
        object.__setattr__(self, "notes", notes)
        object.__setattr__(self, "result_id", self.result_id or stable_id("result", payload))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class PromotionEvent:
    strategy_id: str
    from_state: PromotionState
    to_state: PromotionState
    decided_at: datetime
    actor: str
    reason: str
    evidence_ids: Sequence[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = ""

    def __post_init__(self) -> None:
        decided = _require_aware(self.decided_at, "decided_at")
        evidence = tuple(sorted({_non_empty(v, "evidence_ids item") for v in self.evidence_ids}))
        payload = {
            "strategy_id": _non_empty(self.strategy_id, "strategy_id"),
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "decided_at": decided.isoformat(),
            "actor": _non_empty(self.actor, "actor"),
            "reason": _non_empty(self.reason, "reason"),
            "evidence_ids": evidence,
            "metadata": _plain_mapping(self.metadata),
        }
        object.__setattr__(self, "decided_at", decided)
        object.__setattr__(self, "evidence_ids", evidence)
        object.__setattr__(self, "metadata", _plain_mapping(self.metadata))
        object.__setattr__(self, "event_id", self.event_id or stable_id("promotion", payload))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    entity_type: str
    entity_id: str
    occurred_at: datetime
    payload: Mapping[str, Any]
    actor: str = "system"
    event_id: str = ""

    def __post_init__(self) -> None:
        occurred = _require_aware(self.occurred_at, "occurred_at")
        payload = {
            "event_type": _non_empty(self.event_type, "event_type"),
            "entity_type": _non_empty(self.entity_type, "entity_type"),
            "entity_id": _non_empty(self.entity_id, "entity_id"),
            "occurred_at": occurred.isoformat(),
            "payload": _plain_mapping(self.payload),
            "actor": _non_empty(self.actor, "actor"),
        }
        object.__setattr__(self, "occurred_at", occurred)
        object.__setattr__(self, "payload", _plain_mapping(self.payload))
        object.__setattr__(self, "event_id", self.event_id or stable_id("audit", payload))

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(v) for v in value]
    return value
