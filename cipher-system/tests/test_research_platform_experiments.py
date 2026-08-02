from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.research_platform.artifact_store import ArtifactStore
from core.research_platform.experiments import (
    CallableExperimentAdapter,
    EquityPoint,
    ExperimentRunner,
    StandardBacktestOutput,
    TradeRecord,
    runtime_environment_id,
)
from core.research_platform.models import (
    DataDisposition,
    DatasetManifest,
    EngineKind,
    ExperimentManifest,
    ExperimentVerdict,
    RawObjectManifest,
    StrategySpec,
)
from core.research_platform.registry import ResearchRegistry

NOW = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)


def setup_registry(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    raw = RawObjectManifest(
        source="test",
        dataset="bars",
        uri="file:///tmp/bars.json",
        checksum="d" * 64,
        checksum_method="sha256",
        size_bytes=100,
        received_at=NOW,
        available_at=NOW,
        ingestion_run_id="ingest",
        disposition=DataDisposition.IMMUTABLE_RAW,
    )
    registry.register_raw_object(raw)
    dataset = DatasetManifest(
        name="bars",
        created_at=NOW + timedelta(seconds=1),
        availability_cutoff=NOW,
        sources=("test",),
        raw_object_ids=(raw.raw_object_id,),
        symbol_universe_id="u",
        corporate_action_version="ca",
        normalizer_version="n",
        schema_name="bars_v1",
        row_counts={"bars": 100},
        quality_checks={"passed": True},
    )
    registry.register_dataset(dataset)
    strategy = StrategySpec(
        name="test",
        version="v1",
        signal_rule={"rule": "momentum"},
        instrument_rule={"type": "equity"},
        contract_selection_rule={},
        entry_rule={"timing": "next_open"},
        exit_rule={"hold": 2},
        sizing_rule={"weight": 1.0},
        portfolio_constraints={"max": 1},
        required_feature_ids=(),
        fill_model={"slippage_bps": 10},
        benchmark="SPY",
        statistical_plan={"walk_forward": True},
        promotion_thresholds={
            "minimum_trades": 3,
            "minimum_profit_factor": 1.1,
            "maximum_drawdown_pct": 20,
            "maximum_adjusted_p_value": 0.05,
            "require_walk_forward": True,
            "required_quality_checks": ["point_in_time_validated"],
        },
    )
    registry.register_strategy(strategy)
    return registry, artifacts, dataset, strategy


def passing_output(_manifest: ExperimentManifest) -> StandardBacktestOutput:
    returns = (2.0, -0.5, 1.0, 0.5)
    trades = tuple(
        TradeRecord(
            trade_id=f"t{index}",
            symbol="SPY",
            direction="bullish",
            entry_time=(NOW + timedelta(days=index)).isoformat(),
            exit_time=(NOW + timedelta(days=index, hours=1)).isoformat(),
            entry_price=100,
            exit_price=100 + value,
            quantity=1,
            gross_pnl=value,
            net_pnl=value,
            return_pct=value,
        )
        for index, value in enumerate(returns)
    )
    equity = (
        EquityPoint(NOW.isoformat(), 1000),
        EquityPoint((NOW + timedelta(days=1)).isoformat(), 1020),
        EquityPoint((NOW + timedelta(days=2)).isoformat(), 1015),
        EquityPoint((NOW + timedelta(days=3)).isoformat(), 1030),
    )
    return StandardBacktestOutput(
        trades=trades,
        equity_curve=equity,
        metrics={"holdout_pnl": 15},
        benchmark_metrics={"total_return_pct": 1.0},
        regime_metrics={"normal": {"trades": 4}},
        statistical_tests={"holm_adjusted_p_value": 0.04},
        quality_checks={
            "passed": True,
            "point_in_time_validated": True,
            "walk_forward_passed": True,
        },
        exclusions=(),
        assumptions={"entry": "next open", "slippage_bps": 10},
    )


def test_experiment_runner_persists_standard_artifacts(tmp_path: Path):
    registry, artifacts, dataset, strategy = setup_registry(tmp_path)
    manifest = ExperimentManifest(
        strategy_id=strategy.strategy_id,
        dataset_id=dataset.dataset_id,
        feature_set_id="none",
        parameter_set={"lookback": 20},
        engine=EngineKind.CIPHER_FAST,
        code_hash="e" * 64,
        runtime_environment_id=runtime_environment_id(),
        random_seed=42,
        started_at=NOW + timedelta(minutes=1),
        hypothesis="momentum remains positive out of sample",
    )
    result = ExperimentRunner(registry=registry, artifact_store=artifacts).run(
        manifest,
        strategy=strategy,
        adapter=CallableExperimentAdapter(passing_output),
    )
    assert result.verdict == ExperimentVerdict.PASS
    assert result.metrics["trade_count"] == 4
    assert result.metrics["profit_factor"] > 1.1
    summary = registry.experiment_summary(manifest.experiment_id)
    assert summary["status"] == "COMPLETED"
    assert summary["verdict"] == "PASS"
    assert {item["role"] for item in summary["artifacts"]} == {
        "equity_curve",
        "gate_evaluation",
        "standard_result",
        "trades",
    }


def test_experiment_gate_rejects_underpowered_result(tmp_path: Path):
    registry, artifacts, dataset, strategy = setup_registry(tmp_path)
    manifest = ExperimentManifest(
        strategy_id=strategy.strategy_id,
        dataset_id=dataset.dataset_id,
        feature_set_id="none",
        parameter_set={},
        engine=EngineKind.CIPHER_FAST,
        code_hash="f" * 64,
        runtime_environment_id=runtime_environment_id(),
        random_seed=7,
        started_at=NOW + timedelta(minutes=2),
    )

    def weak(_manifest):
        return StandardBacktestOutput(
            trades=(),
            equity_curve=(),
            metrics={"trade_count": 0, "maximum_drawdown_pct": 50},
            benchmark_metrics={},
            regime_metrics={},
            statistical_tests={},
            quality_checks={"passed": True, "point_in_time_validated": True, "walk_forward_passed": False},
            exclusions=(),
            assumptions={},
        )

    result = ExperimentRunner(registry=registry, artifact_store=artifacts).run(
        manifest,
        strategy=strategy,
        adapter=CallableExperimentAdapter(weak),
    )
    assert result.verdict == ExperimentVerdict.FAIL
    assert "minimum_failed:trade_count" in result.quality_checks["gate_failures"]
    assert "walk_forward_failed" in result.quality_checks["gate_failures"]
