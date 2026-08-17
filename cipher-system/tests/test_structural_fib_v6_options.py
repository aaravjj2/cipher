from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from core import structural_fib_v6_options as options


def _trade(**changes):
    row = {
        "symbol": "NVDA", "day": "2026-08-03", "setup_id": "C05",
        "direction": "long", "entry_time": "09:55", "exit_time": "10:00",
        "entry_price": 100.0, "exit_reason": "target",
    }
    row.update(changes)
    return row


def _historical_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        create table decision_selections (
          decision_date text, symbol text, expiration_date text, strike real,
          option_type text, spot real, dte integer, moneyness real, rank integer
        );
        create table selection_observation_audit (
          decision_date text, symbol text, observed_on_decision integer
        );
        create table option_bars (
          symbol text, timestamp text, open real, high real, low real, close real,
          volume real, source text
        );
        insert into decision_selections values
          ('2026-08-03','NVDA260807C00100000','2026-08-07',100,'call',100,4,1,1);
        insert into selection_observation_audit values
          ('2026-08-03','NVDA260807C00100000',1);
        insert into option_bars values
          ('NVDA260807C00100000','2026-08-03T13:55:00.000000Z',2,2.2,1.9,2.1,10,'test'),
          ('NVDA260807C00100000','2026-08-03T14:04:00.000000Z',3,3.2,2.8,3.0,10,'test');
        """
    )
    db.commit()
    db.close()


def test_historical_protocol_keeps_proxy_and_adverse_marks_separate(tmp_path: Path):
    path = tmp_path / "options.sqlite"
    _historical_db(path)
    report = options.historical_trade_bar_test(
        [_trade()], {("NVDA", "call"): path}
    )
    assert report["mapped_trades"] == 1
    assert report["protocols"]["close_proxy"]["wins"] == 1
    close_row = report["trade_records"]["close_proxy"][0]
    adverse_row = report["trade_records"]["adverse_bar"][0]
    assert close_row["entry_option_price"] == pytest.approx(2.1)
    assert close_row["exit_option_price"] == pytest.approx(3.0)
    assert adverse_row["entry_option_price"] == pytest.approx(2.2)
    assert adverse_row["exit_option_price"] == pytest.approx(2.8)


def test_tradier_protocol_pays_ask_and_receives_bid(tmp_path: Path):
    path = tmp_path / "stream.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        """
        create table tradier_stream_runs (
          id integer, started_at text, completed_at text, last_event_at text,
          selection_json text
        );
        create table tradier_stream_events (
          symbol text, captured_at text, event_type text, bid real, ask real
        );
        """
    )
    selection = {"selection_details": [{
        "underlying": "NVDA", "contracts": [{
          "symbol": "NVDA260807C00100000", "strike": 100,
          "option_type": "call", "expiration": "2026-08-07"
        }]
    }]}
    db.execute(
        "insert into tradier_stream_runs values (1,?,?,?,?)",
        ("2026-08-03T13:30:00+00:00", "2026-08-03T20:00:00+00:00",
         "2026-08-03T20:00:00+00:00", json.dumps(selection)),
    )
    db.executemany(
        "insert into tradier_stream_events values (?,?,?,?,?)",
        [
          ("NVDA260807C00100000", "2026-08-03T13:55:01+00:00", "quote", 1.9, 2.0),
          # Target touch is only known within 10:00-10:05; implementation exits at 10:05.
          ("NVDA260807C00100000", "2026-08-03T14:05:01+00:00", "quote", 3.0, 3.1),
        ],
    )
    db.commit()
    db.close()
    report = options.tradier_nbbo_test([_trade()], path)
    row = report["trade_records"][0]
    assert row["entry_option_price"] == pytest.approx(2.0)
    assert row["exit_option_price"] == pytest.approx(3.0)
    assert row["option_return_pct"] == pytest.approx(50.0)


def test_missing_quotes_are_skipped_not_imputed(tmp_path: Path):
    path = tmp_path / "stream.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        """
        create table tradier_stream_runs (
          id integer, started_at text, completed_at text, last_event_at text,
          selection_json text
        );
        create table tradier_stream_events (
          symbol text, captured_at text, event_type text, bid real, ask real
        );
        """
    )
    db.commit()
    db.close()
    report = options.tradier_nbbo_test([_trade()], path)
    assert report["mapped_trades"] == 0
    assert report["skips"] == {"no_stream_run_at_entry": 1}
