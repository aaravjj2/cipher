from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core import portfolio_daily_report as daily
from core import fronttest_portfolios

NY = ZoneInfo("America/New_York")


def test_theta_daily_counts_use_source_dates_and_never_expose_evidence(tmp_path):
    from core import theta_portfolio as theta
    path = tmp_path/'theta.sqlite'
    db = theta.connect(path)
    now = datetime(2026, 9, 10, 16, 10, tzinfo=NY)
    theta.ingest(db, {'id': 1, 'date': now.isoformat(), 'text': 'private incomplete caption'}, now)
    theta.ingest(db, {'id': 2, 'date': '2026-09-11T02:00:00+00:00', 'text': 'Menu'}, now)
    theta.ingest(db, {'id': 3, 'date': '2026-09-11T05:00:00+00:00', 'text': 'Menu'}, now)
    db.close()
    result = daily._theta_snapshot(path, now.date())
    assert result['messages_today'] == 2
    assert result['dispositions_today'] == {'ignored': 1, 'needs_review': 1}
    assert 'private incomplete caption' not in str(result)
    message = daily.current_message({'report_day': '2026-09-10', 'theta': result})
    assert 'observe' in message and '2 messages today' in message
    assert '0 validated entries' in message


def test_daily_service_can_read_all_wal_ledgers():
    service = (Path(__file__).resolve().parents[2]/'infra/gcp-cipher-vm/systemd/cipher-portfolio-discord-daily.service').read_text()
    for suffix in ('paper_runtime/data/paper_trades', 'paper_runtime/cohorts/confirmation',
                   'paper_runtime/cohorts/cost', 'paper_runtime/cohorts/exit', 'telegram'):
        assert f'ReadWritePaths=/home/aarav/Aarav/cipher/runtime/data/{suffix}' in service
    assert 'ReadWritePaths=/home/aarav/Aarav/cipher/runtime\n' not in service


def test_current_notification_excludes_legacy_research_totals():
    message = daily.current_message({'report_day':'2026-09-10', 'portfolios':[{'portfolio_id':'v6_nvda_c05'}], 'earnings':{'settled':33}, 'autopilot':{}, 'autopilot_cohorts':[]})
    assert 'v6_nvda' not in message and '33 settled' not in message
    assert 'baseline: unavailable' in message


def test_preview_contains_only_active_isolated_portfolios(tmp_path: Path):
    result = daily.preview(tmp_path / "fronttest.sqlite",
                           datetime(2026, 8, 14, 16, 10, tzinfo=NY),
                           prospective_db_path=tmp_path / "prospective.sqlite")
    # The two QQQ systems are deliberately disabled as of 2026-08-18 and
    # v6_nvda_c05 was registered 2026-08-18, so the digest covers the five
    # active portfolios (500k combined starting cash).
    active = [spec.portfolio_id for spec in fronttest_portfolios.ACTIVE_SPECS]
    assert result["snapshot"]["combined_equity"] == 500_000
    assert result["snapshot"]["daily_pnl"] == 0
    assert [row["portfolio_id"] for row in result["snapshot"]["portfolios"]] == active
    assert "Paper simulation only" in result["message"]
    assert len(result["message"]) < 1900


def test_delivery_is_recorded_only_once(tmp_path: Path):
    sent = []
    path = tmp_path / "fronttest.sqlite"
    now = datetime(2026, 8, 14, 16, 10, tzinfo=NY)
    prospective = tmp_path / "prospective.sqlite"
    first = daily.deliver(sent.append, db_path=path, prospective_db_path=prospective, now=now)
    second = daily.deliver(sent.append, db_path=path, prospective_db_path=prospective, now=now)
    assert first["status"] == "delivered"
    assert second["status"] == "already_delivered"
    assert len(sent) == 1


def test_failed_sender_does_not_mark_report_delivered(tmp_path: Path):
    path = tmp_path / "fronttest.sqlite"
    now = datetime(2026, 8, 14, 16, 10, tzinfo=NY)

    def fail(_message):
        raise RuntimeError("transport unavailable")

    try:
        daily.deliver(fail, db_path=path, prospective_db_path=tmp_path / "prospective.sqlite", now=now)
    except RuntimeError:
        pass
    sent = []
    result = daily.deliver(sent.append, db_path=path, prospective_db_path=tmp_path / "prospective.sqlite", now=now)
    assert result["status"] == "delivered"
    assert len(sent) == 1


def test_preview_reports_skipped_counterfactual_outcomes(tmp_path: Path):
    path = tmp_path / "fronttest.sqlite"
    db = fronttest_portfolios.connect(path)
    db.execute(
        """insert into signals(signal_id,portfolio_id,symbol,setup_id,direction,signal_at,
                               detected_at,payload_json,disposition,skip_reason)
           values ('skip1','v6_nvda_p05','NVDA','P05','short',
                   '2026-08-14T10:00:00-04:00','2026-08-14T10:01:00-04:00','{}',
                   'SKIPPED','DAILY_LIMIT')"""
    )
    db.execute(
        """insert into signal_outcomes(
               signal_id,portfolio_id,symbol,status,outcome,methodology,created_at,updated_at)
           values ('skip1','v6_nvda_p05','NVDA','RESOLVED','TARGET',
                   'underlying_path_only','2026-08-14T10:30:00Z','2026-08-14T10:30:00Z')"""
    )
    db.commit()
    db.close()
    result = daily.preview(path, datetime(2026, 8, 14, 16, 10, tzinfo=NY),
                           prospective_db_path=tmp_path / "prospective.sqlite")
    active = [row for row in result["snapshot"]["portfolios"] if row["portfolio_id"] == "v6_nvda_p05"]
    assert active[0]["skipped_targets"] == 1
    assert "skipped path: 1 target" in result["message"]


def test_preview_includes_prospective_cohort_progress(tmp_path: Path):
    from core import prospective_fronttests

    prospective = tmp_path / "prospective.sqlite"
    prospective_fronttests.connect(prospective).close()
    result = daily.preview(
        tmp_path / "fronttest.sqlite", datetime(2026, 8, 17, 16, 10, tzinfo=NY),
        prospective_db_path=prospective,
    )
    assert len(result["snapshot"]["prospective_programs"]) == 2
    assert "Prospective cohorts (no backfill)" in result["message"]
    assert "tsla_stable_wall_rejection_v1" in result["message"]
