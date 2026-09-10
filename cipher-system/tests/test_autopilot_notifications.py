from datetime import datetime, timedelta, timezone

from core import autopilot_notifications
from core import portfolio_daily_report
from core.paper_executor.database import PaperExecutorDatabase
from earnings_model.paper_portfolio import init_paper_db
import json
import pytest


def pending_position(db, identity, since):
    with db.connect() as conn:
        conn.execute("insert into paper_positions(id,episode_id,ticker,direction,symbol,quantity,entry_price,opened_at,status,payload_json) values(?,?,?,?,?,?,?,?,?,?)",
                     (identity, None, 'SPY', 'BULLISH', 'SPY260911C00600000', 1, 2,
                      since, 'OPEN', json.dumps({'pending_exit': {'reason': 'TIME', 'since': since, 'error': 'stale_quote'}})))


def test_pending_exits_survive_busy_logs_age_and_acknowledge_individually(tmp_path):
    path = tmp_path/'paper.sqlite'
    db = PaperExecutorDatabase(path)
    now = datetime.now(timezone.utc)
    since = (now-timedelta(hours=2)).isoformat()
    pending_position(db, 'one', since)
    pending_position(db, 'two', since)
    for _ in range(60):
        db.insert_system_event('ENTRY_BLOCKED', {'reason': 'SKIPPED_NO_CONTRACT'})
    db.insert_system_event('AUTO_PAPER_PROMOTION', {'ok': True})
    sent = []
    state = tmp_path/'notice.json'
    for identity in ('one', 'two'):
        result = autopilot_notifications.deliver_latest_failure(sent.append, db_path=path, state_path=state, now=now)
        assert result == {'status': 'delivered', 'event_id': f'exit:{identity}:{since}'}
    assert autopilot_notifications.deliver_latest_failure(sent.append, db_path=path, state_path=state, now=now)['status'] == 'already_delivered'
    assert len(sent) == 2 and all('Event: exit:' in m for m in sent)
    with db.connect() as conn:
        conn.execute("update paper_positions set status='CLOSED'")
    assert autopilot_notifications.latest_failure(path) is None


def test_failed_pending_delivery_is_retried_without_acknowledging(tmp_path):
    path = tmp_path/'paper.sqlite'
    db = PaperExecutorDatabase(path)
    now = datetime.now(timezone.utc)
    pending_position(db, 'one', (now-timedelta(days=1)).isoformat())
    state = tmp_path/'notice.json'
    def fail(_):
        raise RuntimeError('offline')
    with pytest.raises(RuntimeError):
        autopilot_notifications.deliver_latest_failure(fail, db_path=path, state_path=state, now=now)
    assert not state.exists()
    sent = []
    assert autopilot_notifications.deliver_latest_failure(sent.append, db_path=path, state_path=state, now=now)['status'] == 'delivered'


def test_strategy_noise_cannot_hide_recent_data_failure(tmp_path):
    path = tmp_path/'paper.sqlite'
    db = PaperExecutorDatabase(path)
    identity = db.insert_system_event('ENTRY_BLOCKED', {'reason': 'SKIPPED_MARKET_DATA_UNAVAILABLE'})
    for _ in range(60):
        db.insert_system_event('ENTRY_BLOCKED', {'reason': 'SKIPPED_NO_CONTRACT'})
    assert autopilot_notifications.latest_failure(path)['id'] == identity


@pytest.mark.parametrize('since', ['2026-09-10T10:00:00', '2099-01-01T00:00:00+00:00'])
def test_invalid_pending_timestamps_do_not_send(tmp_path, since):
    path = tmp_path/'paper.sqlite'
    db = PaperExecutorDatabase(path)
    pending_position(db, 'one', since)
    sent = []
    result = autopilot_notifications.deliver_latest_failure(sent.append, db_path=path, state_path=tmp_path/'notice.json')
    assert result['status'] == 'invalid_event' and not sent


def test_blocking_failure_alert_is_deduplicated_and_stale_safe(tmp_path):
    db_path = tmp_path / "paper.sqlite"
    db = PaperExecutorDatabase(db_path)
    event_id = db.insert_system_event("ENTRY_BLOCKED", {
        "ticker": "SPY", "reason": "SKIPPED_MARKET_DATA_UNAVAILABLE",
        "error": "Cipher core /api/options-chain HTTP 401",
    })
    state = tmp_path / "notification.json"
    sent = []
    now = datetime.now(timezone.utc)

    first = autopilot_notifications.deliver_latest_failure(
        sent.append, db_path=db_path, state_path=state, now=now,
    )
    second = autopilot_notifications.deliver_latest_failure(
        sent.append, db_path=db_path, state_path=state, now=now,
    )
    stale = autopilot_notifications.deliver_latest_failure(
        sent.append, db_path=db_path, state_path=tmp_path / "other.json",
        now=now + timedelta(minutes=20),
    )

    assert first == {"status": "delivered", "event_id": event_id}
    assert second["status"] == "already_delivered"
    assert stale["status"] == "stale"
    assert len(sent) == 1
    assert "No simulated fill was created" in sent[0]


def test_strategy_rejection_does_not_page_operations(tmp_path):
    db_path = tmp_path / "paper.sqlite"
    db = PaperExecutorDatabase(db_path)
    db.insert_system_event("ENTRY_BLOCKED", {"ticker": "SPY", "reason": "SKIPPED_NO_CONTRACT"})
    assert autopilot_notifications.latest_failure(db_path) is None


def test_failed_auto_promotion_pages_operations(tmp_path):
    db_path = tmp_path / "paper.sqlite"
    db = PaperExecutorDatabase(db_path)
    event_id = db.insert_system_event("AUTO_PAPER_PROMOTION", {
        "ok": False, "reason": "database reconciliation has not passed",
    })
    event = autopilot_notifications.latest_failure(db_path)
    assert event and event["id"] == event_id


def test_successful_auto_promotion_clears_older_failure(tmp_path):
    db_path = tmp_path / "paper.sqlite"
    db = PaperExecutorDatabase(db_path)
    db.insert_system_event("AUTO_PAPER_PROMOTION", {
        "ok": False, "reason": "database reconciliation has not passed",
    })
    db.insert_system_event("AUTO_PAPER_PROMOTION", {"ok": True, "reason": "ready"})

    assert autopilot_notifications.latest_failure(db_path) is None


def test_after_close_recap_preserves_earnings_data_without_retired_summary(tmp_path):
    autopilot_db = tmp_path / "autopilot.sqlite"
    db = PaperExecutorDatabase(autopilot_db)
    db.insert_system_event("ENTRY_BLOCKED", {
        "ticker": "SPY", "reason": "SKIPPED_MARKET_DATA_UNAVAILABLE",
    })
    earnings_db = tmp_path / "earnings" / "paper.sqlite"
    init_paper_db(str(earnings_db)).close()
    now = datetime.now(timezone.utc)
    result = portfolio_daily_report.preview(
        tmp_path / "fronttest.sqlite", now=now,
        prospective_db_path=tmp_path / "prospective.sqlite",
        autopilot_db_path=autopilot_db, earnings_db_path=earnings_db,
    )
    assert result["snapshot"]["autopilot"]["operating_state"] == "DATA_FAILURE"
    assert result["snapshot"]["earnings"]["available"] is True
    assert "Local autopilot: DATA_FAILURE" in result["message"]
    assert "Earnings legacy:" not in result["message"]
    assert len(result["message"]) < 1900
