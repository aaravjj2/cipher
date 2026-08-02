from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import stable_id
from .models import AuditEvent, utc_now
from .registry import ResearchRegistry

FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "order",
        "orders",
        "order_intent",
        "broker_action",
        "quantity",
        "contracts",
        "position_size",
        "portfolio_weight",
        "override",
        "trade_decision",
        "submit",
        "execute",
    }
)


@dataclass(frozen=True)
class ContextInput:
    strategy_id: str
    signal_id: str
    symbol: str
    decision_time: datetime
    deterministic_signal: Mapping[str, Any]
    feature_snapshots: Sequence[Mapping[str, Any]]
    news_events: Sequence[Mapping[str, Any]]
    anomaly_events: Sequence[Mapping[str, Any]]
    risk_state: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.decision_time.tzinfo is None or self.decision_time.utcoffset() is None:
            raise ValueError("decision_time must be timezone-aware")
        object.__setattr__(self, "decision_time", self.decision_time.astimezone(timezone.utc))
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "deterministic_signal", dict(self.deterministic_signal))
        object.__setattr__(self, "feature_snapshots", tuple(dict(item) for item in self.feature_snapshots))
        object.__setattr__(self, "news_events", tuple(dict(item) for item in self.news_events))
        object.__setattr__(self, "anomaly_events", tuple(dict(item) for item in self.anomaly_events))
        object.__setattr__(self, "risk_state", dict(self.risk_state))


@dataclass(frozen=True)
class ContextMemo:
    memo_id: str
    strategy_id: str
    signal_id: str
    symbol: str
    generated_at: datetime
    model_id: str
    summary: str
    conflicts: tuple[str, ...]
    event_tags: tuple[str, ...]
    uncertainty: tuple[str, ...]
    manual_review_priority: str
    evidence_ids: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memo_id": self.memo_id,
            "strategy_id": self.strategy_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "generated_at": self.generated_at.isoformat(),
            "model_id": self.model_id,
            "summary": self.summary,
            "conflicts": list(self.conflicts),
            "event_tags": list(self.event_tags),
            "uncertainty": list(self.uncertainty),
            "manual_review_priority": self.manual_review_priority,
            "evidence_ids": list(self.evidence_ids),
            "metadata": dict(self.metadata),
            "allowed_role": "context_only",
            "trade_authority": false_value(),
            "sizing_authority": false_value(),
            "broker_authority": false_value(),
        }


class ContextMemoValidationError(ValueError):
    pass


class ContextPanelService:
    """Persist explanation memos while forbidding trade and sizing authority."""

    def __init__(self, registry: ResearchRegistry, artifacts: ArtifactStore):
        self.registry = registry
        self.artifacts = artifacts

    def record(
        self,
        context: ContextInput,
        *,
        model_id: str,
        output: Mapping[str, Any],
        evidence_ids: Sequence[str] = (),
    ) -> tuple[ContextMemo, ArtifactReference]:
        self._validate_output(output)
        generated = utc_now()
        payload = {
            "strategy_id": context.strategy_id,
            "signal_id": context.signal_id,
            "symbol": context.symbol,
            "decision_time": context.decision_time.isoformat(),
            "model_id": model_id,
            "summary": str(output.get("summary") or ""),
            "conflicts": tuple(str(item) for item in output.get("conflicts") or ()),
            "event_tags": tuple(str(item) for item in output.get("event_tags") or ()),
            "uncertainty": tuple(str(item) for item in output.get("uncertainty") or ()),
            "manual_review_priority": str(output.get("manual_review_priority") or "normal"),
            "evidence_ids": tuple(sorted(set(evidence_ids))),
        }
        memo = ContextMemo(
            memo_id=stable_id("context_memo", payload),
            strategy_id=context.strategy_id,
            signal_id=context.signal_id,
            symbol=context.symbol,
            generated_at=generated,
            model_id=model_id,
            summary=payload["summary"],
            conflicts=payload["conflicts"],
            event_tags=payload["event_tags"],
            uncertainty=payload["uncertainty"],
            manual_review_priority=payload["manual_review_priority"],
            evidence_ids=payload["evidence_ids"],
            metadata={
                "decision_time": context.decision_time.isoformat(),
                "deterministic_signal_preserved": True,
                "risk_state_preserved": True,
                "feature_snapshot_count": len(context.feature_snapshots),
                "news_event_count": len(context.news_events),
                "anomaly_event_count": len(context.anomaly_events),
                "output_schema": "context_only_v1",
            },
        )
        artifact = self.artifacts.put_json(
            {
                "memo": memo.to_dict(),
                "input_references": {
                    "strategy_id": context.strategy_id,
                    "signal_id": context.signal_id,
                    "symbol": context.symbol,
                    "decision_time": context.decision_time.isoformat(),
                    "feature_snapshot_ids": [item.get("snapshot_id") for item in context.feature_snapshots],
                    "news_event_ids": [item.get("news_event_id") for item in context.news_events],
                    "anomaly_ids": [item.get("anomaly_id") for item in context.anomaly_events],
                },
            },
            metadata={"kind": "context_memo", "memo_id": memo.memo_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        self.registry.audit(
            AuditEvent(
                event_type="CONTEXT_MEMO_RECORDED",
                entity_type="signal",
                entity_id=context.signal_id,
                occurred_at=generated,
                payload={
                    "memo_id": memo.memo_id,
                    "artifact_id": artifact.artifact_id,
                    "model_id": model_id,
                    "allowed_role": "context_only",
                    "trade_authority": False,
                },
            )
        )
        return memo, artifact

    @classmethod
    def _validate_output(cls, output: Mapping[str, Any]) -> None:
        forbidden = _find_forbidden_keys(output)
        if forbidden:
            raise ContextMemoValidationError(
                f"context output contains forbidden trade/sizing fields: {sorted(forbidden)}"
            )
        allowed = {"summary", "conflicts", "event_tags", "uncertainty", "manual_review_priority"}
        unknown = set(output) - allowed
        if unknown:
            raise ContextMemoValidationError(f"unknown context memo fields: {sorted(unknown)}")
        priority = str(output.get("manual_review_priority") or "normal")
        if priority not in {"low", "normal", "high", "urgent"}:
            raise ContextMemoValidationError("manual_review_priority is invalid")


def _find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_OUTPUT_KEYS:
                found.add(normalized)
            found.update(_find_forbidden_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_find_forbidden_keys(item))
    return found


def false_value() -> bool:
    return False
