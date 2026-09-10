import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core.paper_executor.cohort_evaluation import evaluate_cohort, replay_recorded_quotes
from core.paper_executor.config import ContractConfig, SimulationConfig
from core.exchange_calendar import is_session


def ledger(tmp_path, trades):
    path = tmp_path / "ledger.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("create table paper_positions(id text, ticker text, quantity integer, entry_price real, exit_price real, opened_at text, closed_at text, status text, payload_json text)")
        db.execute("create table paper_marks(position_id text, marked_at text, payload_json text)")
        db.execute("create table contract_mark_tape(position_id text)")
        for i, (pnl, evidence) in enumerate(trades):
            opened = datetime(2026, 8, 3, 15, tzinfo=timezone.utc)
            remaining = i // 3
            while remaining:
                opened += timedelta(days=1)
                remaining -= int(is_session(opened.date()))
            if evidence:
                evidence = {**evidence, "decision_at": opened.isoformat()}
            db.execute("insert into paper_positions values(?,?,?,?,?,?,?,?,?)", (str(i), "SPY", 1, 2, 2 + pnl / 100, opened.isoformat(), (opened + timedelta(minutes=5)).isoformat(), "CLOSED", json.dumps({"instrument_model": "debit_spread", "entry_evidence": evidence})))
    return path


def ev(version="v2"):
    return {"cohort_id": "confirmation", "version": version, "config_hash": "frozen", "registry_strategy_id": "candidate", "execution_assumptions": {"slippage_pct": .5}}


def test_legacy_never_counts_toward_prospective_and_net_spreads_not_doubled(tmp_path):
    path = ledger(tmp_path, [(100, {})] * 60 + [(-20, ev()), (40, ev())])
    report = evaluate_cohort(path)
    assert report["overall"]["pnl_usd"] == 6020
    assert report["per_version"]["legacy/unknown"]["trades"] == 60
    new = report["per_version"]["confirmation/v2/frozen"]
    assert new["trades"] == 2
    assert new["profit_factor"] == 2
    assert "fewer_than_60_closed_trades" in new["promotion_blockers"]
    assert "registry_requirements_not_verified" in new["promotion_blockers"]
    assert report["evidence_coverage"]["positions_with_quote_tape"] == 0
    json.dumps(report, allow_nan=False)


def test_version_changes_split_evidence_and_missing_prices_stay_unknown(tmp_path):
    path = ledger(tmp_path, [(20, ev()), (30, ev("v3"))])
    with sqlite3.connect(path) as db:
        db.execute("update paper_positions set exit_price=null where id='1'")
    report = evaluate_cohort(path)
    assert len(report["per_version"]) == 2
    assert report["overall"]["invalid_closed_rows"] == 1
    assert report["overall"]["pnl_usd"] == 20
    assert report["overall"]["average_loss_usd"] is None


def test_holdout_requirements_and_bootstrap_are_fail_closed(tmp_path, monkeypatch):
    path = ledger(tmp_path, [(20, ev())] * 60)
    monkeypatch.setattr("core.paper_executor.cohort_evaluation.eligible_strategies", lambda: {"candidate"})
    result = evaluate_cohort(path)["per_version"]["confirmation/v2/frozen"]
    assert result["active_sessions"] == 20
    assert result["bootstrap_expectancy_lower_usd"] == 20
    assert result["promotion_blockers"] == ["entry_liquidation_evidence_missing", "liquidation_drawdown_incomplete", "monitoring_quote_coverage_incomplete", "operational_reconciliation_required"]
    assert result["promotion_eligible"] is False


def test_liquidation_drawdown_captures_open_loss(tmp_path):
    path = ledger(tmp_path, [(20, ev())])
    with sqlite3.connect(path) as db:
        db.execute("insert into paper_marks values(?,?,?)", ("0", "2026-08-03T15:01:00+00:00", json.dumps({"liquidation_value": 100})))
    report = evaluate_cohort(path)
    assert report["overall"]["observed_liquidation_drawdown_usd"] == 100
    assert report["overall"]["drawdown_complete"] is False


def test_replay_uses_real_fill_functions_and_reports_missing_leg():
    quote = {"symbol": "TEST", "bid": 2, "ask": 2.1, "timestamp": "2026-09-09T15:00:00+00:00"}
    observation = {"side": "entry", "decision_at": quote["timestamp"], "long_quote": quote}
    result = replay_recorded_quotes([observation, {**observation, "instrument_model": "debit_spread"}, {**observation, "decision_at": "2026-09-09T15:10:00+00:00"}], simulation=SimulationConfig(), contract=ContractConfig())
    assert result["fills"][0]["fill_price"] == pytest.approx(2.1105)
    assert len(result["gaps"]) == 2
    assert result["coverage_fraction"] == pytest.approx(1 / 3)
    assert result["synthetic_prices"] is False


def test_forged_late_provenance_does_not_count(tmp_path):
    path = ledger(tmp_path, [(20, ev())])
    with sqlite3.connect(path) as db:
        payload = json.loads(db.execute("select payload_json from paper_positions").fetchone()[0])
        payload["entry_evidence"]["decision_at"] = "2027-01-01T00:00:00+00:00"
        db.execute("update paper_positions set payload_json=?", (json.dumps(payload),))
    assert list(evaluate_cohort(path)["per_version"]) == ["legacy/unknown"]


def test_holiday_does_not_count_as_prospective_session(tmp_path):
    path = ledger(tmp_path, [(20, ev())])
    with sqlite3.connect(path) as db:
        db.execute("update paper_positions set opened_at='2026-09-07T15:00:00+00:00', closed_at='2026-09-07T15:05:00+00:00'")
    result = evaluate_cohort(path)["per_version"]["confirmation/v2/frozen"]
    assert result["active_sessions"] == 0
    assert "non_session_or_invalid_entry_timestamp" in result["promotion_blockers"]
