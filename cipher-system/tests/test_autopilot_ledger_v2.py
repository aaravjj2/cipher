from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import sqlite3
from threading import Barrier

import pytest

from core.paper_executor.database import PaperExecutorDatabase, _session_bounds


def enter(db, name, **changes):
    args = dict(position_id=name, episode_id=name, ticker=name, direction="bullish",
                symbol=name, quantity=1, entry_price=1.0, status="OPEN",
                payload={"opened_at": "2026-09-09T14:00:00+00:00"},
                max_open_positions=10, max_positions_per_ticker=10,
                max_new_positions_per_day=10, max_new_positions_per_ticker_per_day=10,
                stop_after_daily_losses=10)
    args.update(changes)
    return db.create_position_transactional(**args)


def order(name):
    return dict(order_id=name, episode_id=name, position_id=name, side="buy",
                symbol=name, quantity=1, status="SIMULATED_FILLED", fill={"price": 1},
                created_at="2026-09-09T14:00:00+00:00")


@pytest.mark.parametrize("limit", ["max_open_positions", "max_new_positions_per_day", "starting_cash"])
def test_concurrent_entry_rechecks_limits(tmp_path, limit):
    db = PaperExecutorDatabase(tmp_path / "ledger.db")
    barrier = Barrier(6)
    def attempt(i):
        barrier.wait()
        return enter(db, str(i), **{limit: 100 if limit == "starting_cash" else 1})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))
    assert sum(ok for ok, _ in results) == 1
    assert len(db.open_positions()) == 1


def test_cash_releases_only_real_exit_proceeds(tmp_path):
    db = PaperExecutorDatabase(tmp_path / "ledger.db")
    assert enter(db, "a", starting_cash=100)[0]
    assert enter(db, "b", starting_cash=100)[1] == "SKIPPED_INSUFFICIENT_CASH"
    assert db.close_position("a", -0.2, "adverse_spread_fill", {"closed_at": "2026-09-09T15:00:00Z"})
    assert enter(db, "c", starting_cash=100)[1] == "SKIPPED_INSUFFICIENT_CASH"
    assert db.portfolio_snapshot(100)["cash_balance"] == -20


def test_orders_and_positions_rollback_together(tmp_path):
    db = PaperExecutorDatabase(tmp_path / "ledger.db")
    with pytest.raises(ValueError):
        enter(db, "a", entry_order=order("other"))
    assert db.rows("paper_positions") == []
    assert enter(db, "a", entry_order=order("a"))[0]
    with pytest.raises(sqlite3.IntegrityError):
        db.close_position("a", 0.5, "underlying_invalidation", {}, exit_order=order("a"))
    assert db.open_positions()[0]["id"] == "a"
    assert db.rows("paper_events") == []


def test_concurrent_close_is_idempotent_after_restart(tmp_path):
    path = tmp_path / "ledger.db"
    db = PaperExecutorDatabase(path)
    assert enter(db, "a", entry_order=order("a"))[0]
    exit_order = {**order("a"), "order_id": "exit-a", "side": "sell"}
    barrier = Barrier(5)
    def close(_):
        barrier.wait()
        return db.close_position("a", 0.8, "underlying_invalidation",
                                 {"closed_at": "2026-09-09T15:00:00Z"}, exit_order=exit_order)
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(close, range(5))) == 1
    restarted = PaperExecutorDatabase(path)
    assert restarted.session_counts("2026-09-09T16:00:00Z")["stopped_trades"] == 1
    assert len(restarted.rows("paper_orders")) == 2
    assert len(restarted.rows("paper_events")) == 1
    assert enter(restarted, "b", stop_after_daily_losses=1)[1] == "SKIPPED_DAILY_STOP_LIMIT"


def test_new_york_session_and_dst(tmp_path):
    db = PaperExecutorDatabase(tmp_path / "ledger.db")
    assert enter(db, "a", payload={"opened_at": "2026-09-10T01:00:00Z"})[0]
    assert db.session_counts("2026-09-09T20:00:00-04:00", "a")["ticker_entries"] == 1
    assert db.session_counts("2026-09-10T05:00:00Z")["new_positions"] == 0
    assert db.session_snapshot("2026-09-09")["positions_opened"] == 1
    assert _session_bounds("2026-03-08T12:00:00Z")[1:] == ("2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00")
    assert _session_bounds("2026-11-01T12:00:00Z")[1:] == ("2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00")


def test_structured_payload_and_stale_cached_counts(tmp_path):
    @dataclass
    class Fill:
        price: float
    db = PaperExecutorDatabase(tmp_path / "ledger.db")
    assert enter(db, "a", payload={"opened_at": "2026-09-09T14:00:00Z", "fill": Fill(1.0)})[0]
    assert json.loads(db.rows("paper_positions")[0]["payload_json"])["fill"] == {"price": 1.0}
    with db.connect() as conn:
        conn.execute("delete from daily_account_state")
    assert enter(db, "b", max_new_positions_per_day=1)[1] == "SKIPPED_DAILY_LIMIT"


@pytest.mark.parametrize("changes", [{"quantity": 0}, {"quantity": 1.5}, {"entry_price": float("nan")}, {"entry_price": -1}])
def test_invalid_economics_rejected(tmp_path, changes):
    db = PaperExecutorDatabase(tmp_path / "ledger.db")
    with pytest.raises(ValueError):
        enter(db, "a", **changes)
