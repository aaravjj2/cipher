from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import tradier_flow  # noqa: E402
import tradier_stream_capture as capture  # noqa: E402


def _store(tmp_path: Path, db_path: Path, events: list[dict]) -> None:
    selection = {"option_contract_count": 2, "stream_symbols": ["SPY"]}
    run_id = capture.create_run(
        db_path, env="test", requested_underlyings=["SPY"],
        symbols=["SPY"], filters=["timesale"], selection=selection,
    )
    capture.store_events(db_path, tmp_path / f"{run_id}.jsonl", run_id, events)
    capture.finish_run(db_path, run_id, stop_reason="test")


def test_flow_uses_only_latest_session_and_truthful_event_clock(tmp_path: Path) -> None:
    db_path = tmp_path / "stream.sqlite"
    capture.ensure_schema(db_path)
    _store(tmp_path, db_path, [
        {"type": "timesale", "symbol": "SPY260821C00650000", "bid": 2.0, "ask": 2.1, "price": 2.1, "size": 300, "date": "2026-08-12T15:00:00Z"},
        {"type": "timesale", "symbol": "SPY260821C00650000", "bid": 2.0, "ask": 2.1, "price": 2.1, "size": 300, "date": "2026-08-13T14:31:00Z"},
        {"type": "timesale", "symbol": "SPY260821P00640000", "bid": 1.9, "ask": 2.0, "price": 1.95, "size": 400, "date": "2026-08-13T14:32:00Z"},
    ])

    result = tradier_flow.flow("SPY", spot=645.0, min_premium=1, db_path=db_path)
    assert result is not None
    assert result["session_date"] == "2026-08-13"
    assert result["as_of"] == "2026-08-13T14:32:00Z"
    assert {row["session_date"] for row in result["prints"]} == {"2026-08-13"}
    assert {row["side"] for row in result["prints"]} == {"buy", "unknown"}
    assert result["source"] == "tradier_stream"
    assert result["capture_mode"] == "event_timesales"


def test_flow_accepts_ui_singular_filters_and_event_time_side(tmp_path: Path) -> None:
    db_path = tmp_path / "stream.sqlite"
    capture.ensure_schema(db_path)
    _store(tmp_path, db_path, [
        {"type": "timesale", "symbol": "SPY260821C00650000", "bid": 2.0, "ask": 2.1, "price": 2.1, "size": 300, "date": "2026-08-13T14:31:00Z"},
        {"type": "timesale", "symbol": "SPY260821P00640000", "bid": 1.9, "ask": 2.0, "price": 1.9, "size": 400, "date": "2026-08-13T14:32:00Z"},
    ])
    calls = tradier_flow.flow(
        "SPY", spot=645.0, min_premium=1,
        option_type="call", side="buy", db_path=db_path,
    )
    puts = tradier_flow.flow(
        "SPY", spot=645.0, min_premium=1,
        option_type="put", side="sell", db_path=db_path,
    )
    assert calls and [row["type"] for row in calls["prints"]] == ["call"]
    assert puts and [row["type"] for row in puts["prints"]] == ["put"]


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "stream.sqlite"
    capture.ensure_schema(db_path)
    _store(tmp_path, db_path, [
        {"type": "timesale", "symbol": "SPY260821C00650000", "bid": 2.0, "ask": 2.1, "price": 2.1, "size": 2, "date": "2026-08-13T14:31:00Z"},
    ])
    with sqlite3.connect(db_path) as db:
        db.execute("delete from tradier_option_timesales")
        db.execute(
            "update tradier_stream_events set captured_at = '2026-08-13T14:31:00+00:00'"
        )
    assert tradier_flow.backfill_session("2026-08-13", db_path=db_path) == 1
    assert tradier_flow.backfill_session("2026-08-13", db_path=db_path) == 0
