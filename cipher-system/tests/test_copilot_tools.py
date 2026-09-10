"""Unit tests for the copilot tool layer - fixture stores only, no network."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.copilot import tools


@pytest.fixture
def capture_dir(tmp_path, monkeypatch):
    """A live_option_chains dir holding one NVDA capture file with two
    snapshots; the last line is what every reader must see."""
    directory = tmp_path / "live_option_chains"
    directory.mkdir()
    monkeypatch.setattr(tools, "CAPTURE_DIR", directory)
    return directory


def _contract(symbol, expiry, strike, kind, gamma=None, oi=None, iv=0.4):
    return {
        "symbol": symbol,
        "expiry": expiry,
        "strike": strike,
        "type": kind,
        "gamma": gamma,
        "open_interest": oi,
        "iv": iv,
        "bid": 1.0,
        "ask": 1.2,
        "mid": 1.1,
        "last": 1.05,
        "volume": 10,
    }


def _write_capture(directory: Path, ticker: str, day: str) -> None:
    stamp = f"{day}T14:00:00+00:00"
    contracts = [
        _contract(f"{ticker}260918C00100000", "2026-09-18", 100.0, "call", gamma=0.01, oi=500),
        _contract(f"{ticker}260918P00100000", "2026-09-18", 100.0, "put", gamma=0.02, oi=100),
        _contract(f"{ticker}260918P00110000", "2026-09-18", 110.0, "put", gamma=0.03, oi=200),
    ]
    path = directory / f"{day}_{ticker}.jsonl"
    with path.open("w") as handle:
        for offset in range(2):
            snapshot = {
                "timestamp": stamp,
                "ticker": ticker,
                "feed": "opra",
                "contract_count": len(contracts),
                "contracts": contracts if offset == 1 else contracts[:1],
            }
            handle.write(json.dumps(snapshot) + "\n")


def test_latest_capture_reads_only_final_line(capture_dir):
    _write_capture(capture_dir, "NVDA", "2026-08-25")
    snap = tools.latest_capture("nvda")
    assert snap is not None
    assert snap["ticker"] == "NVDA"
    # Two snapshots were written; only the final one may surface.
    assert len(snap["contracts"]) == 3


def test_latest_capture_missing_ticker_returns_none(capture_dir):
    assert tools.latest_capture("ZZZZ") is None


def test_derive_gex_uses_canonical_formula_and_walls(capture_dir, monkeypatch):
    _write_capture(capture_dir, "SPY", "2026-08-25")
    monkeypatch.setattr(tools, "get_quote", lambda ticker: {"error": "offline"})

    captured = {}

    def fake_gamma_snapshot(ticker):
        captured["ticker"] = ticker
        return {"spot": 200.0}

    monkeypatch.setattr(tools, "latest_gamma_snapshot", fake_gamma_snapshot)
    result = tools.derive_gex_from_capture("SPY")

    assert result["source"] == "local_capture_gex"
    spot = 200.0
    # strike 100: call +0.01*500*100*s^2*.01, put -(0.02*100*100*s^2*.01)
    cell = next(row for row in result["rows_preview"] if row["strike"] == 100.0)
    assert cell["call_gex"] == pytest.approx(0.01 * 500 * 100 * spot**2 * 0.01)
    assert cell["put_gex"] == pytest.approx(-(0.02 * 100 * 100 * spot**2 * 0.01))
    # cumulative net GEX is positive through strike 100 and negative by 110,
    # so the derived flip must sit on the higher strike.
    assert result["gamma_flip_strike"] == 110.0
    assert result["call_wall_strike"] == 100.0
    assert result["put_wall_strike"] == 110.0
    assert "heuristic" in result["note"]


def test_dispatch_reports_unknown_tool_as_data():
    result = tools.dispatch("nope", {})
    assert "unknown tool" in result["error"]
    assert "get_quote" in result["error"]


def test_dispatch_swallows_tool_exception():
    def boom(**kwargs):
        raise RuntimeError("disk on fire")

    original = dict(tools.TOOL_IMPLS)
    tools.TOOL_IMPLS["explode"] = boom
    try:
        result = tools.dispatch("explode", {})
        assert "RuntimeError" in result["error"]
    finally:
        tools.TOOL_IMPLS.clear()
        tools.TOOL_IMPLS.update(original)


def test_bounded_json_never_exceeds_budget_with_invalid_input():
    big = {"rows": [{"text": "x" * 500} for _ in range(50)]}
    encoded = tools.bounded_json(big, 900)
    assert len(encoded) <= 900
    json.loads(encoded)


def test_to_openai_specs_marks_required_params():
    specs = {spec["function"]["name"]: spec["function"] for spec in tools.to_openai_specs()}
    quote = specs["get_quote"]
    assert quote["parameters"]["required"] == ["ticker"]
    health = specs["get_market_health"]
    assert health["parameters"]["required"] == []


def test_get_market_health_lists_captures_and_providers(capture_dir, monkeypatch):
    _write_capture(capture_dir, "AAPL", "2026-08-25")
    monkeypatch.setattr(tools, "GEX_DB", capture_dir / "missing.sqlite")
    monkeypatch.setattr(tools, "TRADIER_DB", capture_dir / "missing.sqlite")
    monkeypatch.setattr(tools, "BARS_DB", capture_dir / "missing.sqlite")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    fresh = Path(__file__).resolve()  # any real file for env_path check inside env_key
    assert fresh.is_file()

    result = tools.get_market_health()
    names = [store["store"] for store in result["stores"]]
    assert any(name.startswith("chain_capture:AAPL") for name in names)
    assert result["llm_providers_configured"]["groq"] is True


def test_gamma_history_requires_store(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "GEX_DB", tmp_path / "none.sqlite")
    assert "error" in tools.get_gamma_history("SPY")


# ---------------- derived analytics ----------------


def test_technicals_trend_math():
    closes = [100.0] * 30 + [110.0] * 20  # step up; last above both MAs
    out = tools._technicals_from_closes(closes)
    assert out["trend"] == "up"
    assert out["above_ma20"] is True
    assert out["off_high_pct"] == 0.0
    down = [float(120 - i) for i in range(60)]  # steady decline
    out2 = tools._technicals_from_closes(down)
    assert out2["trend"] == "down"
    assert out2["above_ma50"] is False
    assert "error" in tools._technicals_from_closes([1.0] * 10)


def test_bucket_exposure_groups_and_coverage():
    import datetime as dt

    today = dt.date(2026, 8, 26)
    rows = [
        {
            "strike": 90.0,
            "cells": [
                {"expiration": "2026-08-28", "call_gex": 1e6, "put_gex": -0.5e6, "call_oi": 100, "put_oi": 300},
                {"expiration": "2026-09-18", "call_gex": None, "put_gex": -1e6, "net_vex": 2e5, "call_oi": 500, "put_oi": 250},
                {"expiration": "2026-11-20", "call_gex": 2e5, "put_gex": -3e5, "call_oi": None, "put_oi": 40},
                {"expiration": "garbage", "call_oi": 9},
            ],
        }
    ]
    out = tools._bucket_exposure(rows, today=today)
    near = out["0-14d"]
    assert near["net_gex_musd"] == 0.5
    assert near["put_call_oi_ratio"] == 3.0
    mid = out["15-45d"]
    # call_gex None falls back to call+put puts only; net_vex used directly
    assert mid["net_gex_musd"] == -1.0
    assert mid["net_vex_musd"] == 0.2
    far = out["46-90d"]
    assert far["oi_coverage_pct"] == 100 or far["oi_coverage_pct"] == 50
    top = out["top_oi_strikes_le45d"][0]
    assert top["strike"] == 90.0 and top["put_oi"] == 550  # 300 (0-14d) + 250 (15-45d)


def test_iv_from_capture_picks_atm_and_25d():
    snap = {
        "contracts": [
            {"expiry": "2026-09-18", "strike": 88.0, "type": "call", "iv": 0.70, "delta": 0.60},
            {"expiry": "2026-09-18", "strike": 90.0, "type": "call", "iv": 0.64, "delta": 0.25},
            {"expiry": "2026-09-18", "strike": 92.0, "type": "put", "iv": 0.66, "delta": -0.30},
            {"expiry": "2026-09-18", "strike": 90.0, "type": "put", "iv": 0.63, "delta": -0.50},
            {"expiry": "2026-10-16", "strike": 90.0, "type": "call", "iv": None},
        ]
    }
    out = tools._iv_from_capture(snap, spot=89.5)
    rows = {row["expiry"]: row for row in out["expirations"]}
    sep = rows["2026-09-18"]
    assert sep["atm_strike"] == 90.0          # nearest strike to spot
    assert sep["atm_iv"] == 0.64              # ATM comes from whichever side listed it
    assert sep["put_25d_iv"] == 0.66          # closest |delta+0.25| -> -0.30 put
    assert sep["call_25d_iv"] == 0.64         # delta 0.25 exactly
    assert sep["skew_25d"] == round(0.66 - 0.64, 6)
    # Expiries without any IV are dropped entirely.
    assert "2026-10-16" not in rows


def test_iv_from_capture_drops_degenerate_short_dated():
    import datetime as dt

    snap = {
        "contracts": [
            {"expiry": "2026-08-26", "strike": 90.0, "type": "call", "iv": 9.9, "delta": 0.5},   # today: junk
            {"expiry": "2026-08-27", "strike": 90.0, "type": "put", "iv": 8.1, "delta": -0.5},   # 1d: junk
            {"expiry": "2026-09-18", "strike": 90.0, "type": "call", "iv": 0.64, "delta": 0.25},
        ]
    }
    out = tools._iv_from_capture(snap, spot=89.5, today=dt.date(2026, 8, 26))
    expiries = {row["expiry"] for row in out["expirations"]}
    assert expiries == {"2026-09-18"}  # 0DTE and 1DTE filtered


def test_backtest_evidence_aggregates_and_survives_junk(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setattr(tools, "RUNTIME_DATA", tmp_path)
    wf = tmp_path / "eod_option_walkforward"
    wf.mkdir()
    (wf / "report.json").write_text(_json.dumps({
        "generated_at": "2026-07-27T20:45:00Z",
        "aggregate_results": [
            {"execution_model": "base", "policy": "permissive", "months": 4, "trades": 78,
             "win_rate_pct": 34.6, "total_pnl_dollars": -99.5, "profit_factor": 0.96},
            "junk-string-entry",
        ],
    }))
    br = tmp_path / "backtest_results"
    br.mkdir()
    (br / "backtest_SPY_20260724.json").write_text(_json.dumps({
        "ticker": "SPY", "total_snapshots": 8, "clusters_detected": 12,
        "predictions": {"not": "a list"},   # dict, not list - must not crash
    }))
    em = tmp_path / "earnings_gap_magnitude_walkforward"
    em.mkdir()
    (em / "report.md").write_text("# study | Verdict: **REJECTED** (tier 4) | Observations: 14742\n")

    out = tools.get_backtest_evidence()
    assert out.get("error") is None
    studies = {row["study"] for row in out["evidence"]}
    assert {"eod_option_walkforward", "cluster_scan_backtest"} <= studies
    verdict_rows = [row for row in out["evidence"] if row["study"] == "earnings_gap_magnitude_walkforward"]
    assert verdict_rows and "REJECTED" in verdict_rows[0]["verdict"]
    # ticker filter
    spy_only = tools.get_backtest_evidence(ticker="SPY")
    assert all(row.get("ticker") in (None, "SPY") for row in spy_only["evidence"])
    empty = tools.get_backtest_evidence(ticker="ZZZZ")
    # ZZZZ has no cluster backtest; other studies may still surface or error honestly
    assert isinstance(empty, dict)


def test_scan_strategies_consensus(monkeypatch):
    import types

    fake_read = {"direction": "BULLISH", "score": 70.0, "setup_kind": "cipher_model",
                 "supports": [48], "resistances": [50], "invalidation": 48}
    captured = []

    def fake_analyze(matrix_fn, ticker, feed, mode, strategy, cluster_exp=None, bars_fn=None):
        captured.append(strategy)
        return dict(fake_read)

    fake_app = types.SimpleNamespace(resolve_options_feed=lambda feed: feed, matrix=lambda *a, **k: {})
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "scanner", types.SimpleNamespace(analyze_ticker=fake_analyze))
    out = tools.scan_strategies("amkr")
    assert captured == ["cipher", "flash", "cluster"]
    assert out["consensus"].startswith("BULLISH (3/3)")
    assert len(out["results"]) == 3


def test_gamma_history_aggregates_strike_cells(tmp_path, monkeypatch):
    db_path = tmp_path / "gex_history.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "create table gex_snapshots (id integer primary key, ticker text, captured_at text, "
            "spot real, call_wall_strike real, put_wall_strike real, gamma_flip_level real, day_change_pct real)"
        )
        db.execute(
            "create table gex_strike_cells (snapshot_id integer, ticker text, captured_at text, expiration text, "
            "strike real, call_gex real, put_gex real, net_gex real, call_vex real, put_vex real, net_vex real, "
            "call_oi real, put_oi real, volume real, call_mid real, put_mid real, listed integer, available integer)"
        )
        now = datetime.now(timezone.utc)
        for i in range(3):
            ts = (now - timedelta(hours=i)).isoformat(timespec="seconds")
            cur = db.execute(
                "insert into gex_snapshots (ticker, captured_at, spot) values (?,?,?)",
                ("SPY", ts, 100.0 + i),
            )
            db.execute(
                "insert into gex_strike_cells (snapshot_id, ticker, captured_at, expiration, strike, net_gex) "
                "values (?,?,?,?,?,?)",
                (cur.lastrowid, "SPY", ts, "2026-09-18", 100.0, 1000.0 + i),
            )
    monkeypatch.setattr(tools, "GEX_DB", db_path)
    series = tools.get_gamma_history("spy", days=5)
    assert series["points"] == 3
    assert series["series"][0]["net_gex_total"] == pytest.approx(1002.0)
