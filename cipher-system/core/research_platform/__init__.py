"""Cipher research governance and strategy-graduation platform.

This package is deliberately broker-agnostic and contains no live-order path.
It standardizes evidence, point-in-time datasets, experiments, promotions,
prospective validation, deterministic risk review, and reconciliation.
"""

from .artifact_store import ArtifactReference, ArtifactStore
from .config import ResearchPlatformConfig
from .experiments import ExperimentRunner, StandardBacktestOutput
from .features import FeatureService
from .promotion import PromotionService
from .prospective import ProspectiveService
from .reference_volume import (
    REFERENCE_ALLOWED_USE,
    ReferenceImportPolicy,
    ReferenceSessionSummary,
    RegularSessionSpec,
    reconcile_session_volume,
    summarize_reference_rows,
)
from .registry import ResearchRegistry
from .models import (
    AllowedUse,
    AuditEvent,
    DataDisposition,
    DatasetManifest,
    EngineKind,
    ExperimentManifest,
    ExperimentResult,
    ExperimentVerdict,
    FeatureSnapshot,
    FeatureSpec,
    PromotionEvent,
    PromotionState,
    RawObjectManifest,
    StrategySpec,
)

__all__ = [
    "AllowedUse",
    "ArtifactReference",
    "ArtifactStore",
    "AuditEvent",
    "DataDisposition",
    "DatasetManifest",
    "EngineKind",
    "ExperimentManifest",
    "ExperimentResult",
    "ExperimentVerdict",
    "FeatureSnapshot",
    "FeatureSpec",
    "PromotionEvent",
    "PromotionState",
    "REFERENCE_ALLOWED_USE",
    "RawObjectManifest",
    "ReferenceImportPolicy",
    "ReferenceSessionSummary",
    "RegularSessionSpec",
    "ResearchPlatformConfig",
    "ResearchRegistry",
    "ExperimentRunner",
    "FeatureService",
    "PromotionService",
    "ProspectiveService",
    "reconcile_session_volume",
    "StandardBacktestOutput",
    "summarize_reference_rows",
    "StrategySpec",
]
