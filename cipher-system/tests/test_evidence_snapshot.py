from __future__ import annotations

from copy import deepcopy

from core import evidence_snapshot
from core import app
from core import scanner


def _matrix_payload() -> dict:
    return {
        "ticker": "AAPL",
        "as_of": "2026-08-17T13:30:02+00:00",
        "feed": "opra",
        "quote": {
            "ticker": "AAPL", "price_context": 200.0,
            "as_of": "2026-08-17T13:30:00+00:00", "feed": "sip",
            "day_change_pct": 0.5,
        },
        "expirations": ["2026-08-21"],
        "rows": [{"strike": 200.0, "cells": [{"available": False}]}],
        "coverage": {
            "contracts": 40, "calculated_cells": 20, "listed_cells": 24,
            "contracts_missing_gamma": 4,
            "open_interest_as_of": "2026-08-14",
            "open_interest_source": "Alpaca option-contract metadata",
        },
        "summary": {
            "global_max_strike": 200.0, "call_wall_strike": 205.0,
            "put_wall_strike": 195.0, "gamma_flip_level": 201.0,
        },
    }


def test_view_neutral_identity_matches_scanner_and_night_vision(monkeypatch) -> None:
    source = _matrix_payload()

    def matrix_fn(*_args, **_kwargs):
        return deepcopy(source)

    scan = scanner.analyze_ticker(
        matrix_fn, "AAPL", "opra", "short", "cipher", bars_fn=None
    )
    monkeypatch.setattr(app, "matrix", matrix_fn)
    monkeypatch.setattr(app, "bars", lambda *_args, **_kwargs: {"bars": []})
    chart = app.night_vision("AAPL", "opra", 0.06, 1)

    assert scan["evidence_snapshot"]["snapshot_id"] == chart["evidence_snapshot"]["snapshot_id"]
    assert scan["evidence_snapshot"]["view"] == "setup_scanner"
    assert chart["evidence_snapshot"]["view"] == "night_vision"
    assert chart["evidence_snapshot"]["session"]["phase"] == "regular"
    assert chart["evidence_snapshot"]["execution_capability"] is False

    run = scanner.run_scan(
        matrix_fn, universe=["AAPL"], workers=1, cache_seconds=0, save_history=False
    )
    assert run["evidence_snapshot_ids"] == [scan["evidence_snapshot"]["snapshot_id"]]
    assert run["rejected_examples"][0]["evidence_snapshot"]["snapshot_id"] == scan["evidence_snapshot"]["snapshot_id"]


def test_night_vision_fails_over_to_explicit_bounded_cache(monkeypatch) -> None:
    source = _matrix_payload()
    monkeypatch.setattr(app, "_night_vision_cache_key", lambda *_args: "fixture-night-vision")
    monkeypatch.setattr(app, "matrix", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider timeout")))
    monkeypatch.setattr(app.disk_cache, "get", lambda key, ttl: deepcopy(source) if key == "fixture-night-vision" and ttl == 300 else None)
    monkeypatch.setattr(app, "bars", lambda *_args, **_kwargs: {"bars": []})

    chart = app.night_vision("AAPL", "opra", 0.06, 1)

    assert chart["data_status"] == "stale_cache"
    assert "provider timeout" in chart["provider_error"]
    assert "bounded cached snapshot" in chart["cache_note"]
    assert chart["evidence_snapshot"]["freshness"]["status"] == "stale"


def test_missing_evidence_is_explicit_and_never_coerced_to_zero() -> None:
    payload = {
        "ticker": "MU", "as_of": None,
        "feed": "indicative", "quote": {"price_context": None},
        "coverage": {}, "summary": {}, "rows": [], "expirations": [],
    }
    result = evidence_snapshot.build(payload, view="setup_scanner")
    assert result["spot"] is None
    assert result["coverage"]["calculated_cells"] is None
    assert result["coverage"]["status"] == "unknown"
    assert {"spot_unknown", "event_time_unknown", "options_coverage_unknown", "options_feed_not_opra"} <= set(result["missing_reasons"])


def test_exchange_session_uses_new_york_dst() -> None:
    summer = _matrix_payload()
    winter = deepcopy(summer)
    winter["as_of"] = "2026-01-12T14:30:02+00:00"
    winter["quote"]["as_of"] = "2026-01-12T14:30:00+00:00"
    assert evidence_snapshot.build(summer, view="setup_scanner")["session"]["phase"] == "regular"
    assert evidence_snapshot.build(winter, view="setup_scanner")["session"]["phase"] == "regular"


def test_frozen_matrix_store_is_bounded_to_valid_snapshot_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(evidence_snapshot, "STORE_DIR", tmp_path)
    payload = _matrix_payload()
    snapshot = evidence_snapshot.build(payload, view="setup_scanner")

    assert evidence_snapshot.persist_matrix(payload, snapshot) is True
    artifact = evidence_snapshot.load_matrix(snapshot["snapshot_id"])
    assert artifact is not None
    assert artifact["matrix"] == payload
    assert artifact["execution_capability"] is False
    assert evidence_snapshot.load_matrix("../../auth.json") is None

    replay = app._night_vision_from_matrix(artifact["matrix"], "AAPL", include_session_levels=False)
    assert replay["evidence_snapshot"]["snapshot_id"] == snapshot["snapshot_id"]
    assert replay["session_levels"]["levels"] == []
    assert "not captured" in replay["session_levels"]["note"].lower()


def test_scan_persists_only_qualified_leaderboard_source(monkeypatch) -> None:
    evidence = evidence_snapshot.build(_matrix_payload(), view="setup_scanner")
    persisted: list[str] = []

    def fake_analyze(*_args, **_kwargs):
        return {
            "ticker": "AAPL", "spot": 200.0, "score": 80.0, "abs_score": 80.0,
            "direction": "BULLISH", "supports": [195.0], "resistances": [205.0],
            "target": 205.0, "invalidation": 198.0, "reward_risk": 2.5,
            "geometry_valid": True, "actionable": True, "setup_type": "CIPHER MODEL",
            "read": "fixture", "reason": "fixture", "pull_target": 205.0,
            "vacuum_targets": [], "setup_kind": "cipher_model", "quality_reasons": [],
            "evidence_snapshot": deepcopy(evidence), "_matrix_snapshot": _matrix_payload(),
        }

    monkeypatch.setattr(scanner, "analyze_ticker", fake_analyze)
    monkeypatch.setattr(
        scanner.evidence_snapshot,
        "persist_matrix",
        lambda _payload, snapshot: persisted.append(snapshot["snapshot_id"]) is None or True,
    )
    result = scanner.run_scan(
        lambda *_args, **_kwargs: {}, universe=["AAPL"], workers=1,
        cache_seconds=0, save_history=False,
    )
    assert persisted == [evidence["snapshot_id"]], result
    assert result["top"][0]["evidence_snapshot"]["replay_available"] is True
    assert "_matrix_snapshot" not in result["top"][0]
