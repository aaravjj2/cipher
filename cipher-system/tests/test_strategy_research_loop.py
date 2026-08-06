from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from core.research_platform.artifact_store import ArtifactStore
from core.research_platform.hashing import sha256_file
from core.research_platform.models import DataDisposition, DatasetManifest, RawObjectManifest
from core.research_platform.registry import ResearchRegistry
from core.research_platform.strategy_research_loop import (
    CanonicalPanel,
    StrategyCandidate,
    StrategyResearchPolicy,
    _simulate_symbol,
    adaptive_neighbours,
    default_candidate_catalog,
    holm_adjust,
    load_canonical_daily_panel,
    run_candidate_backtest,
    run_research_cycle,
    strategy_spec,
)


def synthetic_daily(days: int = 540, symbols: tuple[str, ...] = ("SPY", "QQQ", "AAPL")) -> pd.DataFrame:
    start = pd.Timestamp("2022-01-03")
    dates = pd.bdate_range(start, periods=days)
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        base = 100.0 + symbol_index * 20.0
        trend = np.linspace(0.0, 30.0 + symbol_index * 4.0, days)
        cycle = np.sin(np.arange(days) / (8.0 + symbol_index)) * (2.0 + symbol_index * 0.3)
        close = base + trend + cycle
        open_price = close * (1.0 + 0.001 * np.cos(np.arange(days) / 3.0))
        high = np.maximum(open_price, close) * 1.005
        low = np.minimum(open_price, close) * 0.995
        for index, date in enumerate(dates):
            rows.append(
                {
                    "date": date,
                    "ticker": symbol,
                    "open": float(open_price[index]),
                    "high": float(high[index]),
                    "low": float(low[index]),
                    "close": float(close[index]),
                    "bars": 391,
                }
            )
    return pd.DataFrame(rows)


def test_seed_catalog_is_bounded_diverse_and_unique():
    catalog = default_candidate_catalog()
    assert len(catalog) == 84
    assert len({item.candidate_id for item in catalog}) == len(catalog)
    assert {item.family for item in catalog} == {
        "sma_trend",
        "donchian_breakout",
        "rsi_reversion",
        "bollinger_reversion",
        "trend_pullback",
        "low_vol_breakout",
        "ema_trend",
        "time_series_momentum",
        "keltner_breakout",
        "regime_switch",
        "ensemble_vote",
        "short_term_reversal",
        "cross_sectional_momentum",
        "cross_sectional_reversal",
        "risk_on_rotation",
        "cross_sectional_low_vol",
        "pair_zscore",
    }
    assert all(item.generation == 0 for item in catalog)


def test_holm_adjustment_is_monotone_and_not_smaller_than_raw_values():
    raw = {"a": 0.01, "b": 0.03, "c": 0.20}
    adjusted = holm_adjust(raw)
    assert adjusted["a"] == 0.03
    assert adjusted["b"] == 0.06
    assert adjusted["c"] == 0.20
    assert all(adjusted[key] >= raw[key] for key in raw)


def test_signal_execution_is_shifted_to_next_session_open():
    dates = pd.bdate_range("2025-01-02", periods=6)
    bars = pd.DataFrame(
        {
            "open": [100, 101, 102, 103, 104, 105],
            "high": [101, 102, 103, 104, 105, 106],
            "low": [99, 100, 101, 102, 103, 104],
            "close": [100, 101, 102, 103, 104, 105],
        },
        index=dates,
    )
    desired_at_close = pd.Series([False, True, True, False, False, False], index=dates)
    result = _simulate_symbol("SPY", bars, desired_at_close, 0.0)
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade.entry_time == dates[2].isoformat()
    assert trade.exit_time == dates[4].isoformat()
    assert trade.entry_price == 102.0
    assert trade.exit_price == 104.0


def test_candidate_backtest_records_exploratory_boundaries():
    panel = CanonicalPanel(
        dataset_id="ds_test",
        dataset_name="synthetic",
        frame=synthetic_daily(),
        raw_object_count=1,
        source_paths=("synthetic.parquet",),
        lineage_hash="lineage_test",
    )
    candidate = StrategyCandidate("sma_trend", {"fast": 10, "slow": 50}, hypothesis="test")
    policy = StrategyResearchPolicy(batch_size=1, minimum_sessions=500, minimum_trades=1)
    result = run_candidate_backtest(panel, candidate, policy)
    assert result.output.quality_checks["point_in_time_validated"] is True
    assert result.output.quality_checks["next_session_open_execution"] is True
    assert result.output.quality_checks["volume_features"] is False
    assert result.output.quality_checks["final_holdout_claim"] is False
    assert result.output.quality_checks["automatic_promotion"] is False
    assert isinstance(result.output.quality_checks["walk_forward_passed"], bool)
    assert result.output.assumptions["research_role"] == "exploratory_development_only_not_final_holdout"
    assert len(result.fold_returns_pct) == 3


def test_phase_two_market_neutral_candidate_uses_cash_benchmark_and_next_open_execution():
    panel = CanonicalPanel(
        dataset_id="ds_test",
        dataset_name="synthetic",
        frame=synthetic_daily(),
        raw_object_count=1,
        source_paths=("synthetic.parquet",),
        lineage_hash="lineage_test",
    )
    candidate = StrategyCandidate(
        "cross_sectional_momentum",
        {
            "lookback": 20,
            "skip": 0,
            "top_k": 1,
            "rebalance": 5,
            "long_short": True,
            "minimum_momentum": 0.0,
        },
    )
    result = run_candidate_backtest(
        panel,
        candidate,
        StrategyResearchPolicy(batch_size=1, minimum_sessions=500, minimum_trades=1),
    )
    assert result.output.quality_checks["phase_two_family"] is True
    assert result.output.quality_checks["panel_strategy"] is True
    assert result.output.quality_checks["market_neutral"] is True
    assert result.output.benchmark_metrics["benchmark"] == "cash_zero_return"
    assert result.output.assumptions["strategy_scope"] == "panel"
    assert all(trade.metadata["execution"] == "next_session_open" for trade in result.output.trades)


def test_phase_two_single_symbol_candidate_remains_long_only():
    panel = CanonicalPanel(
        dataset_id="ds_test",
        dataset_name="synthetic",
        frame=synthetic_daily(),
        raw_object_count=1,
        source_paths=("synthetic.parquet",),
        lineage_hash="lineage_test",
    )
    candidate = StrategyCandidate("ema_trend", {"fast": 10, "slow": 60, "confirmation": 0})
    result = run_candidate_backtest(
        panel,
        candidate,
        StrategyResearchPolicy(batch_size=1, minimum_sessions=500, minimum_trades=1),
    )
    assert result.output.quality_checks["phase_two_family"] is True
    assert result.output.quality_checks["panel_strategy"] is False
    assert result.output.benchmark_metrics["benchmark"] == "SPY_open_to_open"
    assert all(trade.direction == "long" for trade in result.output.trades)


def test_evaluation_window_excludes_warmup_from_single_symbol_metrics():
    frame = synthetic_daily()
    dates = sorted(pd.Timestamp(value) for value in frame["date"].unique())
    evaluation_start = dates[-100]
    evaluation_end = dates[-1]
    panel = CanonicalPanel(
        dataset_id="ds_window",
        dataset_name="synthetic_window",
        frame=frame,
        raw_object_count=1,
        source_paths=("synthetic.parquet",),
        lineage_hash="lineage_window",
        evaluation_start=evaluation_start.isoformat(),
        evaluation_end=evaluation_end.isoformat(),
    )
    result = run_candidate_backtest(
        panel,
        StrategyCandidate("sma_trend", {"fast": 10, "slow": 50}),
        StrategyResearchPolicy(batch_size=1, minimum_sessions=100, minimum_trades=1),
    )
    equity_dates = [pd.Timestamp(point.timestamp) for point in result.output.equity_curve]
    assert min(equity_dates) >= evaluation_start
    assert max(equity_dates) <= evaluation_end
    assert all(pd.Timestamp(trade.entry_time) >= evaluation_start for trade in result.output.trades)
    assert result.output.quality_checks["evaluation_window_enforced"] is True
    assert result.output.assumptions["warmup_data_excluded_from_metrics"] is True


def test_evaluation_window_resets_phase_two_panel_positions():
    frame = synthetic_daily(symbols=("SPY", "QQQ", "AAPL", "MSFT"))
    dates = sorted(pd.Timestamp(value) for value in frame["date"].unique())
    evaluation_start = dates[-100]
    evaluation_end = dates[-1]
    panel = CanonicalPanel(
        dataset_id="ds_window_panel",
        dataset_name="synthetic_window_panel",
        frame=frame,
        raw_object_count=1,
        source_paths=("synthetic.parquet",),
        lineage_hash="lineage_window_panel",
        evaluation_start=evaluation_start.isoformat(),
        evaluation_end=evaluation_end.isoformat(),
    )
    candidate = StrategyCandidate(
        "cross_sectional_reversal",
        {"lookback": 5, "top_k": 1, "rebalance": 5, "long_short": False},
    )
    result = run_candidate_backtest(
        panel,
        candidate,
        StrategyResearchPolicy(batch_size=1, minimum_sessions=100, minimum_trades=1),
    )
    equity_dates = [pd.Timestamp(point.timestamp) for point in result.output.equity_curve]
    assert min(equity_dates) >= evaluation_start
    assert all(pd.Timestamp(trade.entry_time) >= evaluation_start for trade in result.output.trades)
    assert result.output.quality_checks["evaluation_window_enforced"] is True
    assert result.output.quality_checks["panel_strategy"] is True


def test_strategy_spec_has_no_execution_or_automatic_promotion_authority():
    candidate = StrategyCandidate("sma_trend", {"fast": 10, "slow": 50})
    spec = strategy_spec(candidate, StrategyResearchPolicy())
    assert spec.portfolio_constraints["live_execution"] is False
    assert spec.contract_selection_rule["options_contracts"] is False
    assert spec.signal_rule["research_role"] == "exploratory_development_only"
    assert "minimum_trades" in spec.promotion_thresholds
    assert "/v2/orders" not in json.dumps(spec.to_dict())


def test_adaptive_neighbours_are_bounded_and_identify_parent():
    parent = StrategyCandidate("sma_trend", {"fast": 10, "slow": 50})
    ranked = [
        {
            "candidate": parent.to_dict(),
            "verdict": "PASS",
            "composite_score": 5.0,
        }
    ]
    children = adaptive_neighbours(ranked, {parent.candidate_id}, StrategyResearchPolicy(maximum_adaptive_children_per_cycle=3))
    assert 1 <= len(children) <= 3
    assert all(child.generation == 1 for child in children)
    assert all(child.parent_candidate_id == parent.candidate_id for child in children)
    assert all(int(child.parameters["fast"]) < int(child.parameters["slow"]) for child in children)


def register_synthetic_dataset(tmp_path: Path) -> tuple[Path, str]:
    registry_path = tmp_path / "registry.sqlite"
    registry = ResearchRegistry(registry_path)
    data_path = tmp_path / "panel.parquet"
    daily = synthetic_daily()
    minute_like = daily.rename(columns={"date": "timestamp"}).copy()
    minute_like["timestamp"] = pd.to_datetime(minute_like["timestamp"], utc=True)
    minute_like.to_parquet(data_path, index=False)
    observed = datetime.now(timezone.utc) - timedelta(days=1)
    raw = RawObjectManifest(
        source="synthetic",
        dataset="synthetic_strategy_research",
        uri=data_path.resolve().as_uri(),
        checksum=sha256_file(data_path),
        checksum_method="sha256",
        size_bytes=data_path.stat().st_size,
        received_at=observed,
        available_at=observed,
        ingestion_run_id="synthetic_run",
        request_metadata={"normalized": True},
        disposition=DataDisposition.IMMUTABLE_RAW,
    )
    registry.register_raw_object(raw)
    manifest = DatasetManifest(
        name="synthetic_strategy_research",
        created_at=datetime.now(timezone.utc),
        availability_cutoff=observed,
        sources=("synthetic",),
        raw_object_ids=(raw.raw_object_id,),
        symbol_universe_id="synthetic_three_symbols",
        corporate_action_version="synthetic_v1",
        normalizer_version="synthetic_v1",
        schema_name="daily_ohlc_v1",
        row_counts={"sessions": 540, "symbols": 3},
        quality_checks={"passed": True},
        frozen=True,
    )
    registry.register_dataset(manifest)
    return registry_path, manifest.dataset_id


def test_canonical_loader_and_cycle_register_real_experiments(tmp_path: Path):
    registry_path, dataset_id = register_synthetic_dataset(tmp_path)
    panel = load_canonical_daily_panel(registry_path, dataset_id, cache_root=tmp_path / "cache")
    assert panel.dataset_id == dataset_id
    assert panel.raw_object_count == 1
    assert panel.frame["date"].nunique() == 540
    assert set(panel.frame["ticker"]) == {"SPY", "QQQ", "AAPL"}

    payload = run_research_cycle(
        registry_path=registry_path,
        artifact_root=tmp_path / "artifacts",
        state_path=tmp_path / "state.json",
        output_root=tmp_path / "runs",
        cache_root=tmp_path / "cache",
        dataset_id=dataset_id,
        policy=StrategyResearchPolicy(
            batch_size=2,
            maximum_total_candidates=4,
            minimum_sessions=500,
            minimum_trades=1,
            maximum_adaptive_children_per_cycle=1,
        ),
        now=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    assert payload["status"] == "completed"
    assert len(payload["results"]) == 2
    assert payload["claims"]["strategy_discovery_active"] is True
    assert payload["claims"]["automatic_promotion"] is False
    assert payload["claims"]["live_execution"] is False

    with ResearchRegistry(registry_path).connect() as db:
        assert db.execute("select count(*) from strategies").fetchone()[0] == 2
        assert db.execute("select count(*) from experiments where status = 'COMPLETED'").fetchone()[0] == 2
        assert db.execute("select count(*) from promotion_events").fetchone()[0] == 0


def test_exhausted_cycle_preserves_total_candidate_count(tmp_path: Path):
    registry_path, dataset_id = register_synthetic_dataset(tmp_path)
    policy = StrategyResearchPolicy(
        batch_size=2,
        maximum_total_candidates=2,
        minimum_sessions=500,
        minimum_trades=1,
        maximum_adaptive_children_per_cycle=1,
    )
    common = {
        "registry_path": registry_path,
        "artifact_root": tmp_path / "artifacts",
        "state_path": tmp_path / "state.json",
        "output_root": tmp_path / "runs",
        "cache_root": tmp_path / "cache",
        "dataset_id": dataset_id,
        "policy": policy,
    }
    first = run_research_cycle(**common, now=datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc))
    second = run_research_cycle(**common, now=datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc))
    assert first["tested_candidate_count_total"] == 2
    assert second["status"] == "catalog_exhausted_or_candidate_cap_reached"
    assert second["tested_candidate_count"] == 2
    assert second["tested_candidate_count_total"] == 2
    assert second["results"] == []
