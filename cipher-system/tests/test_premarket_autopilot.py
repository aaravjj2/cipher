from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
from copy import deepcopy
from datetime import datetime, timezone

from core.paper_executor import autopilot_planner as planner
from core.paper_executor import autopilot_scheduler as scheduler


def stamp(hour: int, minute: int) -> datetime:
    # August is EDT: 08:00 ET is 12:00 UTC.
    return datetime(2026, 8, 17, hour + 4, minute, tzinfo=timezone.utc)


def evidence(phase: str) -> dict:
    return {
        "snapshot_id": "a" * 64,
        "feed": "opra",
        "freshness": {"status": "current", "age_seconds": 2.0},
        "coverage": {"status": "sufficient", "contracts": 500, "calculated_cells": 20},
        "session": {"phase": phase, "timezone": "America/New_York", "market_date": "2026-08-17"},
        "event_at": stamp(8, 0).isoformat(),
        "replay_available": True,
    }


def card(*, phase: str = "premarket", state: str = "") -> dict:
    return {
        "ticker": "MU", "direction": "BULLISH", "score": 82.0,
        "spot": 100.0, "target": 104.0, "invalidation": 98.0,
        "reward_risk": 2.0, "setup_type": "CIPHER MODEL",
        "rank_eligible": True, "geometry_valid": True, "actionable": True,
        "agent_state": state, "evidence_snapshot": evidence(phase),
    }


def test_phase_machine_never_enters_during_premarket_or_opening_candle() -> None:
    assert planner.phase_at(stamp(8, 0)) == planner.AutopilotPhase.PREMARKET_DISCOVERY
    assert planner.phase_at(stamp(9, 32)) == planner.AutopilotPhase.OPENING_WAIT
    assert planner.phase_at(stamp(9, 35)) == planner.AutopilotPhase.ENTRY_CONFIRMATION
    assert planner.phase_at(stamp(12, 0)) == planner.AutopilotPhase.MONITOR_ONLY
    assert planner.phase_at(stamp(15, 45)) == planner.AutopilotPhase.FORCE_CLOSE


def test_finbert_context_is_point_in_time_stale_and_never_authoritative(tmp_path) -> None:
    registry = tmp_path / "registry.sqlite"
    with sqlite3.connect(registry) as db:
        db.execute("create table news_events (available_at text, payload_json text)")
        past = {
            "symbols": ["MU"], "positive_probability": 0.9,
            "negative_probability": 0.05, "sentiment_model_id": "finbert@pinned",
        }
        future = {
            "symbols": ["MU"], "positive_probability": 0.01,
            "negative_probability": 0.98, "sentiment_model_id": "finbert@future",
        }
        db.execute("insert into news_events values (?,?)", ("2026-08-15T12:00:00+00:00", json.dumps(past)))
        db.execute("insert into news_events values (?,?)", ("2026-08-18T12:00:00+00:00", json.dumps(future)))
    context = planner.sentiment_context(["MU"], as_of=stamp(8, 0), registry=registry)
    assert context["MU"]["status"] == "stale"
    assert context["MU"]["score"] == 0.85
    assert context["MU"]["model_ids"] == ["finbert@pinned"]
    assert context["MU"]["directional_authority"] is False


def test_plan_is_watch_only_and_rejects_non_premarket_evidence() -> None:
    regular = card(phase="regular")
    scan = {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card(), regular]}
    plan = planner.build_premarket_plan(scan, now=stamp(8, 0))
    assert plan["state"] == "WATCHLIST_ONLY"
    assert plan["entry_policy"]["premarket_entry_allowed"] is False
    assert plan["live_execution_capability"] is False
    assert len(plan["candidates"]) == 1
    assert plan["rejected"][0]["reasons"] == ["not_premarket_evidence"]


def test_confirmation_requires_trigger_same_direction_and_regular_fresh_evidence() -> None:
    plan = planner.build_premarket_plan(
        {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]},
        now=stamp(8, 0),
    )
    # A Flash-Agentic card that is not yet triggered is still rejected even though
    # the strategy is no longer the primary confirmation source.
    waiting = card(phase="regular", state="arming")
    waiting["evidence_snapshot"]["event_at"] = stamp(9, 35).isoformat()
    result = planner.confirmation_payload(plan, {"as_of": stamp(9, 35).isoformat(), "top": [waiting]}, now=stamp(9, 35))
    assert result["cards"] == []
    assert result["rejected"][0]["reasons"] == ["setup_not_triggered"]

    triggered = deepcopy(waiting)
    triggered["agent_state"] = "triggered"
    triggered["evidence_snapshot"]["snapshot_id"] = "b" * 64
    result = planner.confirmation_payload(plan, {"as_of": stamp(9, 35).isoformat(), "top": [triggered]}, now=stamp(9, 35))
    assert len(result["cards"]) == 1
    assert result["cards"][0]["autopilot"]["sentiment_directional_authority"] is False
    assert result["live_execution_capability"] is False


def test_regular_session_cipher_card_confirms_without_flash_agentic_state() -> None:
    plan = planner.build_premarket_plan(
        {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]},
        now=stamp(8, 0),
    )
    regular = card(phase="regular", state="")
    regular["setup_type"] = "CIPHER MODEL"
    regular["evidence_snapshot"]["snapshot_id"] = "c" * 64
    regular["evidence_snapshot"]["event_at"] = stamp(9, 35).isoformat()
    result = planner.confirmation_payload(
        plan,
        {"as_of": stamp(9, 35).isoformat(), "strategy": "cipher", "top": [regular]},
        now=stamp(9, 35),
    )
    assert len(result["cards"]) == 1
    assert result["cards"][0]["ticker"] == "MU"
    assert result["rejected"] == []


def test_confirmation_merges_multiple_scans_without_duplicate_cards() -> None:
    plan = planner.build_premarket_plan(
        {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]},
        now=stamp(8, 0),
    )
    cipher_card = card(phase="regular", state="")
    cipher_card["evidence_snapshot"]["snapshot_id"] = "d" * 64
    cipher_card["evidence_snapshot"]["event_at"] = stamp(9, 35).isoformat()
    flash_card = deepcopy(cipher_card)
    flash_card["agent_state"] = "triggered"
    flash_card["setup_type"] = "triple cluster (3 peaks, above)"
    flash_card["evidence_snapshot"]["snapshot_id"] = "e" * 64
    scans = [
        {"as_of": stamp(9, 35).isoformat(), "strategy": "cipher", "top": [cipher_card]},
        {"as_of": stamp(9, 35).isoformat(), "strategy": "flash", "top": [flash_card]},
    ]
    result = planner.confirmation_payload(plan, scans=scans, now=stamp(9, 35))
    assert len(result["cards"]) == 1
    assert result["confirmation_sources"] == ["cipher"]
    assert result["scan_types"] == ["cipher", "flash"]


def test_confirmation_rejects_cards_outside_entry_window() -> None:
    plan = planner.build_premarket_plan(
        {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]},
        now=stamp(8, 0),
    )
    regular = card(phase="regular", state="")
    regular["evidence_snapshot"]["snapshot_id"] = "f" * 64
    regular["evidence_snapshot"]["event_at"] = stamp(9, 35).isoformat()
    result = planner.confirmation_payload(
        plan,
        {"as_of": stamp(12, 0).isoformat(), "strategy": "cipher", "top": [regular]},
        now=stamp(12, 0),
    )
    assert result["cards"] == []
    assert result["rejected"][0]["reasons"] == ["outside_entry_confirmation_window"]


def test_plan_entry_policy_no_longer_requires_flash_agentic() -> None:
    plan = planner.build_premarket_plan(
        {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]},
        now=stamp(8, 0),
    )
    policy = plan["entry_policy"]
    assert "flash" not in policy["required_confirmation"].lower()
    assert "cipher" in policy["required_confirmation"].lower()


def test_scheduler_premarket_writes_plan_but_never_calls_executor(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "internal-token")
    monkeypatch.setenv("ALPACA_ALGO_KEY", "server-key")
    monkeypatch.setenv("ALPACA_ALGO_SECRET", "server-secret")
    calls: list[tuple[str, bool]] = []

    def request(url: str, *, payload=None, timeout=600):
        calls.append((url, payload is not None))
        if "finviz-discovery" in url:
            return {"symbols": ["MU"]}
        if "/internal/provider-session" in url:
            return {"provider_session_id": "opaque-test-session"}
        return {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]}

    monkeypatch.setattr(scheduler, "request_json", request)
    monkeypatch.setattr(scheduler, "sentiment_context", lambda *_args, **_kwargs: {})
    result = scheduler.run_cycle(
        now=stamp(8, 0), plan_path=tmp_path / "plan.json", status_path=tmp_path / "status.json",
    )
    assert result["action"] == "premarket_plan_saved"
    assert result["premarket_entries"] == 0
    assert all("/api/scanner-ingest" not in url for url, _ in calls)
    assert json.loads((tmp_path / "plan.json").read_text())["live_execution_capability"] is False
    trace = tmp_path / "cycles" / "2026-08-17.jsonl"
    assert trace.is_file()
    event = json.loads(trace.read_text().splitlines()[-1])
    assert event["action"] == "premarket_plan_saved"
    assert event["candidate_tickers"] == ["MU"]
    assert len(event["cycle_id"]) == 24


def test_scheduler_records_retryable_plan_failure_without_writing_stale_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "internal-token")
    monkeypatch.setenv("ALPACA_ALGO_KEY", "server-key")
    monkeypatch.setenv("ALPACA_ALGO_SECRET", "server-secret")

    def request(url: str, *, payload=None, timeout=600):
        if "finviz-discovery" in url:
            return {"symbols": ["MU"]}
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(scheduler, "request_json", request)
    result = scheduler.run_cycle(
        now=stamp(8, 0), plan_path=tmp_path / "plan.json", status_path=tmp_path / "status.json",
    )
    assert result["action"] == "premarket_plan_unavailable"
    assert result["reason"] == "premarket_provider_unavailable"
    assert result["retryable"] is True
    assert result["error_type"] == "HTTPError"
    assert not (tmp_path / "plan.json").exists()
    trace = tmp_path / "cycles" / "2026-08-17.jsonl"
    event = json.loads(trace.read_text().splitlines()[-1])
    assert event["action"] == "premarket_plan_unavailable"


def test_scheduler_confirmation_uses_only_cipher_and_submits_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "internal-token")
    monkeypatch.setenv("ALPACA_ALGO_KEY", "server-key")
    monkeypatch.setenv("ALPACA_ALGO_SECRET", "server-secret")
    calls: list[dict] = []

    def request(url: str, *, payload=None, timeout=600):
        calls.append({"url": url, "payload": payload})
        if "/internal/provider-session" in url:
            return {"provider_session_id": "opaque-test-session"}
        if "finviz-discovery" in url:
            return {"symbols": ["MU"]}
        if "/api/scan" in url and "strategy=flash_agentic" in url:
            return {"as_of": stamp(9, 35).isoformat(), "strategy": "flash_agentic", "top": []}
        if "/api/scan" in url and "strategy=flash" in url:
            return {"as_of": stamp(9, 35).isoformat(), "strategy": "flash", "top": []}
        if "/api/scan" in url and "strategy=cipher" in url:
            regular = card(phase="regular", state="")
            regular["evidence_snapshot"]["snapshot_id"] = "e" * 64
            regular["evidence_snapshot"]["event_at"] = stamp(9, 35).isoformat()
            return {"as_of": stamp(9, 35).isoformat(), "strategy": "cipher", "top": [regular]}
        if "/api/scanner-ingest" in url:
            return {"batch_id": "batch-1"}
        return {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]}

    monkeypatch.setattr(scheduler, "request_json", request)
    plan = scheduler._load_plan
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(planner.build_premarket_plan(
        {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]},
        now=stamp(8, 0),
    )))
    result = scheduler.run_cycle(
        now=stamp(9, 35), plan_path=plan_path, status_path=tmp_path / "status.json",
    )
    # /api/scanner-ingest contains the substring "/api/scan", so match the scan
    # endpoint boundary explicitly (path end, ? or &) instead of a bare substring.
    scan_calls = [call for call in calls if re.search(r"/api/scan(?:\?|$)", call["url"])]
    strategies = [url.split("strategy=")[1].split("&")[0] for url in [call["url"] for call in scan_calls]]
    assert strategies == ["cipher"]
    ingest = [call for call in calls if "/api/scanner-ingest" in call["url"]]
    assert len(ingest) == 1
    submitted = ingest[0]["payload"]
    assert submitted["scan_type"] == "cipher"
    assert len(submitted["cards"]) == 1
    assert submitted["cards"][0]["ticker"] == "MU"
    assert result["action"] == "paper_confirmations_submitted"
    assert result["cards_submitted"] == 1
    assert result["batch_accepted"] is True
    assert result["confirmation_sources"] == ["cipher"]


def test_scheduler_blocks_ingest_when_executor_opra_probe_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "internal-token")
    monkeypatch.setenv("ALPACA_ALGO_KEY", "server-key")
    monkeypatch.setenv("ALPACA_ALGO_SECRET", "server-secret")
    calls = []

    def request(url: str, *, payload=None, timeout=600):
        calls.append(url)
        if "/internal/provider-session" in url:
            return {"provider_session_id": "opaque-test-session"}
        if "/api/paper/market-data-probe" in url:
            raise urllib.error.HTTPError(url, 503, "OPRA unavailable", {}, None)
        if "/api/scan" in url:
            regular = card(phase="regular", state="")
            regular["evidence_snapshot"]["event_at"] = stamp(9, 35).isoformat()
            return {"as_of": stamp(9, 35).isoformat(), "strategy": "cipher", "top": [regular]}
        return {"status": "ok"}

    monkeypatch.setattr(scheduler, "request_json", request)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(planner.build_premarket_plan(
        {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]},
        now=stamp(8, 0),
    )))
    result = scheduler.run_cycle(
        now=stamp(9, 35), plan_path=plan_path, status_path=tmp_path / "status.json",
    )
    assert result["action"] == "blocked"
    assert result["reason"] == "executor_market_data_unavailable"
    assert not any("/api/scanner-ingest" in url for url in calls)


def test_premarket_payload_enters_plan_candidates_with_premarket_evidence() -> None:
    scan = {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]}
    plan = planner.build_premarket_plan(
        scan, now=stamp(8, 0), premarket_entry_allowed=True,
    )
    assert plan["entry_policy"]["premarket_entry_allowed"] is True
    result = planner.premarket_payload(plan, scan, now=stamp(8, 0))
    assert len(result["cards"]) == 1
    entry = result["cards"][0]
    assert entry["ticker"] == "MU"
    assert entry["direction"] == "BULLISH"
    assert entry["premarket_entry"] is True
    assert entry["confirmation_strategy"] == "premarket_plan"
    assert entry["autopilot"]["paper_only"] is True
    assert entry["signal_record"]["decision"] == "accepted"
    assert result["premarket_entry"] is True
    assert result["paper_only"] is True
    assert result["live_execution_capability"] is False
    assert result["confirmation_sources"] == ["premarket_plan"]


def test_premarket_payload_rejects_stale_or_wrong_direction_candidates() -> None:
    scan = {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]}
    plan = planner.build_premarket_plan(scan, now=stamp(8, 0))
    stale = card()
    stale["evidence_snapshot"]["freshness"]["status"] = "stale"
    wrong = card()
    wrong["ticker"] = "NVDA"
    wrong["direction"] = "BEARISH"
    plan["candidates"].append({
        "ticker": "NVDA", "direction": "BULLISH", "score": 70.0,
        "candidate_id": "candidate_nvda", "evidence_snapshot_id": "x" * 64,
        "sentiment": {"status": "unavailable"}, "state": "WATCHING_FOR_RTH_CONFIRMATION",
    })
    scan["top"] = [stale, wrong]
    result = planner.premarket_payload(plan, scan, now=stamp(8, 0))
    assert result["cards"] == []
    by_ticker = {row["ticker"]: row["reasons"] for row in result["rejected"]}
    assert by_ticker["MU"] == ["evidence_not_current"]
    assert by_ticker["NVDA"] == ["direction_changed"]


def test_scheduler_premarket_mode_defers_candidates_until_options_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "internal-token")
    monkeypatch.setenv("ALPACA_ALGO_KEY", "server-key")
    monkeypatch.setenv("ALPACA_ALGO_SECRET", "server-secret")
    monkeypatch.setenv("CIPHER_AUTOPILOT_PREMARKET_ENTRY", "1")
    calls: list[dict] = []

    def request(url: str, *, payload=None, timeout=600):
        calls.append({"url": url, "payload": payload})
        if "/internal/provider-session" in url:
            return {"provider_session_id": "opaque-test-session"}
        if "finviz-discovery" in url:
            return {"symbols": ["MU"]}
        if "/api/scanner-ingest" in url:
            return {"batch_id": "premarket-batch-1"}
        return {"as_of": stamp(8, 0).isoformat(), "strategy": "cipher", "top": [card()]}

    monkeypatch.setattr(scheduler, "request_json", request)
    monkeypatch.setattr(scheduler, "sentiment_context", lambda *_args, **_kwargs: {})
    result = scheduler.run_cycle(
        now=stamp(8, 0), plan_path=tmp_path / "plan.json", status_path=tmp_path / "status.json",
    )
    assert result["action"] == "premarket_plan_saved"
    assert result["premarket_entry_mode"] is False
    assert result["premarket_entry_requested"] is True
    assert result["premarket_entry_deferred"] is True
    assert result["premarket_entries"] == 0
    ingest = [call for call in calls if "/api/scanner-ingest" in call["url"]]
    assert ingest == []
    saved = json.loads((tmp_path / "plan.json").read_text())
    assert saved["entry_policy"]["premarket_entry_allowed"] is False
    trace = tmp_path / "cycles" / "2026-08-17.jsonl"
    event = json.loads(trace.read_text().splitlines()[-1])
    assert event["action"] == "premarket_plan_saved"
    assert event["premarket_entries"] == 0
    assert event["premarket_entry_deferred"] is True


def test_service_provider_session_is_ephemeral_and_disconnects(tmp_path, monkeypatch):
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "internal-token")
    monkeypatch.setenv("ALPACA_ALGO_KEY", "server-key")
    monkeypatch.setenv("ALPACA_ALGO_SECRET", "server-secret")
    monkeypatch.setenv("ALPACA_DATA_FEED", "opra")
    monkeypatch.setenv("ALPACA_STOCK_FEED", "sip")
    monkeypatch.delenv("CIPHER_PROVIDER_SESSION", raising=False)
    calls = []

    def request(url: str, *, payload=None, timeout=600):
        calls.append((url, payload))
        if payload and payload.get("action") == "connect":
            assert payload["key"] == "server-key"
            assert payload["secret"] == "server-secret"
            return {"provider_session_id": "opaque-session"}
        return {"status": "disconnected"}

    monkeypatch.setattr(scheduler, "request_json", request)
    with scheduler.service_provider_session("http://127.0.0.1:8282") as session_id:
        assert session_id == "opaque-session"
        assert os.environ["CIPHER_PROVIDER_SESSION"] == "opaque-session"
    assert "CIPHER_PROVIDER_SESSION" not in os.environ
    assert [payload.get("action") for _, payload in calls] == ["connect", "disconnect"]
