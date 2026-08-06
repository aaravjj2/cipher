from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

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


def test_signal_only_sources_have_no_order_authority():
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "scripts/run_cipher_signal_only_research.py",
            "scripts/run_cipher_signal_only_loop.py",
            "scripts/manage_cipher_signal_only_loop.py",
            "scripts/run_cipher_signal_specifics.py",
        )
    )
    for forbidden in ("/v2/orders", "submit_order", "place_order", "TradingClient", "OrderClient"):
        assert forbidden not in sources
