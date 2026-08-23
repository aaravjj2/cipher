"""Tests for the earnings paper portfolio's dynamic, idempotent entry path.

These protect the P0-4 fix: `paper-enter` must derive its schedule from the
live radar (never a hardcoded week), must be safe to run on a daily timer
(repeated runs never stack duplicate positions and never delete open ones),
and must fall back to a neutral setup when the reaction model is unavailable.
"""
import sys
from datetime import date
from pathlib import Path

import pytest
import pandas as pd

joblib = pytest.importorskip("joblib", reason="earnings_model requires joblib")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from earnings_model import paper_portfolio as pp  # noqa: E402
from earnings_model import cli  # noqa: E402


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
        target_risk_per_trade=500.0, schedule=schedule, db_path=db_path
    )
    assert len(first) == 2

    second = pp.enter_this_week_paper_book(
        target_risk_per_trade=500.0, schedule=schedule, db_path=db_path
    )
    assert second == []  # both already entered

    conn = pp.init_paper_db(db_path)
    rows = conn.execute("SELECT symbol, status FROM paper_positions").fetchall()
    conn.close()
    assert len(rows) == 2
    assert all(r["status"] == "OPEN" for r in rows)


def test_generate_setup_uses_live_date_and_requires_validated_direction():
    blocked = pp.generate_optimal_paper_setup(
        "NVDA", 210.0, "2026-08-25",
        prediction={"strategy_eligible": False, "model_version": "v2", "validation_status": "FAILED"},
    )
    assert blocked["skip_reason"] == "FAILED"
    setup = pp.generate_optimal_paper_setup(
        "NVDA", 210.0, "2026-08-25",
        prediction={
            "strategy_eligible": True, "direction": "BULLISH", "expected_gap_pct": 3.0,
            "model_version": "v2", "validation_status": "ELIGIBLE_FOR_PROSPECTIVE_PAPER",
        },
    )
    assert setup["strategy_type"] == "Debit Bull Call Spread"
    assert setup["entry_date"] == date.today().strftime("%Y-%m-%d")
    assert setup["expiry_date"] == "2026-08-28"  # Tue report -> that week's Fri
    assert len(setup["legs"]) == 2
    assert setup["total_cost"] > 0
    assert setup["model_version"] == "v2"


def test_round_strike_uses_ten_dollar_increment_above_500():
    assert pp.round_strike(986.42) == 990.0


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
