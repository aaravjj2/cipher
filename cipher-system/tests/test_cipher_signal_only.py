from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

from conftest import load_artifact

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def episode(*, signal_id: str, source: str, timestamp: str, direction: str = "BULLISH") -> dict:
    return {
        "signal_id": signal_id,
        "signal_signature": signal_id,
        "scan_type": source,
        "ticker": "AAPL",
        "direction": direction,
        "setup_family": "momentum_push",
        "score": 75.0 if source != "cluster" else None,
        "strength": 100.0 if source == "cluster" else None,
        "spot": 100.0,
        "target": 102.0,
        "invalidation": 98.0,
        "geometry_valid": True,
        "actionable": source != "cluster",
        "first_seen_at": timestamp,
        "last_seen_at": timestamp,
        "seen_count": 1,
        "market_session": timestamp[:10],
        "regular_hours": True,
        "source_file": "test.csv",
    }


def test_daily_latest_state_uses_one_state_per_source_ticker_session():
    module = load_script("run_cipher_signal_only_research")
    episodes = [
        episode(signal_id="a", source="flash", timestamp="2026-08-05T14:00:00+00:00", direction="BEARISH"),
        episode(signal_id="b", source="flash", timestamp="2026-08-05T19:00:00+00:00", direction="BULLISH"),
        episode(signal_id="c", source="flash_agentic", timestamp="2026-08-05T15:00:00+00:00", direction="BEARISH"),
    ]
    states = module.daily_latest_states(episodes)
    assert len(states) == 2
    flash = next(row for row in states if row["scan_type"] == "flash")
    assert flash["signal_id"] == "b"
    assert flash["direction"] == "BULLISH"


def test_future_open_scoring_respects_direction_and_waits_for_opens():
    module = load_script("run_cipher_signal_only_research")
    states = [episode(signal_id="a", source="flash_agentic", timestamp="2026-08-04T19:00:00+00:00", direction="BEARISH")]
    opens = pd.DataFrame(
        {"AAPL": [100.0, 90.0], "SPY": [500.0, 505.0]},
        index=pd.to_datetime(["2026-08-05", "2026-08-06"]),
    )
    scored = module.score_states(states, opens)
    one = next(row for row in scored if row["horizon_sessions"] == 1)
    five = next(row for row in scored if row["horizon_sessions"] == 5)
    assert one["status"] == "matured"
    assert round(one["directional_return_pct"], 8) == 10.0
    assert one["direction_correct"] is True
    assert five["status"] == "pending_future_opens"


def test_cross_source_context_marks_conflict_and_first_source():
    module = load_script("run_cipher_signal_only_research")
    states = [
        episode(signal_id="a", source="flash", timestamp="2026-08-05T14:00:00+00:00", direction="BULLISH"),
        episode(signal_id="b", source="cluster", timestamp="2026-08-05T15:00:00+00:00", direction="BEARISH"),
    ]
    context = module.agreement_context(states)[("2026-08-05", "AAPL")]
    assert context["agreement_status"] == "mixed_conflict"
    assert context["first_source"] == "flash"
    assert context["cross_source_lag_minutes"] == 60.0


def test_candidate_rules_deduplicate_ticker_day_across_sources():
    module = load_script("run_cipher_signal_specifics")
    frame = pd.DataFrame(
        [
            {
                "status": "matured",
                "market_session": "2026-08-04",
                "ticker": "AAPL",
                "horizon_sessions": 1,
                "source": "flash",
                "direction": "BULLISH",
                "first_seen_at": "2026-08-04T14:00:00+00:00",
                "raw_underlying_return_pct": 2.0,
                "spy_return_pct": 0.5,
            },
            {
                "status": "matured",
                "market_session": "2026-08-04",
                "ticker": "AAPL",
                "horizon_sessions": 1,
                "source": "flash_agentic",
                "direction": "BULLISH",
                "first_seen_at": "2026-08-04T15:00:00+00:00",
                "raw_underlying_return_pct": 2.0,
                "spy_return_pct": 0.5,
            },
        ]
    )
    rules = module.candidate_rule_rows(frame)
    unanimous = rules[rules["rule_name"] == "follow_unanimous_multi_source"]
    assert len(unanimous) == 1
    assert unanimous.iloc[0]["directional_return_pct"] == 2.0
    assert unanimous.iloc[0]["covered_sources"] == 2


def test_cluster_expiration_uses_second_listed_expiry_and_directional_contracts():
    module = load_script("run_cipher_complete_observations")
    contracts = [
        {"symbol": "AAPL1C100", "expiration_date": "2026-08-07", "strike_price": 100.0, "type": "call"},
        {"symbol": "AAPL2C100", "expiration_date": "2026-08-14", "strike_price": 100.0, "type": "call"},
        {"symbol": "AAPL2C105", "expiration_date": "2026-08-14", "strike_price": 105.0, "type": "call"},
        {"symbol": "AAPL2P100", "expiration_date": "2026-08-14", "strike_price": 100.0, "type": "put"},
    ]
    expiry, method = module.expiry_for_state({"market_session": "2026-08-05"}, contracts)
    assert expiry == "2026-08-14"
    assert method == "provider_second_listed_expiration"
    contract = module.nearest_contract(
        contracts,
        expiry=expiry,
        option_type="call",
        strike_target=104.0,
    )
    assert contract["symbol"] == "AAPL2C105"


def test_cluster_debit_spread_metrics_use_atm_long_and_target_short():
    module = load_script("run_cipher_complete_observations")
    result = module.spread_metrics(
        {
            "status": "matured_at_expiry",
            "symbol": "LONG",
            "entry_price": 4.0,
            "mark_price": 7.0,
        },
        {
            "status": "matured_at_expiry",
            "symbol": "SHORT",
            "entry_price": 1.0,
            "mark_price": 2.0,
        },
    )
    assert result["entry_debit"] == 3.0
    assert result["mark_value"] == 5.0
    assert round(result["end_return_pct"], 8) == round((5.0 / 3.0 - 1.0) * 100.0, 8)
    assert result["profitable_at_mark"] is True


def test_provider_open_matrix_covers_all_source_tickers():
    module = load_script("run_cipher_complete_observations")
    bars = {
        "AAPL": [{"t": "2026-08-05T04:00:00Z", "o": 100.0}],
        "SPY": [{"t": "2026-08-05T04:00:00Z", "o": 500.0}],
    }
    matrix = module.provider_open_matrix(bars)
    assert list(matrix.columns) == ["AAPL", "SPY"]
    assert matrix.at[pd.Timestamp("2026-08-05"), "AAPL"] == 100.0


def test_cluster_target_distance_and_time_buckets_are_directional():
    module = load_script("run_cipher_complete_observations")
    bullish = module.directional_target_distance_pct(
        {"direction": "BULLISH", "spot": 100.0, "target": 105.0}
    )
    bearish = module.directional_target_distance_pct(
        {"direction": "BEARISH", "spot": 100.0, "target": 95.0}
    )
    assert round(bullish, 8) == 5.0
    assert round(bearish, 8) == 5.0
    assert module.target_distance_bucket(1.99) == "under_2_pct"
    assert module.target_distance_bucket(2.0) == "2_to_5_pct"
    assert module.target_distance_bucket(5.0) == "5_to_10_pct"
    assert module.target_distance_bucket(10.01) == "over_10_pct"
    assert module.signal_time_bucket("2026-08-05T13:45:00Z") == "0930_1029_et"
    assert module.signal_time_bucket("2026-08-05T19:45:00Z") == "1530_1600_et"


def test_option_path_diagnostics_preserve_peak_giveback_counts():
    module = load_script("run_cipher_complete_observations")
    rows = [
        {"atm_option_maximum_return_pct": 50.0, "atm_option_end_return_pct": -5.0},
        {"atm_option_maximum_return_pct": 120.0, "atm_option_end_return_pct": 40.0},
        {"atm_option_maximum_return_pct": 10.0, "atm_option_end_return_pct": 2.0},
    ]
    result = module.option_path_diagnostics(rows, "atm_option")
    assert result["available"] == 3
    assert result["positive_peak_fraction"] == 1.0
    assert result["thresholds"]["25"]["reached_count"] == 2
    assert result["thresholds"]["25"]["gave_back_to_nonpositive_count"] == 1
    assert result["thresholds"]["100"]["reached_count"] == 1


def test_same_session_option_mark_uses_actual_intraday_session():
    module = load_script("run_cipher_complete_observations")
    signal_at = pd.Timestamp("2026-08-06T13:54:00Z")
    bars = {
        ("2026-08-06", "AAPL260814C00100000"): [
            {"t": "2026-08-06T14:00:00Z", "o": 2.0, "h": 2.2, "l": 1.9, "c": 2.1},
            {"t": "2026-08-06T14:20:00Z", "o": 2.1, "h": 2.5, "l": 2.0, "c": 2.4},
        ]
    }
    result = module.option_leg_metrics(
        symbol="AAPL260814C00100000",
        signal_at=signal_at,
        expiry="2026-08-14",
        minute_bars=bars,
        daily_bars={},
        latest_market_session="2026-08-05",
    )
    assert result["mark_session"] == "2026-08-06"
    assert result["mark_basis"] == "same_session_intraday"
    assert result["mark_at"] == "2026-08-06T14:20:00+00:00"


def test_live_complete_observations_are_all_date_and_expiry_aware():
    payload = load_artifact("data/governance/cipher_signal_only/latest_complete_observations.json")
    populations = payload["population_counts"]
    cluster = payload["cluster_expiry_research"]
    summary = cluster["summary"]
    assert payload["mode"] == "complete_flash_agentic_cluster_observations"
    assert populations["all_unique_episodes"] >= 5000
    assert populations["all_daily_terminal_source_ticker_states"] >= 600
    assert populations["cluster_expiry_records"] == summary["states"]
    assert summary["expiration_reconstructed"] == summary["states"]
    assert summary["matured_at_expiry"] + summary["pending_expiry"] == summary["states"]
    assert summary["finalized_at_expiry"]["observations"] == summary["matured_at_expiry"]
    assert summary["pending_mark_to_latest"]["observations"] == summary["pending_expiry"]
    assert summary["completed_sessions_by_target_distance_bucket"]
    assert summary["completed_sessions_by_signal_time_bucket"]
    assert summary["option_path_diagnostics_completed_sessions"]["atm_option"]["available"] >= 1
    assert summary["candidate_hypotheses_completed_sessions"]
    assert summary["current_partial_sessions"]["observations"] >= 0
    assert cluster["primary_horizon"] == "scanner_second_listed_option_expiration"
    assert payload["automatic_promotion"] is False
    assert payload["paper_or_live_execution"] is False
    assert payload["execution_authority"] is False


def test_live_signal_specifics_has_ticker_and_rule_boundaries():
    payload = load_artifact("data/governance/cipher_signal_only/latest_ticker_strategy_specifics.json")
    assert payload["mode"] == "flash_agentic_cluster_only"
    assert payload["ticker_analysis"]["by_source_ticker_horizon"]
    assert payload["candidate_rule_analysis"]["rules"]
    assert payload["candidate_rule_analysis"]["flash_bullish_leave_one_ticker_out"]
    assert payload["latest_session_snapshot"]["market_session"] >= "2026-08-05"
    assert payload["limits"]["ticker_day_dependence_deduplicated_in_candidate_rules"] is True
    assert payload["automatic_promotion"] is False
    assert payload["execution_authority"] is False


def test_live_signal_only_report_has_exact_three_source_boundary():
    payload = load_artifact("data/governance/cipher_signal_only/latest_signal_research.json")
    assert payload["mode"] == "flash_agentic_cluster_only"
    assert payload["active_sources"] == ["flash", "flash_agentic", "cluster"]
    assert payload["capture_inventory"]["sessions"] >= 1
    assert payload["daily_latest_states"]["count"] >= 1
    assert payload["forward_scoring"]["matured_observations"] >= 1
    assert payload["automatic_promotion"] is False
    assert payload["paper_or_live_execution"] is False
    assert payload["execution_authority"] is False


def test_signal_only_sources_have_no_order_authority():
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "scripts/run_cipher_signal_only_research.py",
            "scripts/run_cipher_signal_only_loop.py",
            "scripts/manage_cipher_signal_only_loop.py",
            "scripts/run_cipher_signal_specifics.py",
            "scripts/run_cipher_complete_observations.py",
        )
    )
    for forbidden in ("/v2/orders", "submit_order", "place_order", "TradingClient", "OrderClient"):
        assert forbidden not in sources
