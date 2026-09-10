import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from core import theta_evidence as e, theta_portfolio as p
from scripts import telegram_theta_worker as worker
from test_theta_quote_portfolio import NOW, TEXT, book, message, Provider


def test_literal_ocr_quotes_never_swallow_tsv_rows(tmp_path, monkeypatch):
    path = tmp_path/'image.png'
    path.write_bytes(b'local image fixture')
    header = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    rows = ['5\t1\t1\t1\t1\t1\t0\t0\t20\t20\t95\t"literal',
            '5\t1\t1\t1\t2\t1\t0\t20\t20\t20\t96\tBuy SPY',
            '5\t1\t1\t1\t2\t2\t20\t20\t20\t20\t97\t600C qty 1']
    monkeypatch.setattr(e.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=header+'\n'.join(rows)))
    result = e.ocr(str(path))
    assert result['text'] == '"literal\nBuy SPY 600C qty 1'
    assert '\t' not in result['text']
    assert result['confidence'] == 95 and result['error'] is None
    assert result['parser_version'] == 'theta-evidence-v3'


@pytest.mark.parametrize('heading', ['🟢 Trade Opened #8', '🔴 Trade Closed #8'])
def test_source_receipts_cannot_execute_even_with_complete_embedded_signal(tmp_path, heading):
    db = book(tmp_path)
    with pytest.raises(ValueError, match='administrative_or_historical'):
        e.parse(heading+'\nText: '+TEXT, NOW.isoformat())
    assert p.ingest(db, message(text=heading+'\nText: '+TEXT), NOW) == 'ignored'
    p.tick(db, Provider(), NOW)
    assert not db.execute('select 1 from positions').fetchone()
    assert not db.execute('select 1 from observations').fetchone()


def test_review_incident_is_atomic_and_deduplicated(tmp_path):
    db = book(tmp_path)
    p.ingest(db, message(text='Buy SPY'), NOW)
    p.ingest(db, message(2, text='Buy QQQ'), NOW)
    for offset in (10, 20, 30):
        p.tick(db, Provider(), NOW+timedelta(seconds=offset))
    assert db.execute("select count(*) from events where event_id like '%incident:review_required:%'").fetchone()[0] == 1
    result = p.snapshot(tmp_path/'quote.sqlite', NOW+timedelta(seconds=30))
    assert result['review_pending'] == 2
    assert result['data_health'] == 'no_quote_evidence'
    p.review(db, 1, 'reject', 'owner', NOW)
    p.review(db, 2, 'reject', 'owner', NOW)
    assert db.execute("select active from incidents where key='review_required'").fetchone()[0] == 0


def test_ingestion_heartbeat_errors_and_recovery_are_separate(tmp_path):
    db = book(tmp_path)
    p.tick(db, Provider(), NOW)
    p.ingestion_status(db, NOW)
    assert p.snapshot(tmp_path/'quote.sqlite', NOW)['ingestion_health'] == 'current'
    assert p.snapshot(tmp_path/'quote.sqlite', NOW+timedelta(seconds=46))['ingestion_health'] == 'stopped_or_stale'
    p.ingestion_status(db, NOW, 'TimeoutError')
    p.ingestion_status(db, NOW, 'TimeoutError')
    result = p.snapshot(tmp_path/'quote.sqlite', NOW)
    assert result['ingestion_health'] == 'error'
    assert result['execution_health'] == 'current'
    assert db.execute("select count(*) from events where event_id like '%telegram_ingestion_unavailable:%'").fetchone()[0] == 1
    p.ingestion_status(db, NOW+timedelta(seconds=10))
    assert p.snapshot(tmp_path/'quote.sqlite', NOW+timedelta(seconds=10))['ingestion_health'] == 'current'


def test_poll_is_oldest_first_cursor_bounded_and_timed_out(monkeypatch):
    options = {}

    class Client:
        async def iter_messages(self, entity, **kw):
            options.update(kw)
            yield SimpleNamespace(id=11)
            yield SimpleNamespace(id=12)

    actual_wait = asyncio.wait_for

    async def wait(awaitable, timeout):
        assert timeout == 30
        return await actual_wait(awaitable, timeout)

    monkeypatch.setattr(worker.asyncio, 'wait_for', wait)
    result = asyncio.run(worker.poll_messages(Client(), 'bot', 10))
    assert [m.id for m in result] == [11, 12]
    assert options == {'min_id': 10, 'reverse': True, 'limit': 100}


def test_review_notification_failure_retains_event_id(tmp_path, monkeypatch):
    db = book(tmp_path)
    p.ingest(db, message(text='Buy SPY'), NOW)
    connect = p.connect
    monkeypatch.setattr(worker.portfolio, 'connect', lambda: connect(tmp_path/'quote.sqlite'))
    monkeypatch.setattr(worker, 'discord', lambda _: (_ for _ in ()).throw(RuntimeError('offline')))
    worker.deliver()
    event_id = db.execute('select event_id from events').fetchone()[0]
    sent = []
    monkeypatch.setattr(worker, 'discord', sent.append)
    worker.deliver()
    assert len(sent) == 1 and event_id in sent[0]
    assert 'review required' in sent[0] and 'Buy SPY' not in sent[0]
    assert db.execute('select delivered_at from events').fetchone()[0]
