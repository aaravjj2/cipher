from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .hashing import canonical_json, stable_id
from .models import AuditEvent, utc_now
from .registry import ResearchRegistry


@dataclass(frozen=True)
class ForecastDistribution:
    forecast_id: str
    feature_id: str
    symbol: str
    event_time: datetime
    available_at: datetime
    horizon_seconds: int
    expected_return_pct: float
    lower_return_pct: float
    upper_return_pct: float
    standard_deviation_pct: float | None
    model_artifact_id: str | None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        event = _aware(self.event_time, "event_time")
        available = _aware(self.available_at, "available_at")
        if available < event:
            raise ValueError("forecast available_at cannot precede event_time")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if self.lower_return_pct > self.upper_return_pct:
            raise ValueError("forecast lower bound cannot exceed upper bound")
        object.__setattr__(self, "event_time", event)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class RealizedOutcome:
    symbol: str
    event_time: datetime
    available_at: datetime
    realized_return_pct: float
    realized_volatility_pct: float | None
    market_return_pct: float | None = None
    sector_return_pct: float | None = None
    data_quality: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        event = _aware(self.event_time, "event_time")
        available = _aware(self.available_at, "available_at")
        if available < event:
            raise ValueError("outcome available_at cannot precede event_time")
        object.__setattr__(self, "event_time", event)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "data_quality", dict(self.data_quality))


@dataclass(frozen=True)
class EventContext:
    event_id: str
    event_time: datetime
    available_at: datetime
    event_type: str
    symbols: Sequence[str]
    title: str
    confidence: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        event = _aware(self.event_time, "event_time")
        available = _aware(self.available_at, "available_at")
        if available < event:
            raise ValueError("event available_at cannot precede event_time")
        if not 0 <= self.confidence <= 1:
            raise ValueError("event confidence must be in [0, 1]")
        object.__setattr__(self, "event_time", event)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "symbols", tuple(sorted({value.upper() for value in self.symbols})))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class AnomalyAssessment:
    anomaly_id: str
    symbol: str
    event_time: datetime
    available_at: datetime
    forecast_id: str
    residual_pct: float
    market_adjusted_residual_pct: float | None
    sector_adjusted_residual_pct: float | None
    standardized_residual: float | None
    confidence_band_breach: bool
    severity: float
    associated_events: tuple[dict[str, Any], ...]
    suitable_for_evaluation: bool
    explanation: str
    uncertainty_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "symbol": self.symbol,
            "event_time": self.event_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "forecast_id": self.forecast_id,
            "residual_pct": self.residual_pct,
            "market_adjusted_residual_pct": self.market_adjusted_residual_pct,
            "sector_adjusted_residual_pct": self.sector_adjusted_residual_pct,
            "standardized_residual": self.standardized_residual,
            "confidence_band_breach": self.confidence_band_breach,
            "severity": self.severity,
            "associated_events": list(self.associated_events),
            "suitable_for_evaluation": self.suitable_for_evaluation,
            "explanation": self.explanation,
            "uncertainty_notes": list(self.uncertainty_notes),
        }


class AttributionEngine:
    """Associate forecast anomalies with events without asserting causality."""

    def __init__(self, registry: ResearchRegistry):
        self.registry = registry

    def assess(
        self,
        forecast: ForecastDistribution,
        outcome: RealizedOutcome,
        events: Sequence[EventContext],
        *,
        event_window_before: timedelta = timedelta(hours=24),
        event_window_after: timedelta = timedelta(hours=1),
    ) -> AnomalyAssessment:
        if forecast.symbol != outcome.symbol:
            raise ValueError("forecast and outcome symbols differ")
        expected_outcome_time = forecast.event_time + timedelta(seconds=forecast.horizon_seconds)
        timing_error = abs((outcome.event_time - expected_outcome_time).total_seconds())
        residual = outcome.realized_return_pct - forecast.expected_return_pct
        market_adjusted = (
            residual - outcome.market_return_pct if outcome.market_return_pct is not None else None
        )
        sector_adjusted = (
            residual - outcome.sector_return_pct if outcome.sector_return_pct is not None else None
        )
        std = forecast.standard_deviation_pct
        standardized = residual / std if std is not None and std > 0 else None
        breach = (
            outcome.realized_return_pct < forecast.lower_return_pct
            or outcome.realized_return_pct > forecast.upper_return_pct
        )
        associated: list[dict[str, Any]] = []
        earliest = outcome.event_time - event_window_before
        latest = outcome.event_time + event_window_after
        for event in events:
            if event.available_at > outcome.available_at:
                continue
            if not earliest <= event.event_time <= latest:
                continue
            symbol_match = outcome.symbol in event.symbols
            if event.symbols and not symbol_match:
                continue
            distance_seconds = abs((outcome.event_time - event.event_time).total_seconds())
            timing_confidence = max(0.0, 1.0 - distance_seconds / max(1.0, event_window_before.total_seconds()))
            association_confidence = round(event.confidence * (0.5 + 0.5 * timing_confidence), 6)
            associated.append(
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "event_time": event.event_time.isoformat(),
                    "available_at": event.available_at.isoformat(),
                    "title": event.title,
                    "association_confidence": association_confidence,
                    "symbol_match": symbol_match,
                    "causal_claim": False,
                }
            )
        associated.sort(key=lambda item: (-float(item["association_confidence"]), item["event_time"]))
        quality = dict(outcome.data_quality)
        quality_passed = quality.get("passed", True) is not False and not quality.get("failures")
        suitable = bool(quality_passed and timing_error <= max(60, forecast.horizon_seconds * 0.05))
        severity = self._severity(standardized, residual, breach)
        uncertainty: list[str] = []
        if standardized is None:
            uncertainty.append("forecast standard deviation unavailable; severity uses residual magnitude")
        if not associated:
            uncertainty.append("no point-in-time event was associated within the configured window")
        if timing_error:
            uncertainty.append(f"outcome timing differs from forecast horizon by {timing_error:.0f} seconds")
        if not quality_passed:
            uncertainty.append("realized-outcome data quality did not pass")
        explanation = self._explanation(
            symbol=outcome.symbol,
            residual=residual,
            breach=breach,
            associated_count=len(associated),
            market_adjusted=market_adjusted,
            sector_adjusted=sector_adjusted,
        )
        payload = {
            "symbol": outcome.symbol,
            "event_time": outcome.event_time.isoformat(),
            "forecast_id": forecast.forecast_id,
            "residual_pct": residual,
            "associated_event_ids": [item["event_id"] for item in associated],
            "severity": severity,
        }
        assessment = AnomalyAssessment(
            anomaly_id=stable_id("anomaly", payload),
            symbol=outcome.symbol,
            event_time=outcome.event_time,
            available_at=max(forecast.available_at, outcome.available_at),
            forecast_id=forecast.forecast_id,
            residual_pct=residual,
            market_adjusted_residual_pct=market_adjusted,
            sector_adjusted_residual_pct=sector_adjusted,
            standardized_residual=standardized,
            confidence_band_breach=breach,
            severity=severity,
            associated_events=tuple(associated),
            suitable_for_evaluation=suitable,
            explanation=explanation,
            uncertainty_notes=tuple(uncertainty),
        )
        self._persist(assessment)
        return assessment

    def _persist(self, assessment: AnomalyAssessment) -> None:
        payload = canonical_json(assessment.to_dict())
        with self.registry.connect() as db:
            existing = db.execute(
                "select payload_json from anomaly_events where anomaly_id = ?",
                (assessment.anomaly_id,),
            ).fetchone()
            if existing and existing["payload_json"] != payload:
                raise RuntimeError("anomaly ID collision")
            db.execute(
                """
                insert or ignore into anomaly_events(
                    anomaly_id, symbol, event_time, available_at, severity,
                    suitable_for_evaluation, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment.anomaly_id,
                    assessment.symbol,
                    assessment.event_time.isoformat(),
                    assessment.available_at.isoformat(),
                    assessment.severity,
                    1 if assessment.suitable_for_evaluation else 0,
                    payload,
                ),
            )
        self.registry.audit(
            AuditEvent(
                event_type="ANOMALY_ASSESSED",
                entity_type="anomaly",
                entity_id=assessment.anomaly_id,
                occurred_at=utc_now(),
                payload={
                    "symbol": assessment.symbol,
                    "severity": assessment.severity,
                    "confidence_band_breach": assessment.confidence_band_breach,
                    "causal_claim": False,
                },
            )
        )

    @staticmethod
    def _severity(standardized: float | None, residual: float, breach: bool) -> float:
        base = abs(standardized) if standardized is not None and math.isfinite(standardized) else abs(residual)
        if breach:
            base += 1.0
        return round(min(10.0, base), 6)

    @staticmethod
    def _explanation(
        *,
        symbol: str,
        residual: float,
        breach: bool,
        associated_count: int,
        market_adjusted: float | None,
        sector_adjusted: float | None,
    ) -> str:
        direction = "above" if residual > 0 else "below" if residual < 0 else "at"
        text = f"{symbol} realized {abs(residual):.4f} percentage points {direction} the forecast expectation"
        text += " and breached the forecast interval" if breach else " without breaching the forecast interval"
        text += f". {associated_count} point-in-time event(s) were associated; this is not a causal conclusion."
        if market_adjusted is not None:
            text += f" Market-adjusted residual: {market_adjusted:.4f}."
        if sector_adjusted is not None:
            text += f" Sector-adjusted residual: {sector_adjusted:.4f}."
        return text


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)
