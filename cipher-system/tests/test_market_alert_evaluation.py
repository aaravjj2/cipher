"""Server-side evaluation of stored market alert rules.

core/alerts.py stored rules and evaluated them "in the authenticated browser", so an alert
fired only while someone was already looking at the screen. These tests pin the two properties
that make the server-side evaluator worth trusting: it fires on the crossing rather than every
run, and it never fires from stale data.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "core", ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import alerts as alert_store  # noqa: E402
import evaluate_market_alerts as evaluator  # noqa: E402

NOW = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)


def _quote(price=780.0, change=1.5, age_seconds=5):
    return {
        "ticker": "SPY", "price_context": price, "day_change_pct": change,
        "as_of": (NOW - timedelta(seconds=age_seconds)).isoformat(),
    }


def _rule(kind="price_above", threshold=775.0):
    return {"id": "r1", "ticker": "SPY", "kind": kind, "threshold": threshold, "enabled": True}


@pytest.mark.parametrize("kind,threshold,price,change,expected", [
    ("price_above", 775.0, 780.0, 1.5, "triggered"),
    ("price_above", 785.0, 780.0, 1.5, "clear"),
    ("price_below", 785.0, 780.0, 1.5, "triggered"),
    ("price_below", 775.0, 780.0, 1.5, "clear"),
    ("day_change_above", 1.0, 780.0, 1.5, "triggered"),
    ("day_change_below", -1.0, 780.0, 1.5, "clear"),
    ("day_change_below", -1.0, 780.0, -2.0, "triggered"),
])
def test_each_kind_reads_the_right_field(kind, threshold, price, change, expected):
    outcome = evaluator.evaluate_rule(
        _rule(kind, threshold), _quote(price, change), now=NOW
    )
    assert outcome["status"] == expected


def test_a_stale_quote_is_unknown_rather_than_clear():
    """Firing "SPY crossed 780" off yesterday's close is worse than not firing at all."""
    outcome = evaluator.evaluate_rule(_rule(), _quote(age_seconds=4000), now=NOW)
    assert outcome["status"] == "unknown"
    assert "old" in outcome["reason"]


@pytest.mark.parametrize("quote,fragment", [
    ({"error": "URLError: refused"}, "URLError"),
    ({"price_context": 780.0}, "as_of"),
    ({"price_context": 780.0, "as_of": "not-a-date"}, "as_of"),
    ({"as_of": NOW.isoformat()}, "numeric"),
    ({"price_context": None, "as_of": NOW.isoformat()}, "numeric"),
])
def test_every_unjudgeable_input_is_unknown(quote, fragment):
    outcome = evaluator.evaluate_rule(_rule(), quote, now=NOW)
    assert outcome["status"] == "unknown"
    assert fragment in outcome["reason"]


def test_an_unsupported_kind_is_unknown_not_silently_clear():
    outcome = evaluator.evaluate_rule(_rule(kind="gex_flip"), _quote(), now=NOW)
    assert outcome["status"] == "unknown"


def _harness(tmp_path, monkeypatch, quote):
    db = tmp_path / "alerts.sqlite"
    state = tmp_path / "state.json"
    monkeypatch.setattr(evaluator, "fetch_quote", lambda ticker, **k: quote)
    sent: list[str] = []
    monkeypatch.setattr(evaluator, "send_hermes_message", lambda msg, **k: sent.append(msg) or 0)
    return db, state, sent


def test_it_notifies_on_the_crossing_and_not_on_every_run(tmp_path, monkeypatch):
    quote = _quote(price=780.0)
    db, state, sent = _harness(tmp_path, monkeypatch, quote)
    alert_store.add_rule(ticker="SPY", kind="price_above", threshold=775.0, db_path=db)

    first = evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    assert first["crossings"] and first["notified"] is True
    assert len(sent) == 1
    assert "780" in sent[0] and "775" in sent[0]
    # Read-only framing is part of the contract, not decoration.
    assert "not advice" in sent[0].lower()

    second = evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    assert second["crossings"] == [], "still-above must not re-notify"
    assert len(sent) == 1


def test_a_rule_rearms_after_returning_to_clear(tmp_path, monkeypatch):
    quote = _quote(price=780.0)
    db, state, sent = _harness(tmp_path, monkeypatch, quote)
    alert_store.add_rule(ticker="SPY", kind="price_above", threshold=775.0, db_path=db)

    evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    quote["price_context"] = 770.0          # back below
    evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    quote["price_context"] = 781.0          # genuine second crossing
    third = evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)

    assert third["crossings"], "a real second crossing must notify again"
    assert len(sent) == 2


def test_a_stale_quote_does_not_rearm_a_triggered_rule(tmp_path, monkeypatch):
    """Treating unknown as clear would re-arm the rule and fake a crossing next tick."""
    quote = _quote(price=780.0)
    db, state, sent = _harness(tmp_path, monkeypatch, quote)
    alert_store.add_rule(ticker="SPY", kind="price_above", threshold=775.0, db_path=db)

    evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    assert len(sent) == 1
    stored = json.loads(state.read_text())
    quote["as_of"] = (NOW - timedelta(hours=20)).isoformat()   # goes stale
    middle = evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    assert middle["unknown"] == 1
    assert json.loads(state.read_text()) == stored, "state must survive an unknown untouched"

    quote["as_of"] = NOW.isoformat()        # fresh again, still above
    evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    assert len(sent) == 1, "a stale gap must not manufacture a second crossing"


def test_dry_run_neither_sends_nor_persists(tmp_path, monkeypatch):
    db, state, sent = _harness(tmp_path, monkeypatch, _quote(price=780.0))
    alert_store.add_rule(ticker="SPY", kind="price_above", threshold=775.0, db_path=db)
    result = evaluator.run(state_path=state, target="telegram", dry_run=True, db_path=db, now=NOW)
    assert result["crossings"] and result["notified"] is False
    assert sent == []
    assert not state.exists()


def test_disabled_rules_and_deleted_rules_are_dropped(tmp_path, monkeypatch):
    db, state, sent = _harness(tmp_path, monkeypatch, _quote(price=780.0))
    rule = alert_store.add_rule(ticker="SPY", kind="price_above", threshold=775.0, db_path=db)
    evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    assert rule["id"] in json.loads(state.read_text())

    alert_store.delete_rule(rule["id"], db_path=db)
    result = evaluator.run(state_path=state, target="telegram", dry_run=False, db_path=db, now=NOW)
    assert result["checked"] == 0
    assert json.loads(state.read_text()) == {}, "state for deleted rules must be pruned"
