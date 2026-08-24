"""Tests for the earnings paper portfolio's dynamic, idempotent entry path.

These protect the P0-4 fix: `paper-enter` must derive its schedule from the
live radar (never a hardcoded week), must be safe to run on a daily timer
(repeated runs never stack duplicate positions and never delete open ones),
and must fall back to a neutral setup when the reaction model is unavailable.

They also guard the fail-closed entry-pricing gate: without fresh two-sided
core quotes for BOTH legs an entry is refused (QUOTES_UNAVAILABLE) unless
allow_estimated_debit=True is passed explicitly, in which case the estimate is
labeled ESTIMATED_DEBIT. No test touches the network or the local core.
"""
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pandas as pd

joblib = pytest.importorskip("joblib", reason="earnings_model requires joblib")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from earnings_model import paper_portfolio as pp  # noqa: E402
from earnings_model import cli  # noqa: E402


def _stamp(offset_seconds=0.0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _leg_quotes(long_bid=2.90, long_ask=3.10, short_bid=0.95, short_ask=1.05,
                stamp_offset_seconds=0.0):
    return [
        {"symbol": "TEST260828C00210000", "bid": long_bid, "ask": long_ask,
         "quote_time": _stamp(stamp_offset_seconds)},
        {"symbol": "TEST260828C00217500", "bid": short_bid, "ask": short_ask,
         "quote_time": _stamp(stamp_offset_seconds)},
    ]


def _always_fresh_quotes(_symbol, _legs):
    return _leg_quotes()


def test_next_friday_rolls_from_report_date():
    # Friday reports roll to the following Friday; Wednesday reports land on
    # the same week's Friday.
    assert pp._next_friday("2026-08-21") == "2026-08-28"  # Friday -> next week
    assert pp._next_friday("2026-08-19") == "2026-08-21"  # Wed -> same week Fri


def test_upcoming_week_schedule_dedupes_and_sorts(monkeypatch):
    cards = [
        {"symbol": "aapl", "scheduled_date": "2026-08-25"},
        {"symbol": "AAPL", "scheduled_date": "2026-08-25"},  # dup, same case only
        {"symbol": "MSFT", "scheduled_date": "2026-08-19"},
        {"symbol": "", "scheduled_date": "2026-08-20"},  # no symbol -> dropped
        {"symbol": "NVDA", "scheduled_date": None},  # no date -> dropped
    ]
    # upcoming_week_schedule imports find_upcoming_earnings locally from the
    # scanner module, so the patch must target that module.
    monkeypatch.setattr(
        "earnings_model.scanner.find_upcoming_earnings",
        lambda days_ahead=14, tiers=None, symbols=None, conn=None: cards,
    )
    schedule = pp.upcoming_week_schedule()
    assert schedule == [("MSFT", "2026-08-19"), ("AAPL", "2026-08-25")]


def test_enter_this_week_is_idempotent(monkeypatch, tmp_path):
    """Repeated scheduled runs must never stack duplicate positions."""
    db_path = str(tmp_path / "paper.sqlite")
    schedule = [("AAPL", "2026-08-25"), ("MSFT", "2026-08-19")]

    prediction = {
        "strategy_eligible": True, "direction": "BULLISH", "expected_gap_pct": 3.0,
        "model_version": "test-v2", "validation_status": "ELIGIBLE_FOR_PROSPECTIVE_PAPER",
    }
    monkeypatch.setattr("earnings_model.paper_portfolio.predict_for_symbol", lambda *_args, **_kwargs: prediction)

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, **_kwargs):
            return pd.DataFrame({"Close": [190.0 + index for index in range(22)]})

    monkeypatch.setattr("earnings_model.paper_portfolio.yf.Ticker", FakeTicker)

    first = pp.enter_this_week_paper_book(
        target_risk_per_trade=500.0, schedule=schedule, db_path=db_path,
        quote_fetcher=_always_fresh_quotes,
    )
    assert len(first) == 2
    assert all(order["debit_source"] == pp.DEBIT_SOURCE_CAPTURED for order in first)
    assert all(order["entry_quotes"]["computed_debit"] == pytest.approx(2.15) for order in first)

    second = pp.enter_this_week_paper_book(
        target_risk_per_trade=500.0, schedule=schedule, db_path=db_path,
        quote_fetcher=_always_fresh_quotes,
    )
    assert second == []  # both already entered

    conn = pp.init_paper_db(db_path)
    rows = conn.execute(
        "SELECT symbol, status, debit_source, entry_quotes_json FROM paper_positions"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    assert all(r["status"] == "OPEN" for r in rows)
    assert all(r["debit_source"] == pp.DEBIT_SOURCE_CAPTURED for r in rows)
    assert all(json.loads(r["entry_quotes_json"])["legs"][0]["quote_time"] for r in rows)


def test_enter_refuses_entries_without_fresh_core_quotes(monkeypatch, tmp_path):
    """Fail-closed default: no fresh quotes means NO new position is recorded."""
    db_path = str(tmp_path / "paper.sqlite")
    schedule = [("AAPL", "2026-08-25")]

    prediction = {
        "strategy_eligible": True, "direction": "BULLISH", "expected_gap_pct": 3.0,
        "model_version": "test-v2", "validation_status": "ELIGIBLE_FOR_PROSPECTIVE_PAPER",
    }
    monkeypatch.setattr("earnings_model.paper_portfolio.predict_for_symbol", lambda *_args, **_kwargs: prediction)

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, **_kwargs):
            return pd.DataFrame({"Close": [190.0 + index for index in range(22)]})

    monkeypatch.setattr("earnings_model.paper_portfolio.yf.Ticker", FakeTicker)

    placed = pp.enter_this_week_paper_book(
        target_risk_per_trade=500.0, schedule=schedule, db_path=db_path,
        quote_fetcher=lambda _symbol, _legs: None,  # core unreachable
    )
    assert placed == []

    conn = pp.init_paper_db(db_path)
    rows = conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
    conn.close()
    assert rows == 0


def test_generate_setup_requires_validated_direction():
    blocked = pp.generate_optimal_paper_setup(
        "NVDA", 210.0, "2026-08-25",
        prediction={"strategy_eligible": False, "model_version": "v2", "validation_status": "FAILED"},
    )
    assert blocked["skip_reason"] == "FAILED"


def test_generate_setup_captures_real_debit_from_fresh_leg_quotes():
    setup = pp.generate_optimal_paper_setup(
        "NVDA", 210.0, "2026-08-25",
        prediction={
            "strategy_eligible": True, "direction": "BULLISH", "expected_gap_pct": 3.0,
            "model_version": "v2", "validation_status": "ELIGIBLE_FOR_PROSPECTIVE_PAPER",
        },
        quote_fetcher=lambda _symbol, _legs: _leg_quotes(),
    )
    assert setup["strategy_type"] == "Debit Bull Call Spread"
    assert setup["entry_date"] == date.today().strftime("%Y-%m-%d")
    assert setup["expiry_date"] == "2026-08-28"  # Tue report -> that week's Fri
    assert len(setup["legs"]) == 2
    # Actual debit = long ask (3.10) - short bid (0.95), NOT the width heuristic.
    assert setup["unit_debit"] == 2.15
    assert setup["debit_source"] == pp.DEBIT_SOURCE_CAPTURED
    assert setup["total_cost"] == round(setup["contracts"] * 2.15 * 100, 2)
    # Spot 210 -> $5 wings: long 210 / short 215 calls.
    width = setup["legs"][1]["strike"] - setup["legs"][0]["strike"]
    assert (setup["legs"][0]["strike"], setup["legs"][1]["strike"], width) == (210.0, 215.0, 5.0)
    assert setup["max_gain"] == round(setup["contracts"] * (width - 2.15) * 100, 2)
    assert setup["model_version"] == "v2"
    snapshot = setup["entry_quotes"]
    assert snapshot["computed_debit"] == 2.15
    assert snapshot["legs"][0]["action"] == "BUY" and snapshot["legs"][1]["action"] == "SELL"
    assert snapshot["legs"][0]["quote_time"]
    assert "captured from core quotes" in setup["notes"]


def test_round_strike_uses_ten_dollar_increment_above_500():
    assert pp.round_strike(986.42) == 990.0


_VALID_PREDICTION = {
    "strategy_eligible": True, "direction": "BULLISH", "expected_gap_pct": 3.0,
    "model_version": "v2", "validation_status": "ELIGIBLE_FOR_PROSPECTIVE_PAPER",
}


def test_generate_setup_refuses_entry_when_quotes_unavailable():
    refused = pp.generate_optimal_paper_setup(
        "NVDA", 210.0, "2026-08-25", prediction=dict(_VALID_PREDICTION),
        quote_fetcher=lambda _symbol, _legs: None,
    )
    assert refused["skip_reason"] == pp.SKIP_QUOTES_UNAVAILABLE
    assert refused["model_version"] == "v2"
    assert "unit_debit" not in refused


def test_generate_setup_refuses_entry_on_stale_leg_quote():
    # One leg fresh, one stamped 5 minutes ago: the pair is unusable.
    def half_stale(_symbol, _legs):
        quotes = _leg_quotes()
        quotes[1]["quote_time"] = _stamp(-300)
        return quotes

    refused = pp.generate_optimal_paper_setup(
        "NVDA", 210.0, "2026-08-25", prediction=dict(_VALID_PREDICTION),
        quote_fetcher=half_stale,
    )
    assert refused["skip_reason"] == pp.SKIP_QUOTES_UNAVAILABLE


def test_generate_setup_refuses_crossed_capture_instead_of_pricing_garbage():
    # Short bid above long ask would produce a non-positive/nonsense debit.
    refused = pp.generate_optimal_paper_setup(
        "NVDA", 210.0, "2026-08-25", prediction=dict(_VALID_PREDICTION),
        quote_fetcher=lambda _symbol, _legs: _leg_quotes(long_ask=0.80, short_bid=1.40),
    )
    assert refused["skip_reason"] == pp.SKIP_QUOTES_UNAVAILABLE


def test_estimated_debit_requires_explicit_opt_in_and_is_labeled():
    setup = pp.generate_optimal_paper_setup(
        "NVDA", 210.0, "2026-08-25", prediction=dict(_VALID_PREDICTION),
        allow_estimated_debit=True,
        quote_fetcher=lambda _symbol, _legs: None,
    )
    width = setup["legs"][1]["strike"] - setup["legs"][0]["strike"]
    assert setup["debit_source"] == pp.DEBIT_SOURCE_ESTIMATED
    assert setup["unit_debit"] == round(width * 0.40, 2)
    assert "ESTIMATED_DEBIT" in setup["notes"]
    assert "entry_quotes" not in setup


def test_freshness_gate_rejects_missing_and_old_timestamps():
    fresh = {"bid": 1.0, "ask": 1.2, "quote_time": _stamp(-10)}
    assert pp._fresh_leg_quote(fresh)
    assert not pp._fresh_leg_quote({"bid": 1.0, "ask": 1.2})  # no timestamp at all
    assert not pp._fresh_leg_quote({**fresh, "quote_time": _stamp(-121)})
    assert not pp._fresh_leg_quote({**fresh, "ask": 0.9})     # crossed
    assert not pp._fresh_leg_quote({**fresh, "bid": -1.0})
    assert not pp._fresh_leg_quote(None)
    assert pp.captured_spread_debit(None, width=5.0)[0] is None
    assert pp.captured_spread_debit([fresh], width=5.0)[0] is None


def test_freeze_legacy_cohort_marks_only_open_unversioned_rows(tmp_path):
    db_path = str(tmp_path / "paper.sqlite")
    conn = pp.init_paper_db(db_path)
    base = {
        "strategy_type": "Debit Bull Call Spread",
        "report_date": "2026-08-20",
        "entry_date": "2026-08-17",
        "expiry_date": "2026-08-21",
        "spot_at_entry": 100.0,
        "legs": [
            {"action": "BUY", "type": "CALL", "strike": 100.0, "expiry": "2026-08-21"},
            {"action": "SELL", "type": "CALL", "strike": 105.0, "expiry": "2026-08-21"},
        ],
        "contracts": 1,
        "unit_debit": 2.0,
        "total_cost": 200.0,
        "max_gain": 300.0,
        "max_loss": 200.0,
        "notes": "test",
    }
    pp.execute_paper_order(conn, {**base, "symbol": "LEG_OPEN"})
    pp.execute_paper_order(conn, {**base, "symbol": "LEG_SETTLED", "report_date": "2026-08-13"})
    pp.execute_paper_order(conn, {
        **base, "symbol": "NEW_GATED",
        "model_version": "v2", "validation_status": "ELIGIBLE_FOR_PROSPECTIVE_PAPER",
    })
    conn.execute("UPDATE paper_positions SET status='SETTLED', settle_spot=110.0, "
                 "realized_pnl=300.0 WHERE symbol='LEG_SETTLED'")
    conn.commit()

    result = pp.freeze_legacy_paper_cohort(conn=conn)
    assert result["marked"] == 1 and result["symbols"] == ["LEG_OPEN"]

    rows = {row["symbol"]: row["validation_status"] for row in
            conn.execute("SELECT symbol, validation_status FROM paper_positions")}
    assert rows["LEG_OPEN"] == pp.LEGACY_FROZEN_VALIDATION_STATUS   # open legacy -> frozen
    assert rows["LEG_SETTLED"] is None                              # settled history untouched
    assert rows["NEW_GATED"] == "ELIGIBLE_FOR_PROSPECTIVE_PAPER"    # gated cohort untouched

    scorecard = pp.get_paper_scorecard(conn)
    assert scorecard["legacy_frozen_open"] == 1
    by_cohort = {(c["model_version"], c["validation_status"]): c for c in scorecard["cohorts"]}
    assert ("legacy-unversioned", pp.LEGACY_FROZEN_VALIDATION_STATUS) in by_cohort
    assert ("legacy-unversioned", "LEGACY_ESTIMATED_ENTRY") in by_cohort  # settled legacy stays distinct
    assert ("v2", "ELIGIBLE_FOR_PROSPECTIVE_PAPER") in by_cohort
    conn.close()


def test_init_paper_db_migrates_existing_schema_without_new_columns(tmp_path):
    """Old books (pre debit_source/entry_quotes_json) upgrade in place."""
    db_path = str(tmp_path / "paper.sqlite")
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.execute("""
        CREATE TABLE paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            report_date TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            expiry_date TEXT NOT NULL,
            spot_at_entry REAL NOT NULL,
            legs_json TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            unit_debit REAL NOT NULL,
            total_cost REAL NOT NULL,
            max_gain REAL NOT NULL,
            max_loss REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            settle_spot REAL,
            realized_pnl REAL,
            realized_pnl_pct REAL,
            notes TEXT,
            created_at TEXT NOT NULL,
            settled_at TEXT
        );
    """)
    legacy_conn.execute(
        "INSERT INTO paper_positions (symbol, strategy_type, report_date, entry_date, "
        "expiry_date, spot_at_entry, legs_json, contracts, unit_debit, total_cost, "
        "max_gain, max_loss, status, notes, created_at) VALUES "
        "('OLD', 'Debit Bull Call Spread', '2026-08-20', '2026-08-17', '2026-08-21', "
        "100.0, '[]', 1, 2.0, 200.0, 300.0, 200.0, 'OPEN', 'legacy', '2026-08-17T00:00:00Z')"
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = pp.init_paper_db(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_positions)")}
    assert {"model_version", "validation_status", "debit_source", "entry_quotes_json"} <= columns
    row = conn.execute("SELECT symbol, debit_source FROM paper_positions").fetchone()
    assert row["symbol"] == "OLD" and row["debit_source"] is None
    conn.close()


def test_settle_expired_debit_spread_and_leave_unpriced_open(tmp_path):
    db_path = str(tmp_path / "paper.sqlite")
    conn = pp.init_paper_db(db_path)
    base = {
        "strategy_type": "Debit Bull Call Spread",
        "report_date": "2026-08-20",
        "entry_date": "2026-08-17",
        "expiry_date": "2026-08-21",
        "spot_at_entry": 100.0,
        "legs": [
            {"action": "BUY", "type": "CALL", "strike": 100.0, "expiry": "2026-08-21"},
            {"action": "SELL", "type": "CALL", "strike": 105.0, "expiry": "2026-08-21"},
        ],
        "contracts": 1,
        "unit_debit": 2.0,
        "total_cost": 200.0,
        "max_gain": 300.0,
        "max_loss": 200.0,
        "notes": "test",
    }
    pp.execute_paper_order(conn, {**base, "symbol": "WIN"})
    pp.execute_paper_order(conn, {**base, "symbol": "MISSING"})
    conn.close()

    def close(symbol, _expiry):
        if symbol == "MISSING":
            raise ValueError("no close")
        return 110.0

    result = pp.settle_expired_positions(
        as_of=date(2026, 8, 22), db_path=db_path, close_loader=close
    )
    assert result["eligible"] == 2
    assert result["settled"] == [{
        "id": 1, "symbol": "WIN", "settle_spot": 110.0,
        "realized_pnl": 300.0, "realized_pnl_pct": 150.0,
    }]
    assert result["errors"][0]["symbol"] == "MISSING"
    conn = pp.init_paper_db(db_path)
    assert conn.execute("SELECT status FROM paper_positions WHERE symbol='WIN'").fetchone()[0] == "SETTLED"
    assert conn.execute("SELECT status FROM paper_positions WHERE symbol='MISSING'").fetchone()[0] == "OPEN"
    scorecard = pp.get_paper_scorecard(conn)
    assert {key: scorecard[key] for key in (
        "total", "open", "settled", "wins", "realized_pnl", "win_rate_pct"
    )} == {
        "total": 2, "open": 1, "settled": 1, "wins": 1,
        "realized_pnl": 300.0, "win_rate_pct": 100.0,
    }
    assert scorecard["cohorts"] == [{
        "model_version": "legacy-unversioned",
        "validation_status": "LEGACY_ESTIMATED_ENTRY",
        "total": 2, "open": 1, "settled": 1, "wins": 1,
        "realized_pnl": 300.0,
    }]
    conn.close()


def test_paper_enter_cli_prints_complete_active_book(monkeypatch, capsys):
    active = [{"id": 7}]
    monkeypatch.setattr(cli, "enter_this_week_paper_book", lambda **_kwargs: [])
    monkeypatch.setattr(cli, "get_active_paper_positions", lambda: active)
    monkeypatch.setattr(cli, "render_paper_book_table", lambda rows: f"ACTIVE={rows[0]['id']}")
    monkeypatch.setattr(sys, "argv", ["earnings_model", "paper-enter"])
    assert cli.main() is None
    assert "ACTIVE=7" in capsys.readouterr().out
