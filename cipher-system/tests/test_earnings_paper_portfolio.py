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

joblib = pytest.importorskip("joblib", reason="earnings_model requires joblib")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from earnings_model import paper_portfolio as pp  # noqa: E402


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

    monkeypatch.setattr(
        "earnings_model.paper_portfolio.predict_stock_reaction",
        lambda symbol: {"error": "no model"},
    )

    class _Indexer:
        def __getitem__(self, _idx):
            return 210.0

    class _FakeClose:
        @property
        def iloc(self):
            return _Indexer()

    class FakeHistory:
        empty = False

        def __getitem__(self, key):
            assert key == "Close"
            return _FakeClose()

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, period="5d"):
            return FakeHistory()

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


def test_generate_setup_uses_live_date_and_falls_back(monkeypatch):
    monkeypatch.setattr(
        "earnings_model.paper_portfolio.predict_stock_reaction",
        lambda symbol: {"error": "no model"},
    )
    setup = pp.generate_optimal_paper_setup("NVDA", 210.0, "2026-08-25")
    assert setup["strategy_type"] == "Iron Condor"
    assert setup["entry_date"] == date.today().strftime("%Y-%m-%d")
    assert setup["expiry_date"] == "2026-08-28"  # Tue report -> that week's Fri
    assert len(setup["legs"]) == 4
    assert setup["total_cost"] > 0
