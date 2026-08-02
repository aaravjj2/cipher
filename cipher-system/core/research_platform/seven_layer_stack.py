from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from .hashing import stable_id
from .models import AllowedUse, PromotionState, utc_now


class LayerMode(str, Enum):
    INGESTION = "ingestion"
    OFFLINE_BATCH = "offline_batch"
    VALIDATION_GATE = "validation_gate"
    LIVE_SYNTHESIS = "live_synthesis"
    FEEDBACK = "feedback"


@dataclass(frozen=True)
class StackLayer:
    layer: int
    name: str
    mode: LayerMode
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    allowed_use: tuple[AllowedUse, ...]
    external_api_access: bool = False
    live_capital_access: bool = False
    maximum_promotion_state: PromotionState = PromotionState.LIVE_REVIEW_REQUIRED
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "name": self.name,
            "mode": self.mode.value,
            "reads": list(self.reads),
            "writes": list(self.writes),
            "allowed_use": [item.value for item in self.allowed_use],
            "external_api_access": self.external_api_access,
            "live_capital_access": self.live_capital_access,
            "maximum_promotion_state": self.maximum_promotion_state.value,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class BoundaryViolation:
    layer: int
    name: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"layer": self.layer, "name": self.name, "reason": self.reason}


@dataclass(frozen=True)
class SevenLayerStackSpec:
    layers: tuple[StackLayer, ...]
    warehouse_tables: tuple[str, ...]
    forbidden_live_terms: tuple[str, ...] = (
        "/v2/orders",
        "submit_order",
        "place_order",
        "create_order",
        "TradingClient",
        "OrderClient",
    )

    @classmethod
    def default(cls) -> "SevenLayerStackSpec":
        context = (AllowedUse.CONTEXT, AllowedUse.FILTER, AllowedUse.RANKING)
        return cls(
            layers=(
                StackLayer(
                    1,
                    "foundational_data_warehouse",
                    LayerMode.INGESTION,
                    reads=("external_market_data", "external_news_data"),
                    writes=(
                        "market_bars",
                        "option_quotes",
                        "option_trades",
                        "option_contract_reference",
                        "gex_snapshots",
                        "news_events",
                    ),
                    allowed_use=(AllowedUse.CONTEXT,),
                    external_api_access=True,
                    notes="Only ingestion workers may touch vendors; downstream layers read warehouse artifacts.",
                ),
                StackLayer(
                    2,
                    "forecasting_and_feature_generation",
                    LayerMode.OFFLINE_BATCH,
                    reads=("market_bars", "news_events", "gex_snapshots"),
                    writes=("model_forecasts", "feature_vectors"),
                    allowed_use=context,
                    notes="Kronos, TimesFM, FinBERT, GDELT parsing, and feature transforms run offline.",
                ),
                StackLayer(
                    3,
                    "multi_agent_factor_discovery",
                    LayerMode.OFFLINE_BATCH,
                    reads=("market_bars", "feature_vectors", "experiment_metrics"),
                    writes=("factor_candidates", "experiment_metrics"),
                    allowed_use=(AllowedUse.CONTEXT, AllowedUse.RANKING),
                    notes="Generated factors must be expressible from raw base columns, not factor-of-factor chains.",
                ),
                StackLayer(
                    4,
                    "causal_attribution_and_anomaly_engine",
                    LayerMode.OFFLINE_BATCH,
                    reads=("model_forecasts", "market_bars", "news_events"),
                    writes=("anomaly_log",),
                    allowed_use=(AllowedUse.CONTEXT,),
                    notes="Anomalies explain forecast misses and are blocked from real-time execution authority.",
                ),
                StackLayer(
                    5,
                    "multi_paradigm_backtesting_gate",
                    LayerMode.VALIDATION_GATE,
                    reads=("factor_candidates", "feature_vectors", "model_forecasts", "anomaly_log"),
                    writes=("backtest_gate_results", "experiment_metrics"),
                    allowed_use=(AllowedUse.CONTEXT, AllowedUse.FILTER, AllowedUse.RANKING),
                    notes="Vectorized sweeps may graduate only to LEAN replication and prospective shadow states.",
                ),
                StackLayer(
                    6,
                    "live_synthesis_and_simulated_risk_allocation",
                    LayerMode.LIVE_SYNTHESIS,
                    reads=("model_forecasts", "anomaly_log", "backtest_gate_results"),
                    writes=("portfolio_proposals", "execution_audit"),
                    allowed_use=(AllowedUse.CONTEXT,),
                    live_capital_access=False,
                    notes="Agent synthesis and convex allocation create simulation-only proposals, never broker orders.",
                ),
                StackLayer(
                    7,
                    "autoresearch_feedback_loop",
                    LayerMode.FEEDBACK,
                    reads=("execution_audit", "anomaly_log", "experiment_metrics"),
                    writes=("autoresearch_feedback",),
                    allowed_use=(AllowedUse.CONTEXT,),
                    notes="Feedback routes to Layer 5 validation; it cannot mutate live runtime prompts directly.",
                ),
            ),
            warehouse_tables=(
                "market_bars",
                "option_quotes",
                "option_trades",
                "option_contract_reference",
                "gex_snapshots",
                "news_events",
                "model_forecasts",
                "feature_vectors",
                "factor_candidates",
                "anomaly_log",
                "backtest_gate_results",
                "experiment_metrics",
                "portfolio_proposals",
                "execution_audit",
                "autoresearch_feedback",
                "audit_events",
            ),
        )

    def validate_boundaries(self) -> tuple[BoundaryViolation, ...]:
        violations: list[BoundaryViolation] = []
        seen_writes: dict[str, int] = {}
        for layer in self.layers:
            if layer.layer > 1 and layer.external_api_access:
                violations.append(BoundaryViolation(layer.layer, layer.name, "external_api_access_above_ingestion"))
            if layer.live_capital_access:
                violations.append(BoundaryViolation(layer.layer, layer.name, "live_capital_access_forbidden"))
            if layer.maximum_promotion_state != PromotionState.LIVE_REVIEW_REQUIRED:
                violations.append(BoundaryViolation(layer.layer, layer.name, "promotion_ceiling_changed"))
            if AllowedUse.EXECUTION in layer.allowed_use:
                violations.append(BoundaryViolation(layer.layer, layer.name, "execution_allowed_use_forbidden"))
            for source in layer.reads:
                source_layer = seen_writes.get(source)
                if source_layer is not None and source_layer > layer.layer:
                    violations.append(BoundaryViolation(layer.layer, layer.name, f"downward_read:{source}"))
            for table in layer.writes:
                seen_writes[table] = layer.layer
        return tuple(violations)

    def offline_orchestration_plan(self) -> dict[str, Any]:
        steps = []
        for layer in self.layers:
            steps.append(
                {
                    "layer": layer.layer,
                    "name": layer.name,
                    "mode": layer.mode.value,
                    "reads": list(layer.reads),
                    "writes": list(layer.writes),
                    "cloud_writes_enabled_by_default": False,
                    "manual_promotion_required": layer.layer >= 5,
                    "live_order_authority": False,
                }
            )
        return {
            "schema_version": 1,
            "generated_at": utc_now().isoformat(),
            "maximum_promotion_state": PromotionState.LIVE_REVIEW_REQUIRED.value,
            "forbidden_live_terms": list(self.forbidden_live_terms),
            "steps": steps,
        }


@dataclass(frozen=True)
class ForecastObservation:
    forecast_id: str
    symbol: str
    event_time: datetime
    available_at: datetime
    lower_bound: float
    upper_bound: float
    point_forecast: float | None = None
    feature_id: str = ""
    model_artifact_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.upper())
        _require_aware(self.event_time, "event_time")
        _require_aware(self.available_at, "available_at")
        if self.upper_bound < self.lower_bound:
            raise ValueError("upper_bound cannot be below lower_bound")


@dataclass(frozen=True)
class RealizedObservation:
    symbol: str
    event_time: datetime
    available_at: datetime
    value: float
    source: str = "market_bars"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.upper())
        _require_aware(self.event_time, "event_time")
        _require_aware(self.available_at, "available_at")


@dataclass(frozen=True)
class EventContext:
    event_id: str
    symbols: tuple[str, ...]
    event_time: datetime
    available_at: datetime
    event_type: str
    sentiment: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", tuple(symbol.upper() for symbol in self.symbols))
        _require_aware(self.event_time, "event_time")
        _require_aware(self.available_at, "available_at")


@dataclass(frozen=True)
class AnomalyRecord:
    anomaly_id: str
    symbol: str
    forecast_id: str
    event_time: datetime
    available_at: datetime
    realized_value: float
    expected_lower: float
    expected_upper: float
    severity: str
    linked_event_ids: tuple[str, ...]
    allowed_use: AllowedUse = AllowedUse.CONTEXT

    def to_warehouse_row(self) -> dict[str, Any]:
        payload = {
            "anomaly_id": self.anomaly_id,
            "symbol": self.symbol,
            "forecast_id": self.forecast_id,
            "event_time": self.event_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "realized_value": self.realized_value,
            "expected_lower": self.expected_lower,
            "expected_upper": self.expected_upper,
            "severity": self.severity,
            "linked_event_ids": list(self.linked_event_ids),
            "allowed_use": self.allowed_use.value,
        }
        return {
            "record_id": self.anomaly_id,
            "event_time": self.event_time,
            "received_at": self.available_at,
            "available_at": self.available_at,
            "source": "causal_attribution_engine",
            "raw_object_id": self.forecast_id,
            "schema_version": 1,
            "anomaly_id": self.anomaly_id,
            "symbol": self.symbol,
            "forecast_id": self.forecast_id,
            "realized_value": self.realized_value,
            "expected_lower": self.expected_lower,
            "expected_upper": self.expected_upper,
            "severity": self.severity,
            "linked_event_ids": list(self.linked_event_ids),
            "allowed_use": self.allowed_use.value,
            "payload_json": payload,
        }


class ForecastAnomalyEngine:
    """Compare realized outcomes against forecast intervals without live authority."""

    def __init__(self, *, event_window: timedelta = timedelta(hours=24)):
        if event_window.total_seconds() < 0:
            raise ValueError("event_window cannot be negative")
        self.event_window = event_window

    def evaluate(
        self,
        forecasts: Sequence[ForecastObservation],
        realized: Sequence[RealizedObservation],
        events: Sequence[EventContext] = (),
    ) -> tuple[AnomalyRecord, ...]:
        realized_by_key = {(item.symbol, item.event_time): item for item in realized}
        output: list[AnomalyRecord] = []
        for forecast in forecasts:
            actual = realized_by_key.get((forecast.symbol, forecast.event_time))
            if actual is None:
                continue
            if forecast.lower_bound <= actual.value <= forecast.upper_bound:
                continue
            width = max(forecast.upper_bound - forecast.lower_bound, 1e-12)
            if actual.value < forecast.lower_bound:
                distance = (forecast.lower_bound - actual.value) / width
            else:
                distance = (actual.value - forecast.upper_bound) / width
            severity = "extreme" if distance >= 1.0 else "breach"
            available_at = max(forecast.available_at, actual.available_at)
            linked = tuple(
                event.event_id
                for event in events
                if forecast.symbol in event.symbols
                and event.available_at <= available_at
                and abs(event.event_time - forecast.event_time) <= self.event_window
            )
            payload = {
                "symbol": forecast.symbol,
                "forecast_id": forecast.forecast_id,
                "event_time": forecast.event_time.isoformat(),
                "realized": actual.value,
                "lower": forecast.lower_bound,
                "upper": forecast.upper_bound,
                "linked": linked,
            }
            output.append(
                AnomalyRecord(
                    anomaly_id=stable_id("anomaly", payload),
                    symbol=forecast.symbol,
                    forecast_id=forecast.forecast_id,
                    event_time=forecast.event_time,
                    available_at=available_at,
                    realized_value=actual.value,
                    expected_lower=forecast.lower_bound,
                    expected_upper=forecast.upper_bound,
                    severity=severity,
                    linked_event_ids=linked,
                )
            )
        return tuple(output)


@dataclass(frozen=True)
class ExecutionDelta:
    strategy_id: str
    backtest_return: float
    observed_return: float
    max_slippage_bps: float
    sample_count: int
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutoResearchFeedbackPacket:
    feedback_id: str
    generated_at: datetime
    prompt_revisions: tuple[Mapping[str, Any], ...]
    bandit_updates: tuple[Mapping[str, Any], ...]
    target_layer: str = "multi_paradigm_backtesting_gate"
    routes_to_live: bool = False

    def to_warehouse_row(self) -> dict[str, Any]:
        payload = {
            "feedback_id": self.feedback_id,
            "generated_at": self.generated_at.isoformat(),
            "target_layer": self.target_layer,
            "routes_to_live": self.routes_to_live,
            "prompt_revisions": [dict(item) for item in self.prompt_revisions],
            "bandit_updates": [dict(item) for item in self.bandit_updates],
        }
        return {
            "record_id": self.feedback_id,
            "feedback_id": self.feedback_id,
            "generated_at": self.generated_at,
            "available_at": self.generated_at,
            "target_layer": self.target_layer,
            "routes_to_live": self.routes_to_live,
            "prompt_revisions_json": payload["prompt_revisions"],
            "bandit_updates_json": payload["bandit_updates"],
            "payload_json": payload,
        }


class AutoResearchFeedbackLoop:
    """Route live-shadow deviations back to validation, never to live trading."""

    def build_packet(
        self,
        *,
        anomalies: Sequence[AnomalyRecord],
        execution_deltas: Sequence[ExecutionDelta],
        panel_seats: Sequence[str] = ("macro_context", "risk_context"),
    ) -> AutoResearchFeedbackPacket:
        prompt_revisions: list[Mapping[str, Any]] = []
        severe_symbols = sorted({item.symbol for item in anomalies if item.severity == "extreme"})
        if severe_symbols:
            for seat in panel_seats:
                prompt_revisions.append(
                    {
                        "seat": seat,
                        "instruction": "Treat forecast interval breaches as context requiring LEAN revalidation.",
                        "symbols": severe_symbols,
                        "direct_live_update": False,
                    }
                )
        bandit_updates: list[Mapping[str, Any]] = []
        for delta in execution_deltas:
            degradation = delta.observed_return - delta.backtest_return
            action_bias = "explore_factor_space" if degradation < 0 else "continue_validation"
            bandit_updates.append(
                {
                    "strategy_id": delta.strategy_id,
                    "reward_delta": degradation,
                    "max_slippage_bps": delta.max_slippage_bps,
                    "sample_count": delta.sample_count,
                    "next_action_bias": action_bias,
                    "routes_to_layer": 5,
                }
            )
        payload = {
            "anomalies": [item.anomaly_id for item in anomalies],
            "deltas": [item.__dict__ for item in execution_deltas],
            "prompt_revisions": prompt_revisions,
            "bandit_updates": bandit_updates,
            "routes_to_live": False,
        }
        return AutoResearchFeedbackPacket(
            feedback_id=stable_id("autoresearch_feedback", payload),
            generated_at=utc_now(),
            prompt_revisions=tuple(prompt_revisions),
            bandit_updates=tuple(bandit_updates),
        )


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.astimezone(timezone.utc) != value:
        return
