"""Canonical evidence and signal records shared by Cipher research surfaces.

This module is intentionally small and dependency-free. Existing payloads remain
backwards compatible, but new scanner, prospective, paper, and agent boundaries
can attach the same deterministic contract. IDs are content-addressed and never
contain credentials or execution state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
_FRESHNESS = {"current", "stale", "unknown"}
_COVERAGE = {"complete", "partial", "stale", "missing", "unknown", "sufficient", "limited"}
_DECISIONS = {"candidate", "accepted", "rejected", "no_signal", "observed", "unknown"}


def _stamp(value: Any, field_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if isinstance(value, datetime):
        return _stamp(value, "timestamp")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def content_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:40]}"


def _tuple_strings(values: Sequence[Any] | None) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in (values or ()) if str(value).strip()}))


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    ticker: str
    provider: str
    feed: str
    event_at: str
    captured_at: str
    freshness: str
    coverage: str
    missing_reasons: tuple[str, ...] = ()
    feature_ids: tuple[str, ...] = ()
    features: Mapping[str, Any] = field(default_factory=dict)
    session: Mapping[str, Any] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()
    snapshot_id: str = ""
    schema_version: int = SCHEMA_VERSION
    read_only: bool = True
    execution_capability: bool = False

    def __post_init__(self) -> None:
        ticker = str(self.ticker or "").strip().upper()
        provider = str(self.provider or "").strip().lower()
        feed = str(self.feed or "").strip().lower()
        if not ticker:
            raise ValueError("ticker is required")
        if not provider:
            raise ValueError("provider is required")
        if not feed:
            raise ValueError("feed is required")
        if self.freshness not in _FRESHNESS:
            raise ValueError(f"invalid freshness: {self.freshness}")
        if self.coverage not in _COVERAGE:
            raise ValueError(f"invalid coverage: {self.coverage}")
        event_at = _stamp(self.event_at, "event_at")
        captured_at = _stamp(self.captured_at, "captured_at")
        if captured_at < event_at:
            raise ValueError("captured_at cannot precede event_at")
        features = _plain(self.features)
        session = _plain(self.session)
        missing = _tuple_strings(self.missing_reasons)
        feature_ids = _tuple_strings(self.feature_ids)
        caveats = _tuple_strings(self.caveats)
        identity = {
            "schema_version": int(self.schema_version), "ticker": ticker,
            "provider": provider, "feed": feed, "event_at": event_at,
            "captured_at": captured_at, "freshness": self.freshness,
            "coverage": self.coverage, "missing_reasons": missing,
            "feature_ids": feature_ids, "features": features, "session": session,
            "caveats": caveats,
        }
        snapshot_id = str(self.snapshot_id or content_id("evidence", identity))
        if not snapshot_id.startswith("evidence_") and len(snapshot_id) < 8:
            raise ValueError("snapshot_id is invalid")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "feed", feed)
        object.__setattr__(self, "event_at", event_at)
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "missing_reasons", missing)
        object.__setattr__(self, "feature_ids", feature_ids)
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "session", session)
        object.__setattr__(self, "caveats", caveats)
        object.__setattr__(self, "snapshot_id", snapshot_id)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "EvidenceSnapshot":
        freshness = payload.get("freshness")
        if isinstance(freshness, Mapping):
            freshness = freshness.get("status", "unknown")
        coverage = payload.get("coverage")
        if isinstance(coverage, Mapping):
            coverage = coverage.get("status", "unknown")
        return cls(
            ticker=str(payload.get("ticker") or ""),
            provider=str(payload.get("provider") or "unknown"),
            feed=str(payload.get("feed") or "unknown"),
            event_at=str(payload.get("event_at") or payload.get("as_of") or ""),
            captured_at=str(payload.get("captured_at") or payload.get("as_of") or ""),
            freshness=str(freshness or "unknown"),
            coverage=str(coverage or "unknown"),
            missing_reasons=payload.get("missing_reasons") or (),
            feature_ids=payload.get("feature_ids") or payload.get("feature_snapshot_ids") or (),
            features=payload.get("features") or {"levels": payload.get("levels") or {}},
            session=payload.get("session") or {},
            caveats=payload.get("caveats") or (),
            snapshot_id=str(payload.get("snapshot_id") or ""),
            schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
            read_only=bool(payload.get("read_only", True)),
            execution_capability=bool(payload.get("execution_capability", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "snapshot_id": self.snapshot_id,
            "ticker": self.ticker, "provider": self.provider, "feed": self.feed,
            "event_at": self.event_at, "captured_at": self.captured_at,
            "freshness": self.freshness, "coverage": self.coverage,
            "missing_reasons": list(self.missing_reasons),
            "feature_ids": list(self.feature_ids), "features": _plain(self.features),
            "session": _plain(self.session), "caveats": list(self.caveats),
            "read_only": self.read_only, "execution_capability": self.execution_capability,
        }


@dataclass(frozen=True, slots=True)
class SignalRecord:
    ticker: str
    strategy: str
    direction: str
    signal_at: str
    available_at: str
    evidence_snapshot_ids: tuple[str, ...]
    decision: str = "observed"
    reason: str | None = None
    configuration_sha256: str | None = None
    signal_id: str = ""
    schema_version: int = SCHEMA_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ticker = str(self.ticker or "").strip().upper()
        strategy = str(self.strategy or "").strip()
        direction = str(self.direction or "").strip().lower()
        if not ticker or not strategy or not direction:
            raise ValueError("ticker, strategy, and direction are required")
        if self.decision not in _DECISIONS:
            raise ValueError(f"invalid decision: {self.decision}")
        signal_at = _stamp(self.signal_at, "signal_at")
        available_at = _stamp(self.available_at, "available_at")
        if available_at < signal_at:
            raise ValueError("available_at cannot precede signal_at")
        evidence_ids = _tuple_strings(self.evidence_snapshot_ids)
        identity = {
            "schema_version": int(self.schema_version), "ticker": ticker,
            "strategy": strategy, "direction": direction, "signal_at": signal_at,
            "available_at": available_at, "evidence_snapshot_ids": evidence_ids,
            "decision": self.decision, "reason": self.reason,
            "configuration_sha256": self.configuration_sha256,
            "metadata": _plain(self.metadata),
        }
        signal_id = str(self.signal_id or content_id("signal", identity))
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "strategy", strategy)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "signal_at", signal_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "evidence_snapshot_ids", evidence_ids)
        object.__setattr__(self, "metadata", _plain(self.metadata))
        object.__setattr__(self, "signal_id", signal_id)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SignalRecord":
        return cls(
            ticker=str(payload.get("ticker") or payload.get("symbol") or ""),
            strategy=str(payload.get("strategy") or payload.get("program_id") or "unknown"),
            direction=str(payload.get("direction") or "unknown"),
            signal_at=str(payload.get("signal_at") or payload.get("signal_bar_at") or ""),
            available_at=str(payload.get("available_at") or payload.get("signal_at") or ""),
            evidence_snapshot_ids=payload.get("evidence_snapshot_ids") or payload.get("feature_snapshot_ids") or (),
            decision=str(payload.get("decision") or ("candidate" if payload.get("signal_id") else "observed")),
            reason=payload.get("reason"),
            configuration_sha256=payload.get("configuration_sha256"),
            signal_id=str(payload.get("signal_id") or ""),
            schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
            metadata=payload.get("metadata") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "signal_id": self.signal_id,
            "ticker": self.ticker, "strategy": self.strategy,
            "direction": self.direction, "signal_at": self.signal_at,
            "available_at": self.available_at,
            "evidence_snapshot_ids": list(self.evidence_snapshot_ids),
            "decision": self.decision, "reason": self.reason,
            "configuration_sha256": self.configuration_sha256,
            "metadata": _plain(self.metadata), "read_only": True,
            "execution_capability": False,
        }


def attach_contracts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with canonical evidence/signal records attached.

    Existing fields are intentionally preserved so old clients keep working.
    Invalid/incomplete records are omitted rather than fabricated.
    """
    result = dict(payload)
    raw_evidence = result.get("evidence_snapshot")
    if isinstance(raw_evidence, Mapping):
        try:
            evidence = EvidenceSnapshot.from_mapping(raw_evidence)
            result["evidence_contract"] = evidence.to_dict()
        except (TypeError, ValueError):
            pass
    try:
        signal = SignalRecord.from_mapping(result)
        result["signal_record"] = signal.to_dict()
    except (TypeError, ValueError):
        pass
    return result
