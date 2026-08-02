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
    "RawObjectManifest",
    "ResearchPlatformConfig",
    "ResearchRegistry",
    "ExperimentRunner",
    "FeatureService",
    "PromotionService",
    "ProspectiveService",
    "StandardBacktestOutput",
    "StrategySpec",
]
