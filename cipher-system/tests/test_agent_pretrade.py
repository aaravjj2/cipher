"""Tests for the agent decision log and the deterministic pre-trade gate."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agent_log = _load("agent_decision_log")
gate = _load("pretrade_gate")


# ------------------------------------------------------------------ decision log

def _intent(decision_id: str) -> dict:
    return {"decision_id": decision_id, "ticker": "NVDA", "side": "buy",
            "contract_symbol": "NVDA260918C00180000", "quantity": 1,
            "limit_price": 2.5, "rationale": "test"}


def test_intent_is_idempotent_per_decision_id(tmp_path) -> None:
    log = tmp_path / "log.jsonl"
    agent_log.append(log, "INTENT", _intent("d1"))
    try:
        agent_log.append(log, "INTENT", _intent("d1"))
        raise AssertionError("duplicate INTENT accepted")
    except ValueError:
        pass


def test_unknown_event_and_missing_fields_are_refused(tmp_path) -> None:
    log = tmp_path / "log.jsonl"
    for event, payload in (("NOT_AN_EVENT", {"x": 1}), ("FILLED", {"decision_id": "d"})):
        try:
            agent_log.append(log, event, payload)
            raise AssertionError(f"accepted {event}")
        except ValueError:
            pass
    assert not log.exists()


def test_rows_carry_paper_only_truth_and_tail_counts_malformed(tmp_path) -> None:
    log = tmp_path / "log.jsonl"
    agent_log.append(log, "INTENT", _intent("d2"))
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
    rows = agent_log.tail_rows(log)
    assert rows[0]["paper_only"] is True
    assert rows[0]["live_execution_capability"] is False
    assert rows[-1].get("malformed_trailing_lines") == 1


# ------------------------------------------------------------------ pre-trade gate

def _seed_ledger(db_path: Path, held: list[str], opened_today: int) -> None:
    # Dates are relative to "now" because the gate compares against the live
    # UTC date; hardcoded strings would break at every midnight rollover.
    from datetime import datetime, timezone as _tz
    today = datetime.now(_tz.utc).isoformat(timespec="seconds")
    hour_ago = datetime.now(_tz.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            create table paper_positions(id text primary key, episode_id text,
              ticker text, direction text, symbol text, quantity integer,
              entry_price real, opened_at text, closed_at text, exit_price real,
              exit_reason text, status text, payload_json text);
            """
        )
        for i, ticker in enumerate(held):
            conn.execute(
                "insert into paper_positions values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"p{i}", None, ticker, "bullish", "SYM", 1, 1.0,
                 hour_ago, None, None, None, "OPEN", "{}"),
            )
        for j in range(opened_today - len(held)):
            conn.execute(
                "insert into paper_positions values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"q{j}", None, "MU", "bullish", "MU_SYM", 1, 1.0,
                 hour_ago, today, 1.1,
                 "option_take_profit", "CLOSED", "{}"),
            )


def test_gate_blocks_on_kill_switch(tmp_path, monkeypatch) -> None:
    kill = tmp_path / "STOP_PAPER_EXECUTOR"
    kill.write_text("x")
    monkeypatch.setattr(gate, "KILL_SWITCH", kill)
    verdict = gate.evaluate(decision_id="k1", ticker="NVDA")
    assert verdict["verdict"] == "BLOCKED"
    assert verdict["reason"] == "SKIPPED_KILL_SWITCH"


def test_gate_blocks_after_hours_and_duplicates(tmp_path, monkeypatch) -> None:
    log = tmp_path / "log.jsonl"
    monkeypatch.setattr(gate, "DECISION_LOG", log)
    monkeypatch.setattr(gate, "_session_is_open",
                        lambda: (False, "after close (16:01 ET)"))
    verdict = gate.evaluate(decision_id="h1", ticker="NVDA")
    assert verdict["verdict"] == "BLOCKED"
    assert "BLOCKED_MARKET_CLOSED" in verdict["reason"]

    monkeypatch.setattr(gate, "_session_is_open", lambda: (True, "forced"))
    assert gate.evaluate(decision_id="h2", ticker="NVDA")["verdict"] == "PASS"
    agent_log.append(log, "INTENT", _intent("h2"))
    again = gate.evaluate(decision_id="h2", ticker="NVDA")
    assert again["reason"] == "BLOCKED_DUPLICATE_INTENT"


def test_gate_enforces_portfolio_limits(tmp_path, monkeypatch) -> None:
    db = tmp_path / "ledger.sqlite"
    _seed_ledger(db, held=["NVDA"], opened_today=6)
    monkeypatch.setattr(gate, "LEDGER", db)
    monkeypatch.setattr(gate, "_session_is_open", lambda: (True, "forced"))
    monkeypatch.setattr(agent_log, "has_event", lambda *a, **k: False)

    held = gate.evaluate(decision_id="p1", ticker="NVDA")
    assert held["reason"] == "SKIPPED_POSITION_EXISTS"

    fresh = gate.evaluate(decision_id="p2", ticker="AAPL")
    # 6 positions opened today (>= budget of 5): the daily limit blocks before
    # the position-count limit matters.
    assert fresh["verdict"] == "BLOCKED"
    assert fresh["reason"] == "SKIPPED_DAILY_LIMIT"


def test_clean_pass_when_everything_is_clear(tmp_path, monkeypatch) -> None:
    db = tmp_path / "ledger.sqlite"
    _seed_ledger(db, held=[], opened_today=0)
    monkeypatch.setattr(gate, "LEDGER", db)
    monkeypatch.setattr(gate, "DECISION_LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(gate, "_session_is_open", lambda: (True, "regular session"))
    monkeypatch.setattr(gate, "_portfolio_state", lambda ticker: {
        "open_count": 0, "ticker_already_held": False, "opened_today": 0})
    verdict = gate.evaluate(decision_id="ok1", ticker="SPY")
    assert verdict["verdict"] == "PASS" and verdict["reason"] is None
    assert all(check["ok"] for check in verdict["checks"])


# ------------------------------------------------------------------ chain integrity

def test_chain_tracks_lifecycle_and_flags_anomalies(tmp_path) -> None:
    log = tmp_path / "log.jsonl"
    agent_log.append(log, "INTENT", _intent("d9"))
    pending = agent_log.chain(log, "d9")
    assert pending["known"] is True and pending["complete"] is False
    assert pending["anomalies"] == ["no terminal event yet"]

    agent_log.append(log, "SUBMITTED", {"decision_id": "d9", "broker_order_id": "b1"})
    dangling = agent_log.chain(log, "d9")
    assert "no terminal event yet" in dangling["anomalies"]

    agent_log.append(log, "FILLED", {"decision_id": "d9", "filled_price": 3.12,
                                     "filled_quantity": 1})
    filled = agent_log.chain(log, "d9")
    assert filled["anomalies"] == ["filled but not reconciled yet"]
    agent_log.append(log, "RECONCILED", {"decision_id": "d9", "matches_local_ledger": True})
    done = agent_log.chain(log, "d9")
    assert done["complete"] is True and done["anomalies"] == []


def test_chain_reports_unknown_decision_and_duplicates(tmp_path) -> None:
    log = tmp_path / "log.jsonl"
    assert agent_log.chain(log, "ghost")["known"] is False
    agent_log.append(log, "INTENT", _intent("dup"))
    agent_log.append(log, "BLOCKED", {"decision_id": "dup", "reason": "gate"})
    agent_log.append(log, "FILLED", {"decision_id": "dup", "filled_price": 1.0,
                                     "filled_quantity": 1})
    weird = agent_log.chain(log, "dup")
    assert any("multiple terminal" in a for a in weird["anomalies"])


def test_gate_blocks_contracts_over_the_cost_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gate, "DECISION_LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(gate, "_session_is_open", lambda: (True, "forced"))
    monkeypatch.setattr(gate, "_portfolio_state", lambda ticker: {
        "open_count": 0, "ticker_already_held": False, "opened_today": 0})

    pricey = gate.evaluate(decision_id="c1", ticker="SPY",
                           limit_price=8.0, quantity=1)
    assert pricey["reason"] == "SKIPPED_MAX_COST"
    assert [c for c in pricey["checks"] if c["check"] == "contract_cost"][0]["ok"] is False

    cheap = gate.evaluate(decision_id="c2", ticker="SPY",
                          limit_price=5.0, quantity=1)
    assert cheap["verdict"] == "PASS"


# ------------------------------------------------------------------ session report

def _session_report_module():
    spec = importlib.util.spec_from_file_location(
        "agent_session_report", SCRIPTS / "agent_session_report.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_session_report"] = module
    spec.loader.exec_module(module)
    return module


def test_session_report_counts_today_and_flags_anomalies(tmp_path) -> None:
    from datetime import datetime, timezone
    sr = _session_report_module()
    log = tmp_path / "log.jsonl"
    today = datetime.now(timezone.utc).date().isoformat()

    def row(event, decision_id, **extra):
        base = {"ts": f"{today}T12:00:00+00:00", "event": event,
                "decision_id": decision_id, **extra}
        return json.dumps(base)

    with open(log, "w", encoding="utf-8") as handle:
        handle.write(row("INTENT", "a", ticker="SPY") + "\n")
        handle.write(row("SUBMITTED", "a", broker_order_id="b1") + "\n")
        handle.write(row("FILLED", "a", filled_price=3.0, filled_quantity=1) + "\n")
        handle.write(row("INTENT", "b", ticker="MU") + "\n")
        handle.write(row("BLOCKED", "b", reason="SKIPPED_KILL_SWITCH") + "\n")
        # stale row from yesterday must not count in today's report
        handle.write(json.dumps({
            "ts": "2020-01-01T00:00:00+00:00", "event": "INTENT",
            "decision_id": "old", "ticker": "X"}) + "\n")

    report = sr.build_report(log, today=today)
    assert report["decisions"] == 2
    assert report["outcomes"] == {"FILLED": 1, "BLOCKED": 1}
    assert report["blocked_reasons"] == ["SKIPPED_KILL_SWITCH"]
    # 'a' is FILLED but never reconciled -> anomaly the operator must see
    assert any(c["decision_id"] == "a" and
               any("not reconciled" in a_ for a_ in c["anomalies"])
               for c in report["chain_anomalies"])
    text = sr.render(report)
    assert "session report" in text and "SKIPPED_KILL_SWITCH" in text


def test_session_report_on_empty_log_says_so(tmp_path) -> None:
    sr = _session_report_module()
    report = sr.build_report(tmp_path / "none.jsonl")
    assert report["decisions"] == 0
    assert "No decisions" in sr.render(report)
