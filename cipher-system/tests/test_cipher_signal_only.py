from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
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


def test_terminal_confirmation_uses_last_selected_source_timestamp():
    module = load_script("run_cipher_complete_observations")
    states = [
        episode(signal_id="cluster", source="cluster", timestamp="2026-08-05T14:00:00+00:00"),
        episode(signal_id="flash", source="flash", timestamp="2026-08-05T15:00:00+00:00"),
        episode(signal_id="agentic", source="flash_agentic", timestamp="2026-08-05T16:00:00+00:00"),
    ]
    context = module.terminal_confirmation_context(states)[("2026-08-05", "AAPL")]
    assert context["confirmed_at"] == "2026-08-05T16:00:00+00:00"
    assert context["trigger_source"] == "flash_agentic"
    assert context["covered_sources"] == 3


def test_first_realtime_confirmation_uses_only_observed_prior_state():
    module = load_script("run_cipher_complete_observations")
    cluster = episode(signal_id="cluster", source="cluster", timestamp="2026-08-05T15:00:00+00:00")
    episodes = [
        episode(signal_id="flash-bear", source="flash", timestamp="2026-08-05T14:30:00+00:00", direction="BEARISH"),
        cluster,
        episode(signal_id="flash-bull", source="flash", timestamp="2026-08-05T15:30:00+00:00", direction="BULLISH"),
    ]
    context = module.first_realtime_confirmation_context(cluster, episodes)
    assert context is not None
    assert context["confirmed_at"] == "2026-08-05T15:30:00+00:00"
    assert context["trigger_source"] == "flash"
    assert context["supporting_sources"] == ["flash"]


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
    payload = json.loads(
        (ROOT / "data" / "governance" / "cipher_signal_only" / "latest_complete_observations.json").read_text(
            encoding="utf-8"
        )
    )
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
    payload = json.loads(
        (ROOT / "data" / "governance" / "cipher_signal_only" / "latest_ticker_strategy_specifics.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["mode"] == "flash_agentic_cluster_only"
    assert payload["ticker_analysis"]["by_source_ticker_horizon"]
    assert payload["candidate_rule_analysis"]["rules"]
    assert payload["candidate_rule_analysis"]["flash_bullish_leave_one_ticker_out"]
    assert payload["latest_session_snapshot"]["market_session"] >= "2026-08-05"
    assert payload["limits"]["ticker_day_dependence_deduplicated_in_candidate_rules"] is True
    assert payload["automatic_promotion"] is False
    assert payload["execution_authority"] is False


def test_live_signal_only_report_has_exact_three_source_boundary():
    payload = json.loads(
        (ROOT / "data" / "governance" / "cipher_signal_only" / "latest_signal_research.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["mode"] == "flash_agentic_cluster_only"
    assert payload["active_sources"] == ["flash", "flash_agentic", "cluster"]
    assert payload["capture_inventory"]["sessions"] >= 1
    assert payload["daily_latest_states"]["count"] >= 1
    assert payload["forward_scoring"]["matured_observations"] >= 1
    assert payload["automatic_promotion"] is False
    assert payload["paper_or_live_execution"] is False
    assert payload["execution_authority"] is False


def test_cluster_individual_assessment_uses_cluster_fields_only():
    module = load_script("run_cipher_cluster_individual_analysis")
    row = {
        "direction": "BULLISH",
        "rank": 4,
        "strength": 245.0,
        "target_distance_pct": 6.0,
        "cluster_expiration": "2026-08-14",
        "atm_contract": {"symbol": "AAPL260814C00100000"},
        "target_contract": {"symbol": "AAPL260814C00105000"},
    }
    result = module.standalone_assessment(row)
    assert result["research_tier"] == "tier_a_cluster_only"
    assert result["preferred_research_structure"] == "atm_to_target_debit_spread_research"
    assert result["uses_other_signal_sources"] is False
    assert result["execution_authority"] is False


def test_cluster_individual_sequence_preserves_every_episode():
    module = load_script("run_cipher_cluster_individual_analysis")
    rows = [
        episode(signal_id="first", source="cluster", timestamp="2026-08-05T14:00:00+00:00"),
        episode(signal_id="second", source="cluster", timestamp="2026-08-05T15:00:00+00:00"),
        episode(signal_id="third", source="cluster", timestamp="2026-08-05T16:00:00+00:00", direction="BEARISH"),
    ]
    rows[0].update({"rank": 10, "strength": 210.0, "target": 102.0})
    rows[1].update({"rank": 5, "strength": 250.0, "target": 105.0})
    rows[2].update({"rank": 3, "strength": 275.0, "target": 95.0})
    context = module.sequence_context(rows)
    assert len(context) == 3
    assert context["first"]["appearance_bucket"] == "first"
    assert context["second"]["episode_number_for_ticker_session"] == 2
    assert context["second"]["rank_change_from_previous"] == -5
    assert context["third"]["direction_changed_from_previous"] is True


def test_independent_source_latest_states_never_merge_sources():
    module = load_script("run_cipher_independent_signal_analysis")
    rows = [
        episode(signal_id="old", source="flash", timestamp="2026-08-05T14:00:00+00:00", direction="BEARISH"),
        episode(signal_id="new", source="flash", timestamp="2026-08-05T19:00:00+00:00", direction="BULLISH"),
    ]
    states = module.latest_states(rows)
    assert len(states) == 1
    assert states[0]["signal_id"] == "new"


def test_active_loop_runs_independent_products_only():
    source = (ROOT / "scripts" / "run_cipher_signal_only_loop.py").read_text(encoding="utf-8")
    assert "run_cipher_independent_signal_analysis.py" in source
    assert "run_cipher_cluster_individual_analysis.py" in source
    assert "run_cipher_cluster_strategy.py" in source
    assert '"--freeze-only"' in source
    assert "run_cipher_signal_specifics.py" not in source
    assert "run_cipher_signal_only_research.py" not in source
    assert '"combined_votes": False' in source
    assert '"confirmations": False' in source


def test_live_independent_signal_report_has_separate_boundaries():
    path = ROOT / "data" / "governance" / "cipher_signal_only" / "latest_independent_signal_analysis.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["mode"] == "independent_flash_and_agentic_analysis"
    assert payload["source_boundary"]["combined_votes"] is False
    for source in ("flash", "flash_agentic"):
        assert payload["sources"][source]["source_boundary"]["uses_other_signal_sources"] is False
        assert payload["sources"][source]["terminal_states"]["count"] >= 1


def test_live_cluster_individual_report_has_no_other_source_logic():
    path = ROOT / "data" / "governance" / "cipher_signal_only" / "latest_cluster_individual_analysis.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["mode"] == "cluster_individual_episode_analysis"
    assert payload["source_boundary"]["uses_other_signal_sources"] is False
    assert payload["research_limits"]["daily_terminal_state_collapse_used"] is False
    assert payload["summary"]["total_cluster_episodes"] == (
        payload["summary"]["eligible_regular_session_episodes"] + payload["summary"]["excluded_episodes"]
    )
    assert len(payload["records"]) == payload["summary"]["eligible_regular_session_episodes"]


def test_signal_only_sources_have_no_order_authority():
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "scripts/run_cipher_signal_only_research.py",
            "scripts/run_cipher_signal_only_loop.py",
            "scripts/manage_cipher_signal_only_loop.py",
            "scripts/run_cipher_signal_specifics.py",
            "scripts/run_cipher_complete_observations.py",
            "scripts/run_cipher_cluster_individual_analysis.py",
            "scripts/run_cipher_independent_signal_analysis.py",
            "scripts/run_cipher_cluster_trade_builder.py",
            "scripts/run_cipher_trade_postmortems.py",
            "scripts/run_cipher_cluster_strategy.py",
        )
    )
    for forbidden in ("/v2/orders", "submit_order", "place_order", "TradingClient", "OrderClient"):
        assert forbidden not in sources


def test_trade_builder_selects_first_tier_a_and_tracks_latest_state():
    module = load_script("run_cipher_cluster_trade_builder")
    rows = [
        {
            "market_session": "2026-08-06",
            "ticker": "AAPL",
            "first_seen_at": "2026-08-06T14:00:00+00:00",
            "signal_id": "first",
            "direction": "BULLISH",
            "standalone_assessment": {"research_tier": "tier_a_cluster_only"},
        },
        {
            "market_session": "2026-08-06",
            "ticker": "AAPL",
            "first_seen_at": "2026-08-06T15:00:00+00:00",
            "signal_id": "second",
            "direction": "BULLISH",
            "standalone_assessment": {"research_tier": "tier_b_cluster_only"},
        },
    ]
    selected = module.first_tier_a_per_ticker_session(rows)
    latest = module.latest_record_per_ticker_session(rows)
    assert [row["signal_id"] for row in selected] == ["first"]
    assert latest[("2026-08-06", "AAPL")]["signal_id"] == "second"


def test_trade_builder_remaining_target_buckets():
    module = load_script("run_cipher_cluster_trade_builder")
    assert module.remaining_target_bucket(0.5) == "under_1_pct"
    assert module.remaining_target_bucket(1.5) == "1_to_2_pct"
    assert module.remaining_target_bucket(3.0) == "2_to_5_pct"
    assert module.remaining_target_bucket(7.0) == "5_to_10_pct"
    assert module.remaining_target_bucket(12.0) == "over_10_pct"
    assert module.remaining_target_bucket(-1.0, reached_before_entry=True) == "target_reached_before_entry"


def test_trade_builder_quote_geometry_uses_defined_risk_spread():
    module = load_script("run_cipher_cluster_trade_builder")
    result = module.quote_trade_geometry(
        long_contract={"bid": 6.9, "ask": 7.25, "mid": 7.075, "volume": 76, "open_interest": 173},
        short_contract={"bid": 3.95, "ask": 4.2, "mid": 4.075, "volume": 150, "open_interest": 314},
        long_strike=207.5,
        short_strike=215.0,
        underlying_spot=211.48,
    )
    assert result["valid_debit_geometry"] is True
    assert result["spread_width"] == 7.5
    assert result["midpoint_debit"] == 3.0
    assert result["reference_max_loss_per_spread"] == 300.0
    assert result["reference_max_profit_per_spread"] == 450.0
    assert result["reference_breakeven"] == 210.5


def test_trade_builder_delayed_entry_uses_next_session_option_opens():
    module = load_script("run_cipher_cluster_trade_builder")
    row = {
        "market_session": "2026-08-05",
        "ticker": "AAPL",
        "signal_id": "tier-a",
        "first_seen_at": "2026-08-05T15:00:00+00:00",
        "rank": 3,
        "strength": 240,
        "spot": 100.0,
        "target": 105.0,
        "target_distance_pct": 5.0,
        "cluster_expiration": "2026-08-14",
        "atm_contract": {"symbol": "LONG", "strike_price": 100.0},
        "target_contract": {"symbol": "SHORT", "strike_price": 105.0},
        "standalone_assessment": {"research_tier": "tier_a_cluster_only"},
    }
    result = module.delayed_entry_record(
        row,
        option_daily={
            "LONG": [{"session": "2026-08-06", "o": 4.0, "c": 7.0}],
            "SHORT": [{"session": "2026-08-06", "o": 1.0, "c": 2.0}],
        },
        stock_daily={"AAPL": [{"session": "2026-08-06", "o": 101.0, "h": 106.0, "c": 103.0}]},
        latest_market_session="2026-08-06",
        latest_state=row,
    )
    assert result["entry_session"] == "2026-08-06"
    assert result["entry_debit"] == 3.0
    assert round(result["spread_return_pct"], 8) == round((5.0 / 3.0 - 1.0) * 100.0, 8)
    assert result["target_remaining_bucket"] == "2_to_5_pct"
    assert result["target_hit_after_delayed_entry"] is True
    assert result["persisted_tier_a_to_last_capture"] is True


def test_cluster_strategy_business_sessions_are_counted_after_entry():
    module = load_script("run_cipher_cluster_strategy")
    assert module.business_sessions_after("2026-08-07", "2026-08-14") == 5
    assert module.business_sessions_after("2026-08-07", "2026-08-10") == 1


def test_cluster_strategy_spread_checks_apply_liquidity_and_economics():
    module = load_script("run_cipher_cluster_strategy")
    candidate = {
        "latest_target": 71.0,
        "quote_geometry": {
            "spread_width": 2.0,
            "midpoint_debit": 0.75,
            "natural_debit": 0.9,
            "long_bid": 1.9,
            "long_ask": 2.1,
            "short_bid": 1.2,
            "short_ask": 1.3,
            "long_relative_quote_width": 0.10,
            "short_relative_quote_width": 0.08,
            "long_volume": 25,
            "short_volume": 20,
            "long_open_interest": 40,
            "short_open_interest": 60,
            "reference_breakeven": 69.75,
        },
    }
    result = module.spread_checks(candidate)
    assert result["quote_integrity_pass"] is True
    assert result["quote_widths_pass"] is True
    assert result["depth_pass"] is True
    assert result["economics_pass"] is True
    assert result["maximum_limit_debit"] == 0.7875


def test_candidate_news_relevance_filters_provider_cross_tags():
    module = load_script("ingest_public_events")
    assert module.yahoo_headline_relevance(
        title="The Takeover Case for OPLN",
        symbol="CVNA",
        raw_related={"OPLN", "CVNA", "KMX"},
        company_names=("Carvana Co.",),
    ) == "multi_ticker_context_only"
    assert module.yahoo_headline_relevance(
        title="Company Insider Dumps Nearly 4,000 Shares of Used Car Seller",
        symbol="CVNA",
        raw_related={"CVNA"},
        company_names=("Carvana Co.",),
    ) == "single_ticker_provider_metadata"
    assert module.yahoo_headline_relevance(
        title="Analyst Report: Carvana Co",
        symbol="CVNA",
        raw_related={"CVNA", "^GSPC"},
        company_names=("Carvana Co.",),
    ) == "direct_ticker_or_company"


def test_cluster_strategy_catalysts_separate_company_and_market_context():
    module = load_script("run_cipher_cluster_strategy")
    through = datetime(2026, 8, 7, 3, 0, tzinfo=timezone.utc)
    result = module.catalyst_context(
        [
            {
                "title": "Stocks Settle Lower as Middle East Tensions Rise",
                "publication_time": "2026-08-06T20:33:53+00:00",
                "company_specific": False,
                "positive_probability": 0.01,
                "negative_probability": 0.90,
            },
            {
                "title": "Analyst Report: Carvana Co",
                "publication_time": "2026-08-06T18:14:37+00:00",
                "company_specific": True,
                "positive_probability": 0.55,
                "negative_probability": 0.10,
            },
        ],
        ticker="CVNA",
        direction="BULLISH",
        through=through,
    )
    assert result["event_count_48h"] == 1
    assert [row["title"] for row in result["events"]] == ["Analyst Report: Carvana Co"]
    assert [row["title"] for row in result["market_context_events"]] == [
        "Stocks Settle Lower as Middle East Tensions Rise"
    ]


def test_cluster_strategy_quote_session_marks_after_hours_reference():
    module = load_script("run_cipher_cluster_strategy")
    assert module.quote_session_context("2026-08-06T19:59:59Z") == "regular_session"
    assert module.quote_session_context("2026-08-07T00:00:00Z") == "extended_hours_reference"


def test_cluster_strategy_auxiliary_sources_are_non_gating():
    module = load_script("run_cipher_cluster_strategy")
    missing = module.source_modifier(None, expected_direction="BULLISH", source="flash")
    aligned = module.source_modifier(
        {"direction": "BULLISH", "score": 75, "setup_family": "momentum_push"},
        expected_direction="BULLISH",
        source="flash_agentic",
    )
    assert missing["modifier_points"] == 0
    assert missing["is_trade_gate"] is False
    assert aligned["modifier_points"] > 0
    assert aligned["is_trade_gate"] is False


def test_cluster_strategy_freezes_latest_raw_scan_before_analysis(tmp_path: Path):
    module = load_script("run_cipher_cluster_strategy")
    capture_root = tmp_path / "browser_ingest"
    snapshot_root = tmp_path / "snapshots"
    capture_root.mkdir()
    source = capture_root / "cluster-scans-v2-2026-08-07.jsonl"
    source.write_text(
        json.dumps(
            {
                "scan_type": "cluster",
                "client_timestamp": "2026-08-07T09:45:00-04:00",
                "records": 2,
                "normalized_cards": [{"ticker": "CVNA"}, {"ticker": "CRWD"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = module.freeze_latest_cluster_scan(
        capture_root=capture_root,
        snapshot_root=snapshot_root,
        observed_at=datetime(2026, 8, 7, 13, 45, tzinfo=timezone.utc),
    )
    snapshot = result["snapshot"]
    assert snapshot["snapshot_stage"] == "raw_cluster_scan_frozen_before_strategy_analysis"
    assert snapshot["source_record"]["records"] == 2
    assert Path(result["path"]).is_file()
    repeated = module.freeze_latest_cluster_scan(
        capture_root=capture_root,
        snapshot_root=snapshot_root,
        observed_at=datetime(2026, 8, 7, 13, 45, tzinfo=timezone.utc),
    )
    assert repeated["path"] == result["path"]


def test_trade_postmortem_source_context_preserves_temporal_boundary():
    module = load_script("run_cipher_trade_postmortems")
    signal_at = datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc)
    records = [
        episode(signal_id="before", source="flash", timestamp="2026-08-05T14:30:00+00:00", direction="BEARISH"),
        episode(signal_id="after", source="flash", timestamp="2026-08-05T15:30:00+00:00", direction="BULLISH"),
    ]
    result = module.source_timeline_context(
        records,
        ticker="AAPL",
        session="2026-08-05",
        signal_at=signal_at,
    )
    assert result["available_at_cluster_signal"]["signal_id"] == "before"
    assert result["available_at_cluster_signal"]["relationship_to_cluster"] == "opposite_direction"
    assert result["first_post_cluster_signal"]["signal_id"] == "after"
    assert result["first_post_cluster_signal"]["lag_minutes_from_cluster"] == 30.0


def test_trade_postmortem_event_windows_separate_pre_entry_and_holding():
    module = load_script("run_cipher_trade_postmortems")
    signal_at = datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc)
    hold_end = datetime(2026, 8, 6, 20, 0, tzinfo=timezone.utc)
    result = module.trade_event_context(
        ticker="AAPL",
        signal_at=signal_at,
        hold_end=hold_end,
        ticker_events=[
            {
                "title": "Apple pre-entry catalyst",
                "publication_time": "2026-08-05T14:00:00+00:00",
                "company_specific": True,
                "positive_probability": 0.8,
                "negative_probability": 0.1,
            },
            {
                "title": "Apple holding-period risk",
                "publication_time": "2026-08-06T16:00:00+00:00",
                "company_specific": True,
                "positive_probability": 0.1,
                "negative_probability": 0.8,
            },
            {
                "title": "Broad market story",
                "publication_time": "2026-08-06T17:00:00+00:00",
                "company_specific": False,
                "positive_probability": 0.2,
                "negative_probability": 0.2,
            },
        ],
        market_events=[],
    )
    assert result["pre_entry_window"]["company_specific"]["count"] == 1
    assert result["pre_entry_window"]["company_specific"]["sentiment_direction"] == "positive"
    assert result["during_holding_window"]["company_specific"]["count"] == 1
    assert result["during_holding_window"]["company_specific"]["sentiment_direction"] == "negative"
    assert result["during_holding_window"]["market_or_cross_tagged"]["count"] == 1


def test_trade_postmortem_fill_quality_flags_asynchronous_legs():
    module = load_script("run_cipher_trade_postmortems")
    result = module.fill_quality(
        {
            "modeled_entry_debit": 2.5,
            "long_entry_time_et": "2026-08-05T10:00:00-04:00",
            "short_entry_time_et": "2026-08-05T10:12:00-04:00",
        }
    )
    assert result["label"] == "asynchronous_modeled_legs"
    assert result["leg_entry_time_gap_minutes"] == 12.0
    assert result["simultaneous_fill_proven"] is False


def test_trade_postmortem_attribution_never_claims_causality():
    module = load_script("run_cipher_trade_postmortems")
    event_context = {
        "pre_entry_window": {"company_specific": {"count": 0, "sentiment_direction": "unavailable"}},
        "during_holding_window": {"company_specific": {"count": 1, "sentiment_direction": "positive"}},
    }
    label, confidence = module.attribution_class(
        underlying_return=5.0,
        excess_vs_spy=4.0,
        event_context=event_context,
    )
    assert label == "company_positive_catalyst_supported"
    assert confidence == "moderate_context_not_causal"


def test_cluster_strategy_backtest_requires_consecutive_qualification():
    module = load_script("run_cipher_cluster_strategy_backtest")
    first = datetime(2026, 8, 5, 13, 45, tzinfo=timezone.utc)
    second = datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc)

    def row(room: float) -> dict:
        return {
            "market_session": "2026-08-05",
            "ticker": "AAPL",
            "direction": "BULLISH",
            "rank": 4,
            "strength": 240,
            "geometry_valid": True,
            "target_distance_pct": room,
            "cluster_expiration": "2026-08-14",
            "standalone_assessment": {"research_tier": "tier_a_cluster_only"},
            "atm_contract": {"symbol": "LONG", "strike_price": 100.0},
            "target_contract": {"symbol": "SHORT", "strike_price": 105.0},
        }

    scenario = module.Scenario(
        "test",
        confirmation="strict_consecutive",
        target_low=2.0,
        target_high=5.0,
    )
    scans = {"2026-08-05": [first, second]}
    lookup = {
        ("2026-08-05", first): {"AAPL": row(3.0)},
        ("2026-08-05", second): {"AAPL": row(3.5)},
    }
    candidates = module.candidate_records(scenario, scans, lookup)
    assert len(candidates) == 1
    assert candidates[0]["scan_at"] == second

    lookup[("2026-08-05", first)]["AAPL"]["target_distance_pct"] = 7.0
    assert module.candidate_records(scenario, scans, lookup) == []


def test_cluster_strategy_backtest_reconstructed_entry_preserves_lag():
    module = load_script("run_cipher_cluster_strategy_backtest")
    scan_at = datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc)
    candidate = {
        "session": "2026-08-05",
        "scan_at": scan_at,
        "ticker": "AAPL",
        "long_symbol": "LONG",
        "short_symbol": "SHORT",
        "long_strike": 100.0,
        "short_strike": 105.0,
        "row": {
            "target": 105.0,
            "atm_option": {"entry_price": 3.0, "entry_at": "2026-08-05T14:32:00+00:00"},
            "target_option": {"entry_price": 1.0, "entry_at": "2026-08-05T14:34:00+00:00"},
        },
    }
    passing = module.Scenario(
        "pass",
        confirmation="strict_consecutive",
        target_low=2.0,
        target_high=5.0,
        entry_model="record_first_prints",
        maximum_entry_lag_minutes=5,
        adverse_execution_fraction=0.0,
    )
    result = module.entry_proxy(candidate, passing, {})
    assert result.valid is True
    assert result.print_gap_minutes == 2.0
    assert result.observed_at == datetime(2026, 8, 5, 14, 34, tzinfo=timezone.utc)

    failing = module.Scenario(
        "fail",
        confirmation="strict_consecutive",
        target_low=2.0,
        target_high=5.0,
        entry_model="record_first_prints",
        maximum_entry_lag_minutes=3,
        adverse_execution_fraction=0.0,
    )
    assert module.entry_proxy(candidate, failing, {}).reason == "reconstructed_entry_exceeded_maximum_lag"


def test_cluster_strategy_backtest_underlying_target_exit():
    module = load_script("run_cipher_cluster_strategy_backtest")
    scan_at = datetime(2026, 8, 5, 14, 30, tzinfo=timezone.utc)
    candidate = {
        "session": "2026-08-05",
        "scan_at": scan_at,
        "ticker": "AAPL",
        "expiry": "2026-08-14",
        "row": {
            "target": 105.0,
            "target_distance_pct": 5.0,
            "rank": 4,
            "strength": 240,
        },
    }
    qualifying = {
        "direction": "BULLISH",
        "rank": 4,
        "strength": 240,
        "geometry_valid": True,
        "target_distance_pct": 5.0,
        "standalone_assessment": {"research_tier": "tier_a_cluster_only"},
    }
    bars = {
        ("2026-08-05", "AAPL"): [
            {
                "timestamp": datetime(2026, 8, 5, 14, 31, tzinfo=timezone.utc),
                "o": 100.0,
                "h": 100.2,
                "l": 99.9,
                "c": 100.0,
                "vw": 100.0,
                "v": 1000,
            },
            {
                "timestamp": datetime(2026, 8, 5, 14, 40, tzinfo=timezone.utc),
                "o": 104.5,
                "h": 105.2,
                "l": 104.4,
                "c": 105.0,
                "vw": 105.0,
                "v": 1000,
            },
        ]
    }
    result = module.underlying_trade(
        candidate,
        exit_policy="managed",
        stock_bars=bars,
        scans={"2026-08-05": [scan_at]},
        lookup={("2026-08-05", scan_at): {"AAPL": qualifying}},
        latest_session="2026-08-05",
    )
    assert result is not None
    assert result["exit_reason"] == "underlying_target_hit"
    assert round(result["return_pct"], 8) == 5.0


def test_live_cluster_trade_board_has_no_execution_authority():
    path = ROOT / "data" / "governance" / "cipher_signal_only" / "latest_cluster_trade_candidates.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["mode"] == "cluster_tier_a_delayed_entry_research_and_read_only_trade_board"
    assert payload["automatic_promotion"] is False
    assert payload["paper_or_live_execution"] is False
    assert payload["execution_authority"] is False
    assert payload["current_trade_board"]["candidates"]
