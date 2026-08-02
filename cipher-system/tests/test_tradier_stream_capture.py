from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import tradier_stream_capture as tradier  # noqa: E402


def test_regular_session_guard_uses_new_york_cash_hours() -> None:
    assert tradier.regular_session_open(datetime(2026, 7, 27, 13, 30, tzinfo=timezone.utc))
    assert not tradier.regular_session_open(datetime(2026, 7, 27, 13, 29, tzinfo=timezone.utc))
    assert not tradier.regular_session_open(datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc))
    assert not tradier.regular_session_open(datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc))
    assert tradier.seconds_until_regular_close(datetime(2026, 7, 27, 19, 59, tzinfo=timezone.utc)) == 60
    assert tradier.seconds_until_regular_close(datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc)) == 0


def test_parse_occ_symbol_extracts_contract_metadata() -> None:
    parsed = tradier.parse_occ_symbol("SPY260731C00740000")
    assert parsed == {
        "underlying": "SPY",
        "expiration": "2026-07-31",
        "option_type": "call",
        "strike": 740.0,
    }
    assert tradier.parse_occ_symbol("SPY") is None


def test_eligible_expirations_respects_dte_window() -> None:
    selected = tradier.eligible_expirations(
        ["2026-07-27", "2026-07-31", "2026-08-21", "bad"],
        today=date(2026, 7, 27),
        min_dte=1,
        max_dte=14,
        count=2,
    )
    assert selected == ["2026-07-31"]


def test_select_chain_contracts_uses_nearest_strikes_and_both_sides() -> None:
    chain = []
    for strike in (95.0, 100.0, 105.0, 110.0):
        strike_code = int(strike * 1000)
        for cp, option_type in (("C", "call"), ("P", "put")):
            chain.append(
                {
                    "symbol": f"XYZ260731{cp}{strike_code:08d}",
                    "strike": strike,
                    "option_type": option_type,
                }
            )
    selected = tradier.select_chain_contracts(
        chain,
        spot=102.0,
        strikes_per_side=1,
        max_contracts=8,
    )
    assert {row["strike"] for row in selected} == {100.0, 105.0}
    assert {row["option_type"] for row in selected} == {"call", "put"}
    assert len(selected) == 4


def test_resolver_round_robins_contracts_across_underlyings(monkeypatch) -> None:
    monkeypatch.setattr(
        tradier,
        "fetch_underlying_spots",
        lambda _token, _env, _symbols: {"AAA": 100.0, "BBB": 200.0},
    )
    monkeypatch.setattr(
        tradier,
        "fetch_expirations",
        lambda _token, _env, _underlying: ["2026-07-31"],
    )

    def fake_chain(_token, _env, underlying, _expiration):
        spot = 100 if underlying == "AAA" else 200
        rows = []
        for strike in (spot - 5, spot, spot + 5):
            for cp, option_type in (("C", "call"), ("P", "put")):
                rows.append(
                    {
                        "symbol": f"{underlying}260731{cp}{int(strike * 1000):08d}",
                        "strike": strike,
                        "option_type": option_type,
                    }
                )
        return rows

    monkeypatch.setattr(tradier, "fetch_option_chain", fake_chain)
    selection = tradier.resolve_stream_universe(
        token="test",
        env="production",
        underlyings=["AAA", "BBB"],
        option_underlyings=["AAA", "BBB"],
        include_options=True,
        expiration_count=1,
        strikes_per_side=1,
        min_dte=0,
        max_dte=14,
        max_options_per_underlying=4,
        max_stream_symbols=6,
        today=date(2026, 7, 30),
    )
    assert selection["resolved_symbol_count"] == 6
    assert selection["option_contract_count"] == 4
    assert selection["underlyings"] == ["AAA", "BBB"]
    assert {tradier.parse_occ_symbol(symbol)["underlying"] for symbol in selection["option_symbols"]} == {"AAA", "BBB"}


def test_resolver_includes_option_underlyings_in_base_tape(monkeypatch) -> None:
    monkeypatch.setattr(
        tradier,
        "fetch_underlying_spots",
        lambda _token, _env, symbols: {symbol: 100.0 for symbol in symbols},
    )
    monkeypatch.setattr(tradier, "fetch_expirations", lambda *_args: [])
    selection = tradier.resolve_stream_universe(
        token="test",
        env="production",
        underlyings=["SPY"],
        option_underlyings=["SPY", "NVDA"],
        include_options=False,
        expiration_count=1,
        strikes_per_side=1,
        min_dte=0,
        max_dte=14,
        max_options_per_underlying=4,
        max_stream_symbols=10,
    )
    assert selection["underlyings"] == ["SPY", "NVDA"]
    assert selection["stream_symbols"] == ["SPY", "NVDA"]


def test_store_events_classifies_options_and_updates_run_progress(tmp_path: Path) -> None:
    db_path = tmp_path / "stream.sqlite"
    raw_path = tmp_path / "events.jsonl"
    tradier.ensure_schema(db_path)
    selection = {
        "option_contract_count": 1,
        "stream_symbols": ["SPY", "SPY260731C00740000"],
    }
    run_id = tradier.create_run(
        db_path,
        env="production",
        requested_underlyings=["SPY"],
        symbols=selection["stream_symbols"],
        filters=["quote", "trade"],
        selection=selection,
    )
    tradier.store_events(
        db_path,
        raw_path,
        run_id,
        [
            {"type": "quote", "symbol": "SPY260731C00740000", "bid": "1.20", "ask": "1.25"},
            {"type": "trade", "symbol": "SPY", "price": "739.10", "size": "10"},
            {"type": "heartbeat"},
        ],
    )

    with sqlite3.connect(db_path) as db:
        event_count, last_event_at = db.execute(
            "select event_count, last_event_at from tradier_stream_runs where id = ?",
            (run_id,),
        ).fetchone()
        option_row = db.execute(
            """
            select asset_class, underlying, option_expiration, option_type, strike
            from tradier_stream_events where symbol = 'SPY260731C00740000'
            """
        ).fetchone()
    assert event_count == 3
    assert last_event_at
    assert option_row == ("option", "SPY", "2026-07-31", "call", 740.0)
    assert tradier.finish_run(db_path, run_id, stop_reason="test") == 3
    assert len(raw_path.read_text(encoding="utf-8").splitlines()) == 3


def test_reconcile_run_counts_repairs_completed_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "stream.sqlite"
    raw_path = tmp_path / "events.jsonl"
    tradier.ensure_schema(db_path)
    selection = {"option_contract_count": 0, "stream_symbols": ["SPY"]}
    run_id = tradier.create_run(
        db_path,
        env="production",
        requested_underlyings=["SPY"],
        symbols=["SPY"],
        filters=["trade"],
        selection=selection,
    )
    tradier.store_events(
        db_path,
        raw_path,
        run_id,
        [{"type": "trade", "symbol": "SPY", "price": "739.10"}],
    )
    tradier.finish_run(db_path, run_id, stop_reason="test")
    with sqlite3.connect(db_path) as db:
        db.execute("update tradier_stream_runs set event_count = 0 where id = ?", (run_id,))
    assert tradier.reconcile_run_counts(db_path) == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "select event_count from tradier_stream_runs where id = ?", (run_id,)
        ).fetchone()[0] == 1


def test_reconcile_stale_run_uses_actual_stored_count(tmp_path: Path) -> None:
    db_path = tmp_path / "stream.sqlite"
    raw_path = tmp_path / "events.jsonl"
    tradier.ensure_schema(db_path)
    selection = {"option_contract_count": 0, "stream_symbols": ["SPY"]}
    run_id = tradier.create_run(
        db_path,
        env="production",
        requested_underlyings=["SPY"],
        symbols=["SPY"],
        filters=["trade"],
        selection=selection,
    )
    tradier.store_events(
        db_path,
        raw_path,
        run_id,
        [{"type": "trade", "symbol": "SPY", "price": "739.10"}],
    )
    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update tradier_stream_runs set started_at = ?, event_count = 0 where id = ?",
            (stale, run_id),
        )
    assert tradier.reconcile_stale_runs(db_path, older_than_seconds=60) == 1
    with sqlite3.connect(db_path) as db:
        completed_at, event_count, error, reason = db.execute(
            "select completed_at, event_count, error, stop_reason from tradier_stream_runs where id = ?",
            (run_id,),
        ).fetchone()
    assert completed_at
    assert event_count == 1
    assert "interrupted" in error
    assert reason == "reconciled_stale_run"
