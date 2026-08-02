from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .hashing import stable_id
from .models import AuditEvent, PromotionState, utc_now
from .registry import ResearchRegistry, RegistryNotFoundError


@dataclass(frozen=True)
class CandidatePosition:
    strategy_id: str
    signal_id: str
    symbol: str
    direction: str
    instrument_template: str
    maximum_loss: float
    premium_at_risk: float
    quantity: int
    sector: str
    correlation_bucket: str
    quote_time: datetime
    spread_pct: float
    liquidity_score: float
    event_risk: bool
    priority_score: float
    feature_snapshot_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    candidate_id: str = ""

    def __post_init__(self) -> None:
        if self.quote_time.tzinfo is None or self.quote_time.utcoffset() is None:
            raise ValueError("quote_time must be timezone-aware")
        quote = self.quote_time.astimezone(timezone.utc)
        if self.maximum_loss < 0 or self.premium_at_risk < 0:
            raise ValueError("risk values cannot be negative")
        if self.quantity < 1:
            raise ValueError("quantity must be positive")
        if self.spread_pct < 0:
            raise ValueError("spread_pct cannot be negative")
        if not 0 <= self.liquidity_score <= 1:
            raise ValueError("liquidity_score must be in [0, 1]")
        payload = {
            "strategy_id": self.strategy_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol.upper(),
            "direction": self.direction,
            "instrument_template": self.instrument_template,
            "maximum_loss": self.maximum_loss,
            "premium_at_risk": self.premium_at_risk,
            "quantity": self.quantity,
            "sector": self.sector,
            "correlation_bucket": self.correlation_bucket,
            "quote_time": quote.isoformat(),
            "spread_pct": self.spread_pct,
            "liquidity_score": self.liquidity_score,
            "event_risk": self.event_risk,
            "priority_score": self.priority_score,
            "feature_snapshot_id": self.feature_snapshot_id,
            "metadata": dict(self.metadata),
        }
        object.__setattr__(self, "quote_time", quote)
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "candidate_id", self.candidate_id or stable_id("candidate", payload))


@dataclass(frozen=True)
class ExistingPosition:
    symbol: str
    sector: str
    correlation_bucket: str
    premium_at_risk: float
    maximum_loss: float


@dataclass(frozen=True)
class PortfolioState:
    as_of: datetime
    starting_equity: float
    daily_realized_pnl: float
    positions: tuple[ExistingPosition, ...] = ()

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        object.__setattr__(self, "as_of", self.as_of.astimezone(timezone.utc))
        object.__setattr__(self, "positions", tuple(self.positions))


@dataclass(frozen=True)
class RiskPolicy:
    maximum_loss_per_position: float
    maximum_loss_per_position_pct_equity: float
    maximum_aggregate_premium_at_risk: float
    maximum_positions: int
    maximum_positions_per_ticker: int
    maximum_positions_per_sector: int
    maximum_positions_per_correlation_bucket: int
    daily_loss_stop: float
    maximum_quote_age_seconds: int
    maximum_spread_pct: float
    minimum_liquidity_score: float
    allow_event_risk: bool = False
    required_promotion_state: PromotionState = PromotionState.PROSPECTIVE_SHADOW


@dataclass(frozen=True)
class RiskDecision:
    candidate_id: str
    approved: bool
    reasons: tuple[str, ...]
    reviewed_at: datetime
    projected: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "approved": self.approved,
            "reasons": list(self.reasons),
            "reviewed_at": self.reviewed_at.isoformat(),
            "projected": dict(self.projected),
            "order_intent_created": False,
            "broker_action": None,
        }


class DeterministicRiskAllocator:
    """Review and cap simulated candidates; never route or generate orders."""

    def __init__(self, registry: ResearchRegistry, policy: RiskPolicy):
        self.registry = registry
        self.policy = policy

    def review(
        self,
        candidates: Sequence[CandidatePosition],
        state: PortfolioState,
    ) -> tuple[RiskDecision, ...]:
        existing = list(state.positions)
        decisions: list[RiskDecision] = []
        for candidate in sorted(candidates, key=lambda item: (-item.priority_score, item.candidate_id)):
            reasons = self._reasons(candidate, state, existing)
            approved = not reasons
            if approved:
                existing.append(
                    ExistingPosition(
                        symbol=candidate.symbol,
                        sector=candidate.sector,
                        correlation_bucket=candidate.correlation_bucket,
                        premium_at_risk=candidate.premium_at_risk,
                        maximum_loss=candidate.maximum_loss,
                    )
                )
            projected = {
                "position_count": len(existing),
                "aggregate_premium_at_risk": sum(item.premium_at_risk for item in existing),
                "aggregate_maximum_loss": sum(item.maximum_loss for item in existing),
                "ticker_count": sum(1 for item in existing if item.symbol == candidate.symbol),
                "sector_count": sum(1 for item in existing if item.sector == candidate.sector),
                "correlation_bucket_count": sum(
                    1 for item in existing if item.correlation_bucket == candidate.correlation_bucket
                ),
            }
            decision = RiskDecision(
                candidate_id=candidate.candidate_id,
                approved=approved,
                reasons=tuple(reasons),
                reviewed_at=utc_now(),
                projected=projected,
            )
            decisions.append(decision)
            self.registry.audit(
                AuditEvent(
                    event_type="SIMULATED_RISK_DECISION",
                    entity_type="candidate_position",
                    entity_id=candidate.candidate_id,
                    occurred_at=decision.reviewed_at,
                    payload={
                        **decision.to_dict(),
                        "strategy_id": candidate.strategy_id,
                        "signal_id": candidate.signal_id,
                        "symbol": candidate.symbol,
                    },
                )
            )
        return tuple(decisions)

    def _reasons(
        self,
        candidate: CandidatePosition,
        state: PortfolioState,
        positions: Sequence[ExistingPosition],
    ) -> list[str]:
        reasons: list[str] = []
        try:
            promotion_state = self.registry.current_state(candidate.strategy_id)
        except RegistryNotFoundError:
            return ["strategy_not_registered"]
        if _state_rank(promotion_state) < _state_rank(self.policy.required_promotion_state):
            reasons.append(f"strategy_not_{self.policy.required_promotion_state.value.lower()}")
        if state.daily_realized_pnl <= -abs(self.policy.daily_loss_stop):
            reasons.append("daily_loss_stop_active")
        if candidate.maximum_loss > self.policy.maximum_loss_per_position:
            reasons.append("maximum_loss_per_position_exceeded")
        equity_limit = state.starting_equity * self.policy.maximum_loss_per_position_pct_equity
        if candidate.maximum_loss > equity_limit:
            reasons.append("maximum_loss_pct_equity_exceeded")
        if len(positions) >= self.policy.maximum_positions:
            reasons.append("maximum_positions_reached")
        if sum(1 for item in positions if item.symbol == candidate.symbol) >= self.policy.maximum_positions_per_ticker:
            reasons.append("ticker_limit_reached")
        if sum(1 for item in positions if item.sector == candidate.sector) >= self.policy.maximum_positions_per_sector:
            reasons.append("sector_limit_reached")
        if (
            sum(1 for item in positions if item.correlation_bucket == candidate.correlation_bucket)
            >= self.policy.maximum_positions_per_correlation_bucket
        ):
            reasons.append("correlation_bucket_limit_reached")
        projected_premium = sum(item.premium_at_risk for item in positions) + candidate.premium_at_risk
        if projected_premium > self.policy.maximum_aggregate_premium_at_risk:
            reasons.append("aggregate_premium_limit_exceeded")
        age = (state.as_of - candidate.quote_time).total_seconds()
        if age < 0:
            reasons.append("quote_from_future")
        elif age > self.policy.maximum_quote_age_seconds:
            reasons.append("stale_quote")
        if candidate.spread_pct > self.policy.maximum_spread_pct:
            reasons.append("spread_too_wide")
        if candidate.liquidity_score < self.policy.minimum_liquidity_score:
            reasons.append("liquidity_below_minimum")
        if candidate.event_risk and not self.policy.allow_event_risk:
            reasons.append("event_risk_blocked")
        if not candidate.feature_snapshot_id:
            reasons.append("feature_snapshot_missing")
        return reasons


def _state_rank(state: PromotionState) -> int:
    order = (
        PromotionState.IDEA,
        PromotionState.SPECIFIED,
        PromotionState.DATA_VALIDATED,
        PromotionState.FAST_BACKTESTED,
        PromotionState.WALK_FORWARD_PASSED,
        PromotionState.LEAN_REPLICATED,
        PromotionState.PROSPECTIVE_SHADOW,
        PromotionState.PAPER_ELIGIBLE,
        PromotionState.LIVE_REVIEW_REQUIRED,
    )
    if state in {PromotionState.REJECTED, PromotionState.RETIRED}:
        return -1
    return order.index(state)
