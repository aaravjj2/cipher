from __future__ import annotations

import csv
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core.research_platform.cipher_signal_overlay import (
    apply_signal_overlay_policy,
    default_signal_overlay_policies,
    load_signal_episodes,
    session_signal_features,
    write_immutable_signal_overlay_snapshot,
)
from core.research_platform.recent_regime import (
    RECENT_CANDIDATE_IDS,
    RecentGateSpec,
    RecentSelectorSpec,
    build_monthly_gate_weights,
    build_monthly_selector_weights,
    default_recent_gate_specs,
    default_recent_selector_specs,
)
from core.research_platform.recent_regime_prospective import write_immutable_prospective_snapshot
from core.research_platform.recent_regime_prospective_evaluation import evaluate_prospective_snapshots
from core.research_platform.recent_regime_refresh import _candidate_pool_fingerprint, data_refresh_due

from conftest import load_artifact, require_artifact

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recent_selector_grid_is_small_and_deterministic():
    specs = default_recent_selector_specs()
    assert len(specs) == 8
    assert len({spec.selector_id for spec in specs}) == 8
    assert {spec.mode for spec in specs} == {"dynamic", "family_balanced", "core_satellite"}
    assert len(RECENT_CANDIDATE_IDS) == 14
    assert len(set(RECENT_CANDIDATE_IDS)) == 14
    gates = default_recent_gate_specs()
    assert len(gates) == 8
    assert len({gate.gate_id for gate in gates}) == 8


def test_recent_pool_fingerprint_ignores_unrelated_matrix_changes(tmp_path: Path):
    source = json.loads(require_artifact("data/governance/cross_period_strategy_matrix.json", non_empty_key="matrix").read_text(encoding="utf-8"))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(source), encoding="utf-8")
    expected = _candidate_pool_fingerprint(baseline)

    unrelated = json.loads(json.dumps(source))
    unrelated_row = next(row for row in unrelated["matrix"] if row["candidate_id"] not in RECENT_CANDIDATE_IDS)
    unrelated_row["pass_count"] = int(unrelated_row.get("pass_count") or 0) + 1
    unrelated_path = tmp_path / "unrelated.json"
    unrelated_path.write_text(json.dumps(unrelated), encoding="utf-8")
    assert _candidate_pool_fingerprint(unrelated_path) == expected

    relevant = json.loads(json.dumps(source))
    relevant_row = next(row for row in relevant["matrix"] if row["candidate_id"] == RECENT_CANDIDATE_IDS[0])
    relevant_row["parameters"] = {**relevant_row["parameters"], "test_mutation": 1}
    relevant_path = tmp_path / "relevant.json"
    relevant_path.write_text(json.dumps(relevant), encoding="utf-8")
    assert _candidate_pool_fingerprint(relevant_path) != expected


def test_january_selection_uses_only_2025_training_data_and_weights_are_monthly_constant():
    index = pd.bdate_range("2025-08-01", "2026-03-31")
    index = index[index != pd.Timestamp("2026-01-01")]
    returns = pd.DataFrame(0.0, index=index, columns=["trend", "reversion", "passive_spy"])
    returns.loc[returns.index < pd.Timestamp("2026-01-01"), "trend"] = 0.001
    returns.loc[returns.index < pd.Timestamp("2026-01-01"), "reversion"] = -0.001
    returns.loc[(returns.index >= pd.Timestamp("2026-01-01")) & (returns.index < pd.Timestamp("2026-02-01")), "reversion"] = 0.02
    returns.loc[(returns.index >= pd.Timestamp("2026-01-01")) & (returns.index < pd.Timestamp("2026-02-01")), "trend"] = -0.005
    families = {"trend": "sma_trend", "reversion": "rsi_reversion", "passive_spy": "passive_benchmark"}
    spec = RecentSelectorSpec("test_top1", 63, 1, "total_return")
    weights, decisions = build_monthly_selector_weights(
        returns,
        families,
        spec,
        evaluation_start="2026-01-02",
        evaluation_end="2026-03-31",
    )
    january = decisions[0]
    assert january["month"] == "2026-01"
    assert january["training_end"] == "2025-12-31"
    assert january["selected_components"] == ["trend"]
    january_weights = weights.loc["2026-01"]
    assert january_weights.nunique().max() == 1
    assert (january_weights["trend"] == 1.0).all()
    assert (weights.abs().sum(axis=1) <= 1.0 + 1e-12).all()


def test_january_2025_selection_uses_only_2024_training_data():
    index = pd.bdate_range("2024-07-01", "2025-02-28")
    index = index[index != pd.Timestamp("2025-01-01")]
    returns = pd.DataFrame(0.0, index=index, columns=["trend", "reversion", "passive_spy"])
    returns.loc[returns.index < pd.Timestamp("2025-01-01"), "trend"] = 0.001
    returns.loc[returns.index < pd.Timestamp("2025-01-01"), "reversion"] = -0.001
    families = {"trend": "sma_trend", "reversion": "rsi_reversion", "passive_spy": "passive_benchmark"}
    spec = RecentSelectorSpec("test_2025_top1", 63, 1, "total_return")
    _weights, decisions = build_monthly_selector_weights(
        returns,
        families,
        spec,
        evaluation_start="2025-01-02",
        evaluation_end="2025-02-28",
    )
    assert decisions[0]["month"] == "2025-01"
    assert decisions[0]["training_end"] == "2024-12-31"
    assert decisions[0]["selected_components"] == ["trend"]


def test_market_gate_uses_prior_session_features_and_is_monthly_constant():
    index = pd.bdate_range("2026-01-02", "2026-02-27")
    base = pd.DataFrame(0.0, index=index, columns=["active", "passive_spy"])
    base["active"] = 1.0
    features = pd.DataFrame(
        {
            "dispersion_21": [0.10, 0.02],
            "dispersion_median_252": [0.05, 0.05],
        },
        index=[pd.Timestamp("2025-12-31"), pd.Timestamp("2026-01-30")],
    )
    spec = RecentGateSpec("test_dispersion", "dispersion_21_ge_trailing_median")
    weights, decisions = build_monthly_gate_weights(base, features, spec)
    assert decisions[0]["feature_date"] == "2025-12-31"
    assert decisions[0]["active_selector"] is True
    assert decisions[1]["feature_date"] == "2026-01-30"
    assert decisions[1]["fallback_to_spy"] is True
    assert (weights.loc["2026-01", "active"] == 1.0).all()
    assert (weights.loc["2026-02", "passive_spy"] == 1.0).all()


def test_prospective_snapshot_preserves_first_session_observation(tmp_path: Path):
    report = {
        "dataset": {"dataset_id": "ds_test", "dataset_name": "test", "lineage_hash": "lineage", "latest_session": "2026-08-04", "evaluation_end": "2026-08-04"},
        "candidate_pool": {"count": 1, "hash": "pool", "selection_rule": "frozen"},
        "component_records": [{"candidate": {"candidate_id": "candidate_a", "family": "rsi_reversion", "parameters": {}}, "active_symbols_at_end": [{"symbol": "AAPL"}]}],
        "selector_records": [{"selector_id": "selector_a", "spec": {"name": "selector"}, "verdict": "FAIL", "current_selection": {"selected_components": ["candidate_a"]}}],
        "gate_records": [],
        "summary": {"leader_selector_id": "selector_a", "leader_selector_name": "selector", "leader_verdict": "FAIL", "current_selection": {"selected_components": ["candidate_a"]}, "current_selected_components": []},
    }
    first = write_immutable_prospective_snapshot(report, root=tmp_path, created_at=datetime(2026, 8, 5, tzinfo=timezone.utc))
    report["selector_records"][0]["current_selection"] = {"selected_components": []}
    second = write_immutable_prospective_snapshot(report, root=tmp_path, created_at=datetime(2026, 8, 5, 1, tzinfo=timezone.utc))
    canonical = json.loads(Path(first["snapshot_path"]).read_text(encoding="utf-8"))
    assert first["status"] == "created_immutable_snapshot"
    assert second["status"] == "immutable_conflict_preserved"
    assert canonical["selectors"][0]["current_selection"]["selected_components"] == ["candidate_a"]
    assert second["conflict_path"] is not None
    assert Path(second["conflict_path"]).is_file()


def test_prospective_evaluation_waits_for_future_opens_and_scores_frozen_basket(tmp_path: Path):
    snapshot = {
        "snapshot_id": "snapshot_a",
        "market_session": "2026-08-04",
        "leader": {
            "selector_id": "selector_a",
            "selector_name": "selector",
            "current_selection": {"weights": {"candidate_a": 1.0}, "selected_components": ["candidate_a"]},
            "current_selected_components": [
                {"candidate_id": "candidate_a", "active_symbols": [{"symbol": "AAPL"}]}
            ],
        },
        "selectors": [],
        "gates": [],
    }
    snapshot_path = tmp_path / "snapshots" / "2026-08-04.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    opens = pd.DataFrame(
        {"AAPL": [100.0, 110.0], "SPY": [500.0, 510.0]},
        index=pd.to_datetime(["2026-08-05", "2026-08-06"]),
    )
    summary = evaluate_prospective_snapshots(
        opens=opens,
        snapshot_paths=[snapshot_path],
        root=tmp_path,
        dataset={"dataset_id": "ds", "checksum": "hash"},
        horizons=(1, 5),
        evaluated_at=datetime(2026, 8, 6, 14, tzinfo=timezone.utc),
    )
    assert summary["matured_observations"] == 1
    assert summary["pending_observations"] == 1
    assert summary["leader_selector_one_session"]["scored_sessions"] == 1
    assert round(summary["leader_selector_one_session"]["strategy_return_pct"], 8) == 10.0
    assert round(summary["leader_selector_one_session"]["spy_return_pct"], 8) == 2.0
    evaluation_files = list((tmp_path / "evaluations" / "2026-08-04").glob("*.json"))
    assert len(evaluation_files) == 1
    persisted = json.loads(evaluation_files[0].read_text(encoding="utf-8"))
    assert persisted["result"]["entry_session"] == "2026-08-05"
    assert persisted["result"]["exit_session"] == "2026-08-06"
    assert persisted["execution_authority"] is False


def test_signal_episode_loader_deduplicates_ids_and_filters_regular_hours(tmp_path: Path):
    fields = [
        "received_at", "scan_type", "signal_id", "signal_signature", "first_seen_at", "last_seen_at",
        "seen_count", "ticker", "direction", "setup_family", "score", "strength", "spot", "target",
        "invalidation", "geometry_valid", "actionable",
    ]
    path = tmp_path / "flash-signals-v2-2026-08-05.csv"
    rows = [
        {
            "received_at": "2026-08-05T14:00:01Z", "scan_type": "flash", "signal_id": "episode_a",
            "signal_signature": "signature", "first_seen_at": "2026-08-05T14:00:00Z",
            "last_seen_at": "2026-08-05T14:01:00Z", "seen_count": "2", "ticker": "META",
            "direction": "BEARISH", "setup_family": "ceiling_rejection", "score": "80", "strength": "",
            "spot": "100", "target": "99", "invalidation": "102", "geometry_valid": "true", "actionable": "true",
        },
        {
            "received_at": "2026-08-05T14:02:01Z", "scan_type": "flash", "signal_id": "episode_a",
            "signal_signature": "signature", "first_seen_at": "2026-08-05T14:00:00Z",
            "last_seen_at": "2026-08-05T14:02:00Z", "seen_count": "3", "ticker": "META",
            "direction": "BULLISH", "setup_family": "mutated_retry", "score": "5", "strength": "",
            "spot": "100", "target": "101", "invalidation": "98", "geometry_valid": "true", "actionable": "true",
        },
        {
            "received_at": "2026-08-05T22:00:01Z", "scan_type": "flash", "signal_id": "episode_after_hours",
            "signal_signature": "after", "first_seen_at": "2026-08-05T22:00:00Z",
            "last_seen_at": "2026-08-05T22:00:00Z", "seen_count": "1", "ticker": "META",
            "direction": "BULLISH", "setup_family": "momentum_push", "score": "90", "strength": "",
            "spot": "100", "target": "101", "invalidation": "98", "geometry_valid": "true", "actionable": "true",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    episodes = load_signal_episodes(tmp_path)
    assert len(episodes) == 2
    canonical = next(row for row in episodes if row["signal_id"] == "episode_a")
    assert canonical["direction"] == "BEARISH"
    assert canonical["setup_family"] == "ceiling_rejection"
    assert canonical["seen_count"] == 3
    features = session_signal_features(episodes, market_session="2026-08-05", symbols=["META"])
    assert features["META"]["sources"]["flash"]["episode_count"] == 1
    assert features["META"]["sources"]["flash"]["latest_direction"] == "BEARISH"


def test_signal_overlay_policy_family_handles_conflict_and_missing_coverage():
    policies = {policy.name: policy for policy in default_signal_overlay_policies()}
    assert len(policies) == 6
    features = {
        "CAT": {
            "sources": {
                "flash": {"latest_direction": None},
                "flash_agentic": {"latest_direction": None},
                "cluster": {"latest_direction": None},
            }
        },
        "META": {
            "sources": {
                "flash": {"latest_direction": "BULLISH"},
                "flash_agentic": {"latest_direction": "BEARISH"},
                "cluster": {"latest_direction": None},
            }
        },
    }
    symbols = ["CAT", "META"]
    assert apply_signal_overlay_policy(symbols, features, policies["baseline_reversal"])["retained_symbols"] == symbols
    assert apply_signal_overlay_policy(symbols, features, policies["agentic_conflict_avoidance"])["retained_symbols"] == ["CAT"]
    assert apply_signal_overlay_policy(symbols, features, policies["flash_conflict_avoidance"])["retained_symbols"] == symbols
    consensus = apply_signal_overlay_policy(symbols, features, policies["all_source_consensus"])
    assert consensus["retained_symbols"] == []
    assert consensus["symbol_weights"] == {"SPY": 1.0}
    pressure = apply_signal_overlay_policy(symbols, features, policies["bearish_pressure_confirmation"])
    assert pressure["retained_symbols"] == ["META"]


def test_signal_overlay_snapshot_is_immutable(tmp_path: Path):
    snapshot = {"market_session": "2026-08-05", "snapshot_id": "overlay_a", "observations": []}
    first = write_immutable_signal_overlay_snapshot(snapshot, root=tmp_path, updated_at=datetime(2026, 8, 5, tzinfo=timezone.utc))
    snapshot["snapshot_id"] = "overlay_b"
    second = write_immutable_signal_overlay_snapshot(snapshot, root=tmp_path, updated_at=datetime(2026, 8, 5, 1, tzinfo=timezone.utc))
    assert first["status"] == "created_immutable_snapshot"
    assert second["status"] == "immutable_conflict_preserved"
    canonical = json.loads(Path(first["snapshot_path"]).read_text(encoding="utf-8"))
    assert canonical["snapshot_id"] == "overlay_a"
    assert Path(second["conflict_path"]).is_file()


def test_generic_overlay_observation_uses_same_future_open_evaluator(tmp_path: Path):
    snapshot = {
        "snapshot_id": "overlay_snapshot",
        "market_session": "2026-08-04",
        "observations": [
            {
                "observation_type": "cipher_signal_overlay",
                "observation_name": "baseline_reversal",
                "observation_id": "policy_a",
                "symbol_weights": {"CAT": 0.5, "META": 0.5},
            }
        ],
    }
    path = tmp_path / "snapshots" / "2026-08-04.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    opens = pd.DataFrame(
        {"CAT": [100.0, 110.0], "META": [200.0, 180.0], "SPY": [500.0, 505.0]},
        index=pd.to_datetime(["2026-08-05", "2026-08-06"]),
    )
    summary = evaluate_prospective_snapshots(
        opens=opens,
        snapshot_paths=[path],
        root=tmp_path,
        dataset={"dataset_id": "ds", "checksum": "hash"},
        horizons=(1, 5),
        evaluated_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )
    assert summary["matured_observations"] == 1
    assert summary["pending_observations"] == 1
    assert summary["leader_selector_one_session"]["scored_sessions"] == 0
    record = summary["one_session_by_observation"][0]
    assert record["observation_type"] == "cipher_signal_overlay"
    assert record["observation_id"] == "policy_a"
    assert round(record["metrics"]["strategy_return_pct"], 8) == 0.0
    assert round(record["metrics"]["spy_return_pct"], 8) == 1.0


def test_current_cipher_signal_overlay_report_is_prospective_only():
    payload = load_artifact("data/governance/cipher_signal_overlay_research.json")
    assert payload["status"] == "completed"
    assert payload["policy_family"]["count"] == 6
    assert payload["capture_inventory"]["sessions"] >= 1
    assert payload["capture_inventory"]["historical_backtest_eligible"] is False
    assert payload["assessment"]["can_help"] is True
    assert "2025_backfill" in payload["assessment"]["not_supported"]
    assert payload["prospective_evaluation"]["snapshots"] >= 1
    assert payload["prospective_evaluation"]["matured_observations"] >= 0
    assert payload["prospective_evaluation"]["pending_observations"] >= 1
    assert set(payload["current_policy_baskets"]) == {
        policy.name for policy in default_signal_overlay_policies()
    }
    for basket in payload["current_policy_baskets"].values():
        assert round(sum(float(value) for value in basket["symbol_weights"].values()), 12) == 1.0
    assert payload["automatic_promotion"] is False
    assert payload["paper_or_live_execution"] is False
    assert payload["execution_authority"] is False


def test_recent_component_robustness_artifact_has_exact_concentration_checks():
    payload = load_artifact("data/governance/recent_component_robustness.json")
    assert payload["component"]["candidate_id"] == "candidate_450ab714a604e63bc221ccfb"
    assert payload["summary"]["leave_one_symbol_out_tests"] == 38
    assert payload["summary"]["positive_2025_fraction"] == 1.0
    assert payload["summary"]["positive_2026_fraction"] == 1.0
    assert payload["summary"]["leave_one_symbol_out_passed"] is True
    assert payload["summary"]["top_symbol_positive_share"] < 0.35
    assert payload["contribution_concentration"]["concentration_flag"] is False
    assert payload["automatic_promotion"] is False
    assert payload["execution_authority"] is False


def test_family_balanced_selector_uses_trend_and_reversion_when_both_are_positive():
    index = pd.bdate_range("2025-08-01", "2026-02-27")
    returns = pd.DataFrame(0.0, index=index, columns=["trend", "reversion", "passive_spy"])
    returns.loc[returns.index < pd.Timestamp("2026-01-01"), "trend"] = 0.0010
    returns.loc[returns.index < pd.Timestamp("2026-01-01"), "reversion"] = 0.0008
    families = {"trend": "sma_trend", "reversion": "bollinger_reversion", "passive_spy": "passive_benchmark"}
    spec = RecentSelectorSpec("test_balanced", 63, 2, "sharpe", mode="family_balanced")
    weights, decisions = build_monthly_selector_weights(
        returns,
        families,
        spec,
        evaluation_start="2026-01-02",
        evaluation_end="2026-02-27",
    )
    assert set(decisions[0]["selected_components"]) == {"trend", "reversion"}
    january = weights.loc["2026-01"]
    assert (january["trend"] == 0.5).all()
    assert (january["reversion"] == 0.5).all()


def test_recent_data_refresh_runs_only_after_close_once_per_weekday():
    after_close = datetime(2026, 8, 5, 18, 0, tzinfo=NY).astimezone(timezone.utc)
    before_close = datetime(2026, 8, 5, 16, 30, tzinfo=NY).astimezone(timezone.utc)
    weekend = datetime(2026, 8, 8, 18, 0, tzinfo=NY).astimezone(timezone.utc)
    assert data_refresh_due(latest_session="2026-08-04", now=after_close, last_attempt_date=None) is True
    assert data_refresh_due(latest_session="2026-08-04", now=before_close, last_attempt_date=None) is False
    assert data_refresh_due(latest_session="2026-08-04", now=weekend, last_attempt_date=None) is False
    assert data_refresh_due(latest_session="2026-08-04", now=after_close, last_attempt_date="2026-08-05") is False


def test_recent_runner_pool_and_report_preserve_exploratory_boundary():
    # candidate_pool() reads the cross-period matrix internally, so the guard has
    # to precede it — otherwise the missing artifact surfaces as a raw
    # FileNotFoundError from inside the script rather than a skip.
    require_artifact("data/governance/cross_period_strategy_matrix.json", non_empty_key="matrix")
    module = load_script("run_recent_regime_research")
    rows, pool_hash = module.candidate_pool()
    assert len(rows) == 14
    assert pool_hash.startswith("recent_regime_candidate_pool_")
    artifact = require_artifact("data/governance/recent_regime_research.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["candidate_pool"]["count"] == 14
    assert payload["candidate_pool"]["selection_rule"] == "frozen_2026_08_04_recent_evidence_pool"
    assert payload["candidate_pool"]["outcome_informed"] is True
    assert payload["candidate_pool"]["independent_holdout"] is False
    assert payload["summary"]["selectors"] == 8
    assert payload["summary"]["gate_variants"] == 16
    assert payload["summary"]["gate_passes"] == 0
    assert payload["dataset"]["component_return_start"] == "2024-01-02"
    assert payload["dataset"]["rolling_2025_start"] == "2025-01-02"
    assert payload["dataset"]["rolling_2026_start"] == "2026-01-02"
    assert payload["research_role"] == "recent_2024_warmup_2025_2026_rolling_development_only_not_independent_holdout"
    assert payload["prospective_snapshot"]["market_session"] == payload["dataset"]["latest_session"]
    assert payload["automatic_promotion"] is False
    assert payload["paper_or_live_execution"] is False
    assert payload["execution_authority"] is False
    for selector in payload["selector_records"]:
        for decision in selector["decisions"]:
            assert decision["training_end"] < decision["first_session"]
    for gate in payload["gate_records"]:
        for decision in gate["gate_decisions"]:
            assert decision["feature_date"] < decision["first_session"]


def test_recent_source_has_no_order_authority():
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "core/research_platform/recent_regime.py",
            "core/research_platform/cipher_signal_overlay.py",
            "core/research_platform/recent_regime_prospective.py",
            "core/research_platform/recent_regime_prospective_evaluation.py",
            "core/research_platform/recent_regime_refresh.py",
            "scripts/run_recent_regime_research.py",
            "scripts/evaluate_recent_regime_prospective.py",
            "scripts/run_recent_component_robustness.py",
            "scripts/run_cipher_signal_overlay_research.py",
            "scripts/refresh_recent_equity_panel.py",
        )
    )
    for forbidden in ("/v2/orders", "submit_order", "place_order", "create_order", "TradingClient", "OrderClient"):
        assert forbidden not in sources
