from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.research_platform.attribution import (
    AttributionEngine,
    EventContext,
    ForecastDistribution,
    RealizedOutcome,
)
from core.research_platform.models import PromotionState, StrategySpec
from core.research_platform.prospective import (
    ProspectiveObservation,
    ProspectiveRegistration,
    ProspectiveService,
)
from core.research_platform.registry import ResearchRegistry
from core.research_platform.risk import (
    CandidatePosition,
    DeterministicRiskAllocator,
    PortfolioState,
    RiskPolicy,
)

NOW = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)


def strategy() -> StrategySpec:
    return StrategySpec(
        name="prospective_test",
        version="v1",
        signal_rule={"rule": "registered"},
        instrument_rule={"type": "debit_spread"},
        contract_selection_rule={"point_in_time": True},
        entry_rule={"timing": "signal"},
        exit_rule={"hold": 1},
        sizing_rule={"quantity": 1},
        portfolio_constraints={"max": 1},
        required_feature_ids=(),
        fill_model={"entry": "ask", "exit": "bid"},
        benchmark="control",
        statistical_plan={"prospective": True},
        promotion_thresholds={},
    )


def test_prospective_registration_locks_sample_and_scores(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    spec = strategy()
    registry.register_strategy(spec)
    service = ProspectiveService(registry)
    registration = ProspectiveRegistration(
        strategy_id=spec.strategy_id,
        name="locked",
        configuration={"threshold": 1.0},
        minimum_sample=3,
        acceptance_criteria={
            "minimum_win_rate": 0.5,
            "minimum_average_return_pct": 0.1,
            "minimum_profit_factor": 1.0,
        },
        created_at=NOW,
    )
    service.register(registration)
    for index, value in enumerate((1.0, -0.2, 0.5)):
        service.append(
            ProspectiveObservation(
                prospective_test_id=registration.registration_id,
                signal_id=f"signal-{index}",
                signal_time=NOW + timedelta(minutes=index),
                available_at=NOW + timedelta(minutes=index, seconds=1),
                symbol="SPY",
                direction="bullish",
                feature_snapshot_ids=(),
                contract_candidates=({"symbol": "SPY_TEST", "accepted": True},),
                selected_instrument={"symbol": "SPY_TEST"},
                simulated_entry={"price": 1.0},
                rejection_reasons=(),
                outcome={"return_pct": value},
            )
        )
    result = service.evaluate(registration.registration_id)
    assert result["minimum_reached"]
    assert result["status"] == "PASSED"
    assert result["metrics"]["scored"] == 3
    assert result["metrics"]["win_rate"] == 2 / 3


def test_attribution_associates_events_without_causal_claim(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    engine = AttributionEngine(registry)
    forecast = ForecastDistribution(
        forecast_id="forecast-1",
        feature_id="feature-1",
        symbol="SPY",
        event_time=NOW,
        available_at=NOW,
        horizon_seconds=3600,
        expected_return_pct=0.1,
        lower_return_pct=-0.5,
        upper_return_pct=0.5,
        standard_deviation_pct=0.2,
        model_artifact_id="model-1",
    )
    outcome = RealizedOutcome(
        symbol="SPY",
        event_time=NOW + timedelta(hours=1),
        available_at=NOW + timedelta(hours=1, seconds=5),
        realized_return_pct=1.0,
        realized_volatility_pct=0.8,
        market_return_pct=0.2,
        sector_return_pct=0.1,
        data_quality={"passed": True},
    )
    event = EventContext(
        event_id="news-1",
        event_time=NOW + timedelta(minutes=30),
        available_at=NOW + timedelta(minutes=31),
        event_type="macro",
        symbols=("SPY",),
        title="Test event",
        confidence=0.8,
    )
    assessment = engine.assess(forecast, outcome, [event])
    assert assessment.confidence_band_breach
    assert assessment.associated_events[0]["causal_claim"] is False
    assert "not a causal conclusion" in assessment.explanation
    assert registry.counts()["anomaly_events"] == 1


def test_deterministic_risk_review_never_creates_order_intent(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    spec = strategy()
    registry.register_strategy(spec)
    policy = RiskPolicy(
        maximum_loss_per_position=200,
        maximum_loss_per_position_pct_equity=0.05,
        maximum_aggregate_premium_at_risk=500,
        maximum_positions=3,
        maximum_positions_per_ticker=1,
        maximum_positions_per_sector=2,
        maximum_positions_per_correlation_bucket=2,
        daily_loss_stop=200,
        maximum_quote_age_seconds=5,
        maximum_spread_pct=12,
        minimum_liquidity_score=0.5,
        required_promotion_state=PromotionState.IDEA,
    )
    candidate = CandidatePosition(
        strategy_id=spec.strategy_id,
        signal_id="signal-1",
        symbol="SPY",
        direction="bullish",
        instrument_template="debit_spread",
        maximum_loss=100,
        premium_at_risk=100,
        quantity=1,
        sector="index",
        correlation_bucket="broad_market",
        quote_time=NOW,
        spread_pct=5,
        liquidity_score=0.9,
        event_risk=False,
        priority_score=1.0,
        feature_snapshot_id="snapshot-1",
    )
    state = PortfolioState(as_of=NOW + timedelta(seconds=1), starting_equity=5000, daily_realized_pnl=0)
    decision = DeterministicRiskAllocator(registry, policy).review([candidate], state)[0]
    assert decision.approved
    assert decision.to_dict()["order_intent_created"] is False
    assert decision.to_dict()["broker_action"] is None

    stale = CandidatePosition(
        **{
            **candidate.__dict__,
            "signal_id": "signal-2",
            "quote_time": NOW - timedelta(minutes=1),
            "candidate_id": "",
        }
    )
    rejected = DeterministicRiskAllocator(registry, policy).review([stale], state)[0]
    assert not rejected.approved
    assert "stale_quote" in rejected.reasons
