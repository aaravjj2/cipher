from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from core.research_platform.artifact_store import ArtifactStore
from core.research_platform.bootstrap import ResearchPlatform
from core.research_platform.cloud_deploy import (
    CONFIRMATION_TOKEN,
    CloudDeploymentService,
    CloudWriteBlockedError,
)
from core.research_platform.config import ResearchPlatformConfig
from core.research_platform.context_panel import (
    ContextInput,
    ContextMemoValidationError,
    ContextPanelService,
)
from core.research_platform.external_integrations import (
    DEFAULT_EXTERNAL_INTEGRATIONS,
    integration_status,
)
from core.research_platform.factors import FactorCandidate, FactorResearchService, SafeFactorCompiler, UnsafeFactorError
from core.research_platform.lean import LeanAuditValidator
from core.research_platform.local_capabilities import build_local_capability_report
from core.research_platform.local_scheduler import run_due
from core.research_platform.engine_adapters import EngineGateError, screen_vectorbt_buy_and_hold, screen_vectorbt_price_only_signal
from core.research_platform.market_quality import HoldoutCohortEligibility
from core.research_platform.repair_boundary import RepairBoundaryViolation, RepairRequest, authorize_repair
from core.research_platform.model_context import (
    ModelContextValidationError,
    build_model_context_assessment,
)
from core.research_platform.hashing import sha256_file
from core.research_platform.models import AllowedUse, PromotionState, StrategySpec
from core.research_platform.news import NewsDocument, NewsFeatureService, SentimentScore
from core.research_platform.portfolio import (
    DeterministicPortfolioOptimizer,
    PortfolioAsset,
    PortfolioOptimizationPolicy,
)
from core.research_platform.registry import ResearchRegistry
from core.research_platform.seven_layer_stack import (
    AutoResearchFeedbackLoop,
    ExecutionDelta,
    EventContext,
    ForecastAnomalyEngine,
    ForecastObservation,
    RealizedObservation,
    EightLayerStackSpec,
)
from core.research_platform.warehouse import BigQueryWarehousePlan
from core.timesfm_walkforward import base_ohlcv_context_forecast
from core.research_platform.huggingface_datasets import (
    OHLCV_1M,
    OPTIONS_IV_SP500,
    HuggingFaceDatasetError,
    download_approved_file,
)
from core.research_platform.local_market_catalog import IV_JOIN_LIMITATION, build_market_catalog
from core.research_platform.market_quality import (
    evaluate_market_days,
    require_eligible_market_day,
    require_price_only_market_day,
    require_holdout_c_cohort,
)
from core.research_platform.corporate_actions import capture_actions, normalize_actions
from core.research_platform import market_data_providers

NOW = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)


def strategy(name: str = "advanced") -> StrategySpec:
    return StrategySpec(
        name=name,
        version="v1",
        signal_rule={"rule": "test"},
        instrument_rule={"type": "defined_risk"},
        contract_selection_rule={},
        entry_rule={"timing": "test"},
        exit_rule={"timing": "test"},
        sizing_rule={"quantity": 1},
        portfolio_constraints={"maximum": 1},
        required_feature_ids=(),
        fill_model={"entry": "ask", "exit": "bid"},
        benchmark="control",
        statistical_plan={},
        promotion_thresholds={},
    )


def test_safe_factor_compiler_rejects_lookahead_and_evaluates_raw_columns(tmp_path: Path):
    candidate = FactorCandidate(
        name="momentum_z",
        version="v1",
        expression="zscore(pct_change(close, 1), 3)",
        hypothesis="short-horizon momentum normalized by recent variation",
        expected_direction="positive",
        availability_lag_seconds=60,
        missing_value_policy="unavailable",
        allowed_use=AllowedUse.CONTEXT,
    )
    compiled = SafeFactorCompiler().compile(candidate)
    values = compiled.evaluate({"close": np.asarray([100, 101, 102, 100, 104, 105], dtype=float)})
    assert values.shape == (6,)
    assert np.isfinite(values[-1])
    with pytest.raises(UnsafeFactorError):
        SafeFactorCompiler().compile(
            FactorCandidate(
                **{
                    **candidate.__dict__,
                    "expression": "lag(close, -1)",
                    "candidate_id": "",
                }
            )
        )
    with pytest.raises(UnsafeFactorError, match="unknown or derived"):
        SafeFactorCompiler().compile(
            FactorCandidate(
                **{
                    **candidate.__dict__,
                    "expression": "other_factor + close",
                    "candidate_id": "",
                }
            )
        )

    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    _, spec, artifact = FactorResearchService(registry, artifacts).register_candidate(candidate)
    assert spec.allowed_use == AllowedUse.CONTEXT
    assert artifact.artifact_id.startswith("artifact_")


class FakeSentiment:
    model_id = "fake-finbert-v1"

    def score(self, chunks):
        return [SentimentScore(positive=0.8, negative=0.1, neutral=0.1, model_id=self.model_id) for _ in chunks]


def test_news_pipeline_preserves_point_in_time_and_does_not_store_raw_text(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    service = NewsFeatureService(registry, artifacts)
    document = NewsDocument(
        source="test",
        external_id="article-1",
        title="A test filing",
        text="revenue increased and guidance improved " * 100,
        publication_time=NOW,
        received_at=NOW + timedelta(seconds=10),
        available_at=NOW + timedelta(seconds=15),
        symbols=("SPY",),
    )
    record, artifact = service.process(document, FakeSentiment(), chunk_words=64, overlap_words=8)
    assert record.high_magnitude
    assert record.available_at == NOW + timedelta(seconds=15)
    stored = json.loads(artifacts.get_bytes(artifact.sha256))
    assert stored["raw_text_stored"] is False
    assert "revenue increased" not in json.dumps(stored)
    assert registry.counts()["news_events"] == 1


def test_lean_validator_blocks_smoke_and_accepts_complete_audit():
    validator = LeanAuditValidator()
    blocked = validator.validate(
        {
            "schema_version": 1,
            "generated_at": NOW.isoformat(),
            "lean_version": "2.5",
            "strategy_id": "strategy_x",
            "dataset_id": "ds_x",
            "code_hash": "a" * 64,
            "date_range": {"start": NOW.isoformat(), "end": (NOW + timedelta(days=1)).isoformat()},
            "metrics": {"trade_count": 0},
            "trades": [],
            "quality_checks": {},
            "reconciliation": {},
            "research_grade": False,
        }
    )
    assert not blocked.promotable
    assert "zero_trade_result" in blocked.failures

    trade = {
        "trade_id": "t1",
        "symbol": "SPY_TEST",
        "entry_time": NOW.isoformat(),
        "exit_time": (NOW + timedelta(minutes=15)).isoformat(),
        "entry_price": 1.0,
        "exit_price": 1.2,
        "entry_bid": 0.99,
        "entry_ask": 1.0,
        "exit_bid": 1.2,
        "exit_ask": 1.21,
        "quantity": 1,
    }
    passed = validator.validate(
        {
            "schema_version": 1,
            "generated_at": NOW.isoformat(),
            "lean_version": "2.5",
            "strategy_id": "strategy_x",
            "dataset_id": "ds_x",
            "code_hash": "b" * 64,
            "date_range": {"start": NOW.isoformat(), "end": (NOW + timedelta(days=1)).isoformat()},
            "metrics": {"trade_count": 1},
            "trades": [trade],
            "quality_checks": {
                "point_in_time_data": True,
                "chronological_event_loop": True,
                "contract_selection_audited": True,
                "fill_evidence_observed": True,
                "historical_nbbo_required": True,
                "historical_nbbo_present": True,
                "corporate_actions_handled": True,
                "survivorship_bias_controlled": True,
            },
            "reconciliation": {
                "passed": True,
                "cash_ledger_balanced": True,
                "position_ledger_balanced": True,
                "order_fill_counts_match": True,
                "fees_included": True,
                "slippage_included": True,
            },
            "research_grade": True,
            "benchmark": {"return": 0.0},
            "regime_metrics": {"normal": {}},
            "statistical_tests": {"adjusted_p_value": 0.04},
        }
    )
    assert passed.promotable


def test_context_panel_forbids_trade_authority(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    service = ContextPanelService(registry, artifacts)
    context = ContextInput(
        strategy_id="strategy-1",
        signal_id="signal-1",
        symbol="SPY",
        decision_time=NOW,
        deterministic_signal={"direction": "bullish"},
        feature_snapshots=(),
        news_events=(),
        anomaly_events=(),
        risk_state={},
    )
    with pytest.raises(ContextMemoValidationError, match="forbidden"):
        service.record(
            context,
            model_id="test-model",
            output={"summary": "context", "position_size": 5},
        )
    memo, _ = service.record(
        context,
        model_id="test-model",
        output={
            "summary": "Signals conflict with the event context.",
            "conflicts": ["forecast_vs_news"],
            "event_tags": ["macro"],
            "uncertainty": ["limited sample"],
            "manual_review_priority": "high",
        },
    )
    assert memo.to_dict()["trade_authority"] is False


def test_cloud_load_gate_is_disabled_by_default_and_idempotent(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    config = ResearchPlatformConfig(
        repository_root=root,
        registry_path=root / "data" / "registry.sqlite",
        artifact_root=root / "data" / "artifacts",
        raw_lake_root=root / "data" / "raw",
        snapshot_root=root / "data" / "snapshots",
        warehouse_export_root=root / "data" / "exports",
        inventory_output_path=root / "data" / "inventory.json",
        bigquery_project="project",
        bigquery_dataset="cipher_research",
        gcs_bucket="bucket",
        cloud_writes_enabled=False,
    )
    platform = ResearchPlatform(config)
    source = root / "batch.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"record_id":"one","available_at":"2026-08-01T15:00:00+00:00"}\n')
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "exports": [
                    {
                        "export_id": "warehouse_export_test",
                        "table": "market_bars",
                        "row_count": 1,
                        "jsonl_path": str(source),
                        "sha256": sha256_file(source),
                        "artifact_id": "artifact_test",
                    }
                ]
            }
        )
    )
    with pytest.raises(CloudWriteBlockedError, match="disabled"):
        CloudDeploymentService(platform).load_export_manifest(
            manifest,
            confirmation=CONFIRMATION_TOKEN,
        )

    enabled = ResearchPlatformConfig(
        **{
            **config.__dict__,
            "cloud_writes_enabled": True,
        }
    )
    enabled_platform = ResearchPlatform(enabled)
    monkeypatch.setattr("core.research_platform.cloud_deploy.shutil.which", lambda _: "/usr/bin/bq")

    class Completed:
        returncode = 0
        stdout = "loaded"
        stderr = ""

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr("core.research_platform.cloud_deploy.subprocess.run", fake_run)
    service = CloudDeploymentService(enabled_platform)
    first = service.load_export_manifest(manifest, confirmation=CONFIRMATION_TOKEN)
    second = service.load_export_manifest(manifest, confirmation=CONFIRMATION_TOKEN)
    assert first["loads"][0]["status"] == "LOADED"
    assert second["loads"][0]["status"] == "SKIPPED_ALREADY_LOADED"
    assert len(calls) == 1
    assert enabled_platform.registry.counts()["warehouse_loads"] == 1


def test_portfolio_optimizer_is_simulation_only(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    first = strategy("one")
    second = strategy("two")
    registry.register_strategy(first)
    registry.register_strategy(second)
    policy = PortfolioOptimizationPolicy(
        objective="minimum_variance",
        maximum_sector_weight=1.0,
        maximum_correlation_bucket_weight=1.0,
        required_promotion_state=PromotionState.IDEA,
    )
    optimizer = DeterministicPortfolioOptimizer(registry, policy)
    assets = (
        PortfolioAsset("AAA", first.strategy_id, 0.01, 0.8, "tech", "growth"),
        PortfolioAsset("BBB", second.strategy_id, 0.005, 0.8, "finance", "value"),
    )
    returns = np.asarray(
        [
            [0.01, 0.005],
            [-0.005, 0.002],
            [0.006, -0.001],
            [0.002, 0.003],
        ]
    )
    proposal = optimizer.optimize(assets, returns, as_of=NOW)
    assert abs(sum(proposal.weights.values()) + proposal.cash_weight - 1.0) < 1e-8
    assert proposal.to_dict()["simulation_only"] is True
    assert proposal.to_dict()["order_intents"] == []


def test_eight_layer_stack_boundary_and_bigquery_topology(tmp_path: Path):
    spec = EightLayerStackSpec.default()
    assert len(spec.layers) == 8
    assert spec.layers[3].name == "attribution_and_anomaly_engine"
    assert spec.layers[6].name == "shadow_and_paper_execution"
    assert spec.layers[7].name == "evidence_feedback_loop"
    assert spec.validate_boundaries() == ()
    plan = spec.offline_orchestration_plan()
    assert plan["maximum_promotion_state"] == "LIVE_REVIEW_REQUIRED"
    assert all(step["live_order_authority"] is False for step in plan["steps"])
    assert "anomaly_log" in spec.warehouse_tables
    assert "autoresearch_feedback" in spec.warehouse_tables

    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    warehouse = BigQueryWarehousePlan(
        project="project",
        dataset="cipher_research",
        export_root=tmp_path / "exports",
        artifact_store=artifacts,
        registry=registry,
    )
    ddl = warehouse.ddl()
    assert "CREATE TABLE IF NOT EXISTS `project.cipher_research.anomaly_log`" in ddl
    assert "CREATE TABLE IF NOT EXISTS `project.cipher_research.autoresearch_feedback`" in ddl
    assert "CLUSTER BY `symbol`, `severity`, `allowed_use`" in ddl


def test_local_capability_report_keeps_execution_disabled(tmp_path: Path):
    report = build_local_capability_report(tmp_path, external_root=tmp_path / "external")
    assert report["safe_for_local_research"]
    assert report["execution_boundary"] == {
        "live_execution": False,
        "maximum_promotion_state": "LIVE_REVIEW_REQUIRED",
        "order_authority": False,
    }
    assert "kronos" in report["models"]
    assert "timesfm" in report["models"]
    assert report["models"]["timesfm"]["ready_for_prospective_forecast"] is False


def test_local_scheduler_records_blocked_jobs_without_execution(tmp_path: Path):
    capabilities = build_local_capability_report(tmp_path, external_root=tmp_path / "external")
    result = run_due(capabilities, tmp_path / "scheduler.json", now=NOW)
    assert len(result["last_run"]) == 4
    assert any(event["status"] == "ready_for_manual_research_run" for event in result["last_run"])
    assert any(event["status"] == "blocked" for event in result["last_run"])
    assert all(event["live_order_authority"] is False for event in result["last_run"])
    assert any(event["blocker"] == "full_volume_gate_reference_scope_unresolved" for event in result["last_run"])


def test_vectorbt_adapter_refuses_uncleared_holdout_c(tmp_path: Path):
    cohort = HoldoutCohortEligibility(source_count=1, common_tickers=9, strict_independent_origins=6)
    with pytest.raises(EngineGateError, match="Holdout C gate"):
        screen_vectorbt_buy_and_hold([100.0, 101.0], cohort, ArtifactStore(tmp_path / "artifacts"))


def test_vectorbt_price_only_contract_uses_same_promotion_path_without_volume(tmp_path: Path):
    result = screen_vectorbt_price_only_signal(
        [100.0, 101.0, 102.0, 101.0],
        [True, False, False, False],
        [False, False, True, False],
        ArtifactStore(tmp_path / "artifacts"),
        strategy_id="price_only_strategy",
        dataset_id="price_only_dataset",
    )
    payload = json.loads(Path(result.artifact.data_path).read_text(encoding="utf-8"))
    assert payload["data_scope"] == "price_only"
    assert payload["volume_features"] is False
    assert payload["promotion_path"] == "same_ordered_states_as_other_validated_strategies"
    assert payload["promotion_eligible_now"] is False
    assert payload["execution_authority"] is False


@pytest.mark.parametrize("changes", [
    {"cohort_id": "replacement"},
    {"promotion_decision": "PAPER_ELIGIBLE"},
    {"replacement_data": "alternate_vendor"},
    {"minimum_strict_independent_origins": 6},
])
def test_repair_boundary_rejects_evidence_and_gate_mutation(changes):
    with pytest.raises(RepairBoundaryViolation):
        authorize_repair(RepairRequest("retry_transient_delivery", "market_bars", changes))


def test_base_timesfm_context_is_never_promotion_eligible():
    class FakeTimesFM:
        def forecast(self, *, horizon, inputs):
            assert horizon == 3
            assert len(inputs) == 1
            return [[101.0, 102.0, 103.0]], None

    result = base_ohlcv_context_forecast(
        [float(index) for index in range(32)],
        horizon=3,
        model_loader=lambda _model_id, _context, _horizon: FakeTimesFM(),
    )
    assert result["point_forecast"] == [101.0, 102.0, 103.0]
    assert result["promotion_eligible"] is False
    assert result["live_execution"] is False


def test_model_context_only_reports_agreement_for_matching_horizons():
    assessment = build_model_context_assessment(
        last_close=100.0,
        timesfm={"model_id": "timesfm-test", "horizon": 3, "point_forecast": [100.1, 100.2, 101.0]},
        kronos={"model_id": "kronos-test", "available": True, "pred_bars": 3, "pred_return_pct": 0.5},
        source_end=NOW - timedelta(minutes=10),
        assessed_at=NOW,
    )
    assert assessment["context_status"] == "directional_agreement"
    assert assessment["data_freshness"]["status"] == "fresh"
    assert assessment["models"]["timesfm"]["direction"] == "long"
    assert assessment["actionable"] is False
    assert assessment["promotion_eligible"] is False
    assert assessment["live_execution"] is False


def test_model_context_rejects_horizon_comparison_and_bad_values():
    mismatch = build_model_context_assessment(
        last_close=100.0,
        timesfm={"horizon": 2, "point_forecast": [99.0, 99.0]},
        kronos={"available": True, "pred_bars": 3, "pred_return_pct": -1.0},
    )
    assert mismatch["context_status"] == "horizon_mismatch"
    stale = build_model_context_assessment(
        last_close=100.0,
        source_end=NOW - timedelta(days=2),
        assessed_at=NOW,
    )
    assert stale["data_freshness"]["status"] == "stale"
    with pytest.raises(ModelContextValidationError, match="last_close must be positive"):
        build_model_context_assessment(last_close=0.0)
    with pytest.raises(ModelContextValidationError, match="source_end cannot be after assessed_at"):
        build_model_context_assessment(
            last_close=100.0,
            source_end=NOW + timedelta(minutes=1),
            assessed_at=NOW,
        )


def test_huggingface_ingestion_restricts_files_and_preserves_revision(tmp_path: Path):
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["local_dir"]) / kwargs["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("symbol,date\nSPY,2026-01-01\n", encoding="utf-8")
        return str(target)

    downloaded = download_approved_file(
        OPTIONS_IV_SP500,
        "data_IV_USA.csv",
        destination_root=tmp_path,
        downloader=fake_download,
    )
    assert downloaded.is_file()
    assert calls[0]["revision"] == OPTIONS_IV_SP500.revision
    with pytest.raises(HuggingFaceDatasetError, match="not approved"):
        download_approved_file(
            OPTIONS_IV_SP500,
            "unexpected.csv",
            destination_root=tmp_path,
            downloader=fake_download,
        )
    with pytest.raises(HuggingFaceDatasetError, match="monthly"):
        download_approved_file(
            OHLCV_1M,
            "README.md",
            destination_root=tmp_path,
            downloader=fake_download,
        )


def test_local_market_catalog_exposes_raw_files_without_copying_them(tmp_path: Path):
    iv = tmp_path / "iv.csv"
    iv.write_text("symbol,date,ATM_IV\nSPY,2020-01-02,20.0\n", encoding="utf-8")
    parquet = tmp_path / "bars.parquet"
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(
        pa.table({
            "timestamp": ["2020-01-02T09:30:00-05:00"],
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
            "volume": [100], "ticker": ["SPY"],
        }),
        parquet,
    )
    catalog = build_market_catalog(tmp_path / "market.duckdb", iv_csv=iv, ohlcv_parquet_files=[parquet])
    import duckdb

    with duckdb.connect(str(catalog), read_only=True) as db:
        assert db.execute("select count(*) from cipher_market.ohlcv_1m").fetchone()[0] == 1
        assert db.execute("select value from cipher_market.catalog_metadata").fetchone()[0] == IV_JOIN_LIMITATION


def test_market_quality_requires_complete_session_and_reconciled_volume():
    accepted = require_eligible_market_day(
        observed_bars=391, observed_volume=1000, reference_volume=1040
    )
    assert accepted["eligible"] is True
    rejected = evaluate_market_days([
        {"observed_bars": 390, "observed_volume": 1000, "reference_volume": 1000},
        {"observed_bars": 391, "observed_volume": 1000, "reference_volume": None},
        {"observed_bars": 391, "observed_volume": 1200, "reference_volume": 1000},
    ])
    assert [row["eligible"] for row in rejected] == [False, False, False]
    assert rejected[0]["session"]["reason"] == "incomplete_or_duplicate_regular_session"
    assert rejected[1]["volume"]["reason"] == "unreconciled_or_materially_different_volume"


def test_price_only_gate_never_claims_volume_sensitive_eligibility():
    accepted = require_price_only_market_day(
        observed_bars=391, close_ratio_to_previous_session=1.15
    )
    assert accepted["eligible"] is True
    assert accepted["allowed_use"] == "price_forecast_research_only_no_volume_features"
    assert accepted["volume_sensitive_use"] is False
    assert require_price_only_market_day(
        observed_bars=390, close_ratio_to_previous_session=1.0
    )["eligible"] is False
    assert require_price_only_market_day(
        observed_bars=391, close_ratio_to_previous_session=0.25
    )["price_continuity"]["reason"] == "missing_or_split_like_price_discontinuity"


def test_holdout_c_gate_requires_single_source_and_full_minimums():
    assert require_holdout_c_cohort(
        source_count=1, common_tickers=8, strict_independent_origins=12,
    )["eligible"]
    mixed = require_holdout_c_cohort(
        source_count=2, common_tickers=9, strict_independent_origins=14,
    )
    assert not mixed["eligible"]
    assert "single_source_required" in mixed["cohort"]["reasons"]
    undersized = require_holdout_c_cohort(
        source_count=1, common_tickers=8, strict_independent_origins=6,
    )
    assert not undersized["eligible"]
    assert "insufficient_strict_independent_origins" in undersized["cohort"]["reasons"]


def test_corporate_action_snapshot_is_reference_only():
    import pandas as pd

    actions = pd.DataFrame(
        {"Stock Splits": [0.0, 4.0], "Dividends": [0.82, 0.0]},
        index=pd.to_datetime(["2020-08-07", "2020-08-31"]),
    )
    payload = capture_actions(["aapl"], fetch_actions=lambda _symbol: actions, retrieved_at=NOW)
    assert len(payload["rows"]) == 2
    assert payload["point_in_time_ready"] is False
    assert all(row["adjustment_authorized"] is False for row in payload["rows"])
    assert normalize_actions("AAPL", None) == []


def test_read_only_provider_adapters_normalize_action_and_daily_payloads(monkeypatch):
    monkeypatch.setenv("ALPACA_ALGO_KEY", "test-key")
    monkeypatch.setenv("ALPACA_ALGO_SECRET", "test-secret")
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "test-token")
    calls = []

    def fake_request(url, headers):
        calls.append((url, headers))
        if "alpaca" in url:
            return {"corporate_actions": {"forward_splits": [{"symbol": "AAPL", "new_rate": 4, "old_rate": 1}]}}
        return {"history": {"day": {"date": "2020-08-31", "volume": 225702690}}}

    actions = market_data_providers.fetch_alpaca_corporate_actions(
        ["aapl"], start="2020-08-01", end="2020-09-02", request_json=fake_request
    )
    bars = market_data_providers.fetch_tradier_daily_history(
        "aapl", start="2020-08-01", end="2020-08-31", request_json=fake_request
    )
    assert actions[0]["action_type"] == "forward_splits"
    assert actions[0]["point_in_time_ready"] is False
    assert bars == [{"date": "2020-08-31", "volume": 225702690}]
    assert all("order" not in url for url, _headers in calls)


def test_massive_adapter_is_read_only_and_normalizes_minute_payload(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    calls = []

    def fake_request(url, headers):
        calls.append((url, headers))
        return {"status": "OK", "results": [{"t": 1483453800000, "o": 115.8, "h": 116.2, "l": 115.7, "c": 116.1, "v": 1200, "n": 8, "vw": 115.95}]}

    bars = market_data_providers.fetch_massive_minute_bars(
        "aapl", start="2017-01-03", end="2017-01-03", request_json=fake_request,
    )
    assert bars == [{"provider": "massive_polygon", "ticker": "AAPL", "timestamp_ms": 1483453800000,
                     "open": 115.8, "high": 116.2, "low": 115.7, "close": 116.1, "volume": 1200,
                     "trade_count": 8, "vwap": 115.95, "adjusted": False}]
    assert len(calls) == 1
    assert "/range/1/minute/2017-01-03/2017-01-03" in calls[0][0]
    assert "order" not in calls[0][0]


def test_forecast_anomaly_engine_outputs_context_only_rows():
    forecast = ForecastObservation(
        forecast_id="forecast_spy_1",
        symbol="spy",
        event_time=NOW,
        available_at=NOW + timedelta(minutes=1),
        lower_bound=99.0,
        upper_bound=101.0,
        point_forecast=100.0,
    )
    realized = RealizedObservation(
        symbol="SPY",
        event_time=NOW,
        available_at=NOW + timedelta(minutes=5),
        value=104.0,
    )
    event = EventContext(
        event_id="event_macro_1",
        symbols=("SPY", "QQQ"),
        event_time=NOW - timedelta(hours=2),
        available_at=NOW - timedelta(hours=1),
        event_type="macro",
        sentiment=-0.7,
    )
    anomalies = ForecastAnomalyEngine(event_window=timedelta(hours=6)).evaluate(
        [forecast],
        [realized],
        [event],
    )
    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert anomaly.severity == "extreme"
    assert anomaly.linked_event_ids == ("event_macro_1",)
    row = anomaly.to_warehouse_row()
    assert row["allowed_use"] == "context"
    assert row["source"] == "attribution_and_anomaly_engine"
    assert row["available_at"] == realized.available_at
    assert row["payload_json"]["linked_event_ids"] == ["event_macro_1"]


def test_autoresearch_feedback_routes_to_validation_not_live():
    anomaly = ForecastAnomalyEngine().evaluate(
        [
            ForecastObservation(
                forecast_id="forecast_q",
                symbol="QQQ",
                event_time=NOW,
                available_at=NOW,
                lower_bound=10.0,
                upper_bound=11.0,
            )
        ],
        [
            RealizedObservation(
                symbol="QQQ",
                event_time=NOW,
                available_at=NOW + timedelta(minutes=1),
                value=9.0,
            )
        ],
    )[0]
    packet = AutoResearchFeedbackLoop().build_packet(
        anomalies=[anomaly],
        execution_deltas=[
            ExecutionDelta(
                strategy_id="strategy_q",
                backtest_return=0.05,
                observed_return=-0.01,
                max_slippage_bps=35.0,
                sample_count=12,
            )
        ],
    )
    row = packet.to_warehouse_row()
    assert row["routes_to_live"] is False
    assert row["target_layer"] == "multi_paradigm_backtesting_gate"
    assert row["bandit_updates_json"][0]["next_action_bias"] == "explore_factor_space"
    assert row["bandit_updates_json"][0]["routes_to_layer"] == 5
    assert row["prompt_revisions_json"][0]["direct_live_update"] is False


def test_external_repos_are_registered_and_blocked_from_live_runtime(tmp_path: Path):
    for integration in DEFAULT_EXTERNAL_INTEGRATIONS:
        (tmp_path / integration.relative_path).mkdir(parents=True)
    status = integration_status(tmp_path)
    names = {item["name"] for item in status["integrations"]}
    assert {
        "MiroFish",
        "TradingAgents",
        "Dexter",
        "financial-services",
        "daily_stock_analysis",
        "PolyMarket-MCP",
        "polymarket-mcp-server",
        "alpaca-mcp-server",
    } <= names
    assert status["available_count"] == status["total_count"]
    assert status["boundary_violations"] == []
    assert status["usable_now"] is True
    for item in status["integrations"]:
        assert item["live_runtime_enabled"] is False
        assert item["broker_order_authority"] is False
        assert "execution" not in item["allowed_use"]
    blocked = {item["name"]: set(item["blocked_capabilities"]) for item in status["integrations"]}
    assert "place_option_order" in blocked["alpaca-mcp-server"]
    assert "execute_smart_trade" in blocked["polymarket-mcp-server"]
    assert "direct_runtime_prompt_mutation" in blocked["MiroFish"]
