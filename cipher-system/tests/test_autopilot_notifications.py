from datetime import datetime, timedelta, timezone

from core import autopilot_notifications
from core import portfolio_daily_report
from core.paper_executor.database import PaperExecutorDatabase
from earnings_model.paper_portfolio import init_paper_db


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


def test_after_close_recap_includes_separate_autopilot_and_earnings_ledgers(tmp_path):
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
    assert "Earnings legacy:" in result["message"]
    assert len(result["message"]) < 1900
