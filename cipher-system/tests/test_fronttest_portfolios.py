from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from core import fronttest_portfolios as ft
from core.structural_fib_bars import Bar

NY = ZoneInfo("America/New_York")


class FakeMarket:
    def __init__(self):
        self.bid = 1.90
        self.ask = 2.00
        self.missing = False

    def stock(self, symbol):
        return 100.0

    def chain(self, spec, today):
        return [{
            "symbol": "MU260807C00100000", "type": "call", "expiration": "2026-08-07",
            "strike": 100.0, "bid": self.bid, "ask": self.ask,
            "open_interest": 500, "volume": 100,
        }]

    def quotes(self, symbols):
        if self.missing:
            return {}
        return {symbol: {"bid": self.bid, "ask": self.ask, "timestamp": "2026-08-03T14:00:00Z"}
                for symbol in symbols}


class SpreadMarket(FakeMarket):
    def chain(self, spec, today):
        return [
            {"symbol": "MU260807C00100000", "type": "call", "expiration": "2026-08-07",
             "strike": 100.0, "bid": 29.0, "ask": 30.0, "open_interest": 500, "volume": 100},
            {"symbol": "MU260807C00105000", "type": "call", "expiration": "2026-08-07",
             "strike": 105.0, "bid": 25.8, "ask": 26.2, "open_interest": 500, "volume": 100},
        ]

    def quotes(self, symbols):
        values = {
            "MU260807C00100000": {"bid": 29.0, "ask": 30.0, "timestamp": "2026-08-03T14:00:00Z"},
            "MU260807C00105000": {"bid": 25.8, "ask": 26.2, "timestamp": "2026-08-03T14:00:00Z"},
        }
        return {symbol: values[symbol] for symbol in symbols if symbol in values}


def test_seven_portfolios_are_isolated_and_initialized(tmp_path: Path):
    db = ft.connect(tmp_path / "fronttest.sqlite")
    try:
        rows = ft.portfolio_status(db)
        assert len(rows) == 7
        assert len({row["portfolio_id"] for row in rows}) == 7
        assert all(row["realized_equity"] == 100_000 for row in rows)
    finally:
        db.close()


def test_shadow_fill_crosses_spread_and_fixed_horizon_closes(monkeypatch, tmp_path: Path):
    signal = {
        "portfolio_id": "mu_pm_liquidity", "symbol": "MU", "day": "2026-08-03",
        "setup_id": "bull_break", "direction": "long",
        "signal_at": "2026-08-03T10:00:00-04:00", "max_hold_minutes": 15,
    }
    monkeypatch.setattr(ft, "detect_signals", lambda bars: [signal])
    market = FakeMarket()
    bars = {"MU": [Bar(datetime(2026, 8, 3, 10, 0, tzinfo=NY), 100, 101, 99, 100, 1)],
            "NVDA": [], "QQQ": []}
    path = tmp_path / "fronttest.sqlite"
    first = ft.run_pass(bars, db_path=path, market=market,
                        now=datetime(2026, 8, 3, 10, 1, tzinfo=NY))
    assert first["opened"] == 1 and first["paper_only"] is True
    db = ft.connect(path)
    try:
        row = db.execute("select * from positions").fetchone()
        assert row["entry_fill"] > row["entry_ask"]
    finally:
        db.close()

    monkeypatch.setattr(ft, "detect_signals", lambda bars: [])
    market.bid, market.ask = 2.40, 2.50
    second = ft.run_pass(bars, db_path=path, market=market,
                         now=datetime(2026, 8, 3, 10, 17, tzinfo=NY))
    assert second["closed"] == 1
    db = ft.connect(path)
    try:
        row = db.execute("select * from positions").fetchone()
        assert row["status"] == "CLOSED"
        assert row["exit_reason"] == "fixed_15m"
        assert row["exit_fill"] < row["exit_bid"]
        assert row["pnl"] > 0
    finally:
        db.close()


def test_position_table_allows_only_one_open_position_per_portfolio(tmp_path: Path):
    db = ft.connect(tmp_path / "fronttest.sqlite")
    try:
        indexes = {row[1] for row in db.execute("pragma index_list(positions)")}
        assert "ux_fronttest_one_open" in indexes
    finally:
        db.close()


def test_late_signal_is_audited_but_not_opened(monkeypatch, tmp_path: Path):
    signal = {
        "portfolio_id": "mu_pm_liquidity", "symbol": "MU", "setup_id": "bull_break",
        "direction": "long", "signal_at": "2026-08-03T11:40:00-04:00", "max_hold_minutes": 15,
    }
    monkeypatch.setattr(ft, "detect_signals", lambda _bars: [signal])
    path = tmp_path / "fronttest.sqlite"
    result = ft.run_pass({"MU": [], "NVDA": [], "QQQ": []}, db_path=path, market=FakeMarket(),
                         now=datetime(2026, 8, 3, 11, 41, tzinfo=NY))
    assert result["opened"] == 0
    db = ft.connect(path)
    try:
        row = db.execute("select disposition,skip_reason from signals").fetchone()
        assert tuple(row) == ("SKIPPED", "ENTRY_WINDOW_CLOSED")
    finally:
        db.close()


def test_two_realized_losses_lock_the_portfolio(monkeypatch, tmp_path: Path):
    path = tmp_path / "fronttest.sqlite"
    db = ft.connect(path)
    for index in range(2):
        signal_id = f"loss-{index}"
        db.execute(
            """insert into signals(signal_id,portfolio_id,symbol,setup_id,direction,signal_at,
                                    detected_at,payload_json,disposition)
                 values (?,?,?,?,?,?,?,?,?)""",
            (signal_id, "qqq_early", "QQQ", "early_bull", "long",
             f"2026-08-03T09:4{index}:00-04:00", f"2026-08-03T09:4{index}:01-04:00", "{}", "OPENED"),
        )
        db.execute(
            """insert into positions(position_id,portfolio_id,signal_id,status,contract,option_type,
                 expiration,strike,quantity,allocated_capital,entry_at,entry_bid,entry_ask,entry_fill,
                 underlying_entry,exit_at,exit_fill,exit_reason,pnl,return_pct)
                 values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"p-{index}", "qqq_early", signal_id, "CLOSED", f"QQQ{index}", "call", "2026-08-07",
             100, 1, 200, f"2026-08-03T09:4{index}:01-04:00", 1.9, 2.0, 2.02, 100,
             f"2026-08-03T09:5{index}:00-04:00", 1.0, "pivot_invalidation", -100, -50),
        )
    db.commit(); db.close()
    signal = {"portfolio_id": "qqq_early", "symbol": "QQQ", "setup_id": "early_bear",
              "direction": "short", "signal_at": "2026-08-03T10:10:00-04:00", "target": 99, "stop": 101}
    monkeypatch.setattr(ft, "detect_signals", lambda _bars: [signal])
    result = ft.run_pass({"QQQ": [], "NVDA": [], "MU": []}, db_path=path, market=FakeMarket(),
                         now=datetime(2026, 8, 3, 10, 11, tzinfo=NY))
    assert result["opened"] == 0
    db = ft.connect(path)
    try:
        row = db.execute("select skip_reason from signals where setup_id='early_bear'").fetchone()
        assert row[0] == "DAILY_LOSS_LOCKOUT"
    finally:
        db.close()


def test_expensive_long_option_falls_back_to_defined_risk_vertical(monkeypatch, tmp_path: Path):
    signal = {"portfolio_id": "mu_pm_liquidity", "symbol": "MU", "setup_id": "bull_break",
              "direction": "long", "signal_at": "2026-08-03T10:00:00-04:00", "max_hold_minutes": 15}
    monkeypatch.setattr(ft, "detect_signals", lambda _bars: [signal])
    path = tmp_path / "fronttest.sqlite"
    result = ft.run_pass({"MU": [], "NVDA": [], "QQQ": []}, db_path=path, market=SpreadMarket(),
                         now=datetime(2026, 8, 3, 10, 1, tzinfo=NY))
    assert result["opened"] == 1
    db = ft.connect(path)
    try:
        row = db.execute("select * from positions").fetchone()
        assert row["structure"] == "debit_spread"
        assert row["short_contract"] == "MU260807C00105000"
        assert 0 < row["allocated_capital"] <= 2_000
        assert row["entry_fill"] < row["entry_ask"]
    finally:
        db.close()


def test_alpaca_expiry_field_is_accepted_by_contract_selector():
    spec = next(s for s in ft.SPECS if s.portfolio_id == "qqq_validated")
    row = {
        "symbol": "QQQ260807C00100000", "type": "call", "expiry": "2026-08-07",
        "strike": 100, "bid": 1.9, "ask": 2.0, "open_interest": 500, "volume": 100,
    }
    selected = ft._select_contract(spec, [row], 100, datetime(2026, 8, 3).date(), "call")
    assert selected == row


def test_forced_close_uses_labeled_last_mark_if_feed_is_missing(monkeypatch, tmp_path: Path):
    signal = {
        "portfolio_id": "qqq_early", "symbol": "QQQ", "day": "2026-08-03",
        "setup_id": "early_bull", "direction": "long",
        "signal_at": "2026-08-03T11:20:00-04:00", "target": 200.0, "stop": 50.0,
    }
    monkeypatch.setattr(ft, "detect_signals", lambda bars: [signal])
    market = FakeMarket()
    bars = {"QQQ": [Bar(datetime(2026, 8, 3, 11, 20, tzinfo=NY), 100, 101, 99, 100, 1)],
            "NVDA": [], "MU": []}
    path = tmp_path / "fronttest.sqlite"
    ft.run_pass(bars, db_path=path, market=market,
                now=datetime(2026, 8, 3, 11, 21, tzinfo=NY))
    monkeypatch.setattr(ft, "detect_signals", lambda bars: [])
    market.missing = True
    result = ft.run_pass(bars, db_path=path, market=market,
                         now=datetime(2026, 8, 3, 15, 46, tzinfo=NY))
    assert result["closed"] == 1
    db = ft.connect(path)
    try:
        row = db.execute("select * from positions").fetchone()
        assert row["exit_reason"] == "forced_close_stale_mark"
    finally:
        db.close()


def test_counterfactual_ledger_scores_skipped_target_without_option_pnl(tmp_path: Path):
    path = tmp_path / "fronttest.sqlite"
    db = ft.connect(path)
    payload = {
        "portfolio_id": "qqq_early", "symbol": "QQQ", "setup_id": "early_bear",
        "direction": "short", "signal_at": "2026-08-03T10:00:00-04:00",
        "signal_price": 100.0, "target": 98.0, "stop": 101.0,
    }
    db.execute(
        """insert into signals(signal_id,portfolio_id,symbol,setup_id,direction,signal_at,
                               detected_at,payload_json,disposition,skip_reason)
           values ('skipped-target','qqq_early','QQQ','early_bear','short',?,?,?,?,?)""",
        (payload["signal_at"], payload["signal_at"], json.dumps(payload), "SKIPPED", "DAILY_LIMIT"),
    )
    bars = {"QQQ": [
        Bar(datetime(2026, 8, 3, 10, 1, tzinfo=NY), 100, 100.5, 99.0, 99.5, 1),
        Bar(datetime(2026, 8, 3, 10, 2, tzinfo=NY), 99.5, 99.7, 97.9, 98.2, 1),
    ]}
    result = ft.update_signal_outcomes(
        db, bars, now_et=datetime(2026, 8, 3, 10, 3, tzinfo=NY),
    )
    row = db.execute("select * from signal_outcomes where signal_id='skipped-target'").fetchone()
    assert result == {"created": 1, "updated": 0, "resolved": 1}
    assert row["status"] == "RESOLVED" and row["outcome"] == "TARGET"
    assert row["mfe_pct"] > 2 and row["mae_pct"] == 0.5
    assert "underlying_path_only" in row["methodology"]
    db.close()


def test_counterfactual_ledger_uses_conservative_same_bar_ordering(tmp_path: Path):
    db = ft.connect(tmp_path / "fronttest.sqlite")
    payload = {
        "portfolio_id": "qqq_early", "symbol": "QQQ", "setup_id": "early_bull",
        "direction": "long", "signal_at": "2026-08-03T10:00:00-04:00",
        "signal_price": 100.0, "target": 101.0, "stop": 99.0,
    }
    db.execute(
        """insert into signals(signal_id,portfolio_id,symbol,setup_id,direction,signal_at,
                               detected_at,payload_json,disposition,skip_reason)
           values ('same-bar','qqq_early','QQQ','early_bull','long',?,?,?,?,?)""",
        (payload["signal_at"], payload["signal_at"], json.dumps(payload), "SKIPPED", "OPEN_POSITION"),
    )
    bars = {"QQQ": [Bar(datetime(2026, 8, 3, 10, 1, tzinfo=NY), 100, 101.5, 98.5, 100, 1)]}
    ft.update_signal_outcomes(db, bars, now_et=datetime(2026, 8, 3, 10, 2, tzinfo=NY))
    row = db.execute("select outcome from signal_outcomes where signal_id='same-bar'").fetchone()
    assert row["outcome"] == "INVALIDATED"
    db.close()
