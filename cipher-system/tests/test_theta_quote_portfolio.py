from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from core import theta_portfolio as p, theta_evidence as e
from core.theta_quote_execution import Contract, Leg, Quote, entry, liquidation_pnl, trigger

NOW = datetime(2026, 9, 10, 15, tzinfo=timezone.utc)
TEXT = 'Buy SPY 600C qty 1; debit 2.00 points; 2026-09-11; swing'


class Provider:
    def __init__(self):
        self.now = NOW
        self.bid = 2.0
        self.ask = 2.1
        self.missing = False

    def session(self, day):
        at = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        return at.replace(hour=13, minute=30), at.replace(hour=20)

    def contracts(self, symbol, expiry):
        return [Contract(symbol+'C600', symbol, expiry, 'C', 600, 'physical'), Contract(symbol+'C610', symbol, expiry, 'C', 610, 'physical')]

    def contract_close(self, contract, day):
        return self.session(day)[1]

    def quotes(self, symbols):
        return {} if self.missing else {s: Quote(s, self.bid, self.ask, self.now, 2, 2) for s in symbols}


def message(mid=1, text=TEXT, **kw):
    return {'id': mid, 'date': NOW.isoformat(), 'text': text, **kw}


def book(tmp_path, paper=True):
    db = p.connect(tmp_path/'quote.sqlite')
    p.initialize(db, 0, NOW-timedelta(seconds=10))
    if paper:
        # Unit fixtures deliberately bypass the rollout to exercise execution.
        with db:
            p.put(db, 'mode', 'paper')
            p.put(db, 'activated_at', (NOW-timedelta(seconds=10)).isoformat())
    return db


def test_observation_does_not_open_and_cannot_activate(tmp_path):
    db = book(tmp_path, False)
    p.ingest(db, message(), NOW)
    p.tick(db, Provider(), NOW)
    assert db.execute('select count(*) from positions').fetchone()[0] == 0
    assert db.execute('select count(*) from observations').fetchone()[0] == 1
    with pytest.raises(ValueError):
        p.activate(db, NOW)


def test_pending_exit_restart_retry_and_atomic_events(tmp_path):
    db = book(tmp_path)
    provider = Provider()
    p.ingest(db, message(), NOW)
    p.tick(db, provider, NOW)
    assert p.ingest(db, message(), NOW) == 'duplicate'
    p.ingest(db, message(2, 'Trim 80%', reply_to_message_id=1), NOW)
    provider.missing = True
    p.tick(db, provider, NOW+timedelta(seconds=10))
    row = db.execute('select * from positions').fetchone()
    assert row['status'] == 'OPEN' and row['pending_reason'] == 'signal' and row['mark_pnl'] is None
    db.close()
    db = p.connect(tmp_path/'quote.sqlite')
    assert p.reconcile(db)
    provider.missing = False
    provider.now = NOW+timedelta(seconds=20)
    p.tick(db, provider, provider.now)
    assert db.execute('select status from positions').fetchone()[0] == 'CLOSED'
    p.tick(db, provider, provider.now)
    assert db.execute("select count(*) from events where event_id like '%:close:%'").fetchone()[0] == 1


@pytest.mark.parametrize('text', ['Commands\nclose SPY', 'EOD Report\nBuy SPY 600C qty 1 debit 2 points today', 'Trim 80%', 'Close SPY 600P', 'Close SPY 600/620C'])
def test_menus_wrong_types_and_ambiguous_closes_do_not_exit(tmp_path, text):
    db = book(tmp_path)
    p.ingest(db, message(), NOW)
    p.tick(db, Provider(), NOW)
    p.ingest(db, message(2, text), NOW)
    assert db.execute('select pending_reason from positions').fetchone()[0] is None


@pytest.mark.parametrize('change', [lambda q: replace(q, ask=float('nan')), lambda q: replace(q, observed_at=NOW-timedelta(seconds=16)), lambda q: replace(q, observed_at=NOW+timedelta(seconds=1)), lambda q: replace(q, bid_size=0), lambda q: replace(q, ask_size=None)])
def test_bad_quotes_block_whole_fill(change):
    c = Contract('SPY600C', 'SPY', '2026-09-11', 'C', 600, 'physical')
    q = change(Quote(c.symbol, 1, 1.1, NOW, 2, 2))
    with pytest.raises(ValueError):
        entry([Leg(c, 'SELL' if q.bid_size == 0 else 'BUY')], {c.symbol: c}, {c.symbol: q}, now=NOW)


def test_credit_and_debit_pnl_costs_and_thresholds():
    c = Contract('A', 'SPY', '2026-09-11', 'C', 600, 'physical')
    q = {'A': Quote('A', 2, 2.1, NOW, 2, 2)}
    for side in ('BUY', 'SELL'):
        legs = [Leg(c, side)]
        opening = entry(legs, {'A': c}, q, now=NOW, fee_per_contract=.65)
        assert liquidation_pnl(opening, legs, q, now=NOW, fee_per_contract=.65) == -11.3
        assert trigger(opening, abs(opening['premium'])*.5) == 'TP'
        assert trigger(opening, -abs(opening['premium'])*.25) == 'SL'


def test_partial_and_asynchronous_four_leg_quotes():
    legs = [Leg(Contract(str(i), 'SPY', '2026-09-11', 'C' if i<2 else 'P', 600+i, 'physical'), 'BUY') for i in range(4)]
    contracts = {l.contract.symbol:l.contract for l in legs}
    qs = {str(i):Quote(str(i), 1, 1.1, NOW, 2, 2) for i in range(4)}
    assert len(entry(legs, contracts, qs, now=NOW)['legs']) == 4
    with pytest.raises(ValueError):
        entry(legs, contracts, {k:v for k,v in qs.items() if k!='3'}, now=NOW)
    qs['3'] = replace(qs['3'], observed_at=NOW-timedelta(seconds=6))
    with pytest.raises(ValueError):
        entry(legs, contracts, qs, now=NOW)


def test_no_root_expiry_or_settlement_substitution():
    parsed = e.parse(TEXT, NOW.isoformat())
    c = Contract('A', 'SPXW', '2026-09-11', 'C', 600, 'cash_pm')
    for bad in [c, replace(c, underlying='SPY', expiration='2026-09-12'), replace(c, underlying='SPY', settlement='')]:
        with pytest.raises(ValueError):
            p.resolve(parsed, [bad])


@pytest.mark.parametrize('text', [TEXT.replace('qty 1', ''), TEXT.replace('qty 1', 'qty 2'), TEXT.replace('2026-09-11', '9/11'), TEXT.replace('points', ''), TEXT.replace('Buy', ''), TEXT+'; Buy 610C', TEXT.replace('swing', 'hold for 2 days')])
def test_incomplete_evidence_requires_review(text):
    with pytest.raises(ValueError):
        e.parse(text, NOW.isoformat())


def test_ocr_agreement_conflict_and_incomplete(monkeypatch):
    monkeypatch.setattr(e, 'ocr', lambda _: {'text': TEXT, 'error': None, 'confidence': 95, 'sha256': 'a', 'parser_version': e.VERSION})
    assert e.evidence(message(media_path='local'))[0] is not None
    assert e.evidence(message(text=TEXT.replace('600C', '610C'), media_path='local'))[2] == 'caption_image_conflict'
    assert e.evidence(message(text='Buy SPY', media_path='local'))[0] is None


def test_review_requires_identity_and_freshness(tmp_path):
    db = book(tmp_path)
    p.ingest(db, message(text='incomplete'), NOW)
    with pytest.raises(PermissionError):
        p.review(db, 1, 'approve', 'guest', NOW, TEXT)
    with pytest.raises(ValueError, match='expired_signal'):
        p.review(db, 1, 'approve', 'owner', NOW+timedelta(minutes=3), TEXT)
    p.review(db, 1, 'approve', 'owner', NOW, TEXT)
    assert db.execute('select count(*) from positions').fetchone()[0] == 0
    p.tick(db, Provider(), NOW)
    assert db.execute('select count(*) from positions').fetchone()[0] == 1


def test_swing_overnight_and_same_day_deadline_missing_quotes(tmp_path):
    db = book(tmp_path)
    provider = Provider()
    p.ingest(db, message(), NOW)
    p.ingest(db, message(2, TEXT.replace('swing', 'same-day')), NOW)
    p.tick(db, provider, NOW)
    provider.missing = True
    p.tick(db, provider, NOW.replace(hour=19, minute=45))
    rows = db.execute('select * from positions order by id').fetchall()
    assert rows[0]['pending_reason'] is None
    assert rows[1]['pending_reason'] == 'session_deadline'
    p.tick(db, provider, NOW+timedelta(days=2))
    assert all(r[0] is None for r in db.execute('select pnl from positions'))


def test_notification_failure_does_not_lose_events(tmp_path, monkeypatch):
    from scripts import telegram_theta_worker as worker
    path = tmp_path/'quote.sqlite'
    db = book(tmp_path)
    p.ingest(db, message(), NOW)
    p.tick(db, Provider(), NOW)
    real_connect = p.connect
    monkeypatch.setattr(worker.portfolio, 'connect', lambda: real_connect(path))
    monkeypatch.setattr(worker, 'discord', lambda _: (_ for _ in ()).throw(RuntimeError('outage')))
    worker.deliver()
    assert db.execute('select attempts from events order by created_at limit 1').fetchone()[0] == 1
    sent = []
    monkeypatch.setattr(worker, 'discord', sent.append)
    worker.deliver()
    assert sent and 'Event: theta-quote-v1:' in sent[0]
    assert not db.execute('select 1 from events where delivered_at is null').fetchone()


def test_position_and_event_rollback_together(tmp_path):
    db = book(tmp_path)
    p.ingest(db, message(), NOW)
    db.execute("create trigger abort_open before insert on events when new.event_id like '%:open:%' begin select raise(ABORT,'test'); end")
    with pytest.raises(sqlite3.IntegrityError):
        p.tick(db, Provider(), NOW)
    assert db.execute('select count(*) from positions').fetchone()[0] == 0


def test_early_close_and_distinct_duplicate_messages(tmp_path):
    db = book(tmp_path)
    provider = Provider()
    p.ingest(db, message(text=TEXT.replace('swing', 'same-day')), NOW)
    p.tick(db, provider, NOW)
    assert p.ingest(db, message(2, TEXT.replace('swing', 'same-day')), NOW) == 'duplicate'
    provider.contract_close = lambda c, day: NOW.replace(hour=17)
    provider.now = NOW.replace(hour=16, minute=45)
    p.tick(db, provider, provider.now)
    row = db.execute('select * from positions').fetchone()
    assert row['status'] == 'CLOSED' and row['pending_reason'] == 'session_deadline'


def test_tp_without_telegram(tmp_path):
    db = book(tmp_path)
    provider = Provider()
    p.ingest(db, message(), NOW)
    p.tick(db, provider, NOW)
    provider.bid, provider.ask = 3.5, 3.6
    provider.now += timedelta(seconds=10)
    p.tick(db, provider, provider.now)
    row = db.execute('select * from positions').fetchone()
    assert row['status'] == 'CLOSED' and row['pending_reason'] == 'TP'


def test_real_local_ocr_complete_and_incomplete_screenshots(tmp_path):
    import shutil
    if not shutil.which('tesseract'):
        pytest.skip('local Tesseract optional dependency unavailable')
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=32)
    for text, expected in [(TEXT, True), ('Buy SPY call; debit 2 points', False)]:
        image = Image.new('RGB', (1600, 160), 'white')
        ImageDraw.Draw(image).text((25, 40), text, fill='black', font=font)
        path = tmp_path/'ocr.png'
        image.save(path)
        extracted = e.ocr(str(path))
        assert extracted['sha256'] and extracted['text']
        try:
            parsed = e.parse(extracted['text'], NOW.isoformat())
        except ValueError:
            parsed = None
        assert bool(parsed) == expected


def test_rollout_accepts_only_complete_regular_session_with_restart(tmp_path):
    from zoneinfo import ZoneInfo
    db = book(tmp_path, False)
    start = NOW.replace(hour=13, minute=30)
    end = NOW.replace(hour=20)
    with db:
        for seconds in range(0, 23401, 10):
            at = start+timedelta(seconds=seconds)
            db.execute('insert into passes values(?,?,?,?,?,?,?)', (at.isoformat(), 'observe', start.astimezone(ZoneInfo('America/New_York')).isoformat(), end.astimezone(ZoneInfo('America/New_York')).isoformat(), 1, 1, 1))
    assert not p.rollout(db)['eligible']
    p.initialize(db, 1, end+timedelta(seconds=10))
    assert p.rollout(db)['eligible']
    with db:
        db.execute('update passes set covered=0 where at=?', (start.isoformat(),))
    assert not p.rollout(db)['eligible']


def test_quotes_received_during_request_use_completion_clock(tmp_path):
    db = book(tmp_path)
    provider = Provider()
    provider.now = NOW + timedelta(seconds=2)
    p.ingest(db, message(), NOW)
    ticks = iter([NOW, NOW, NOW, provider.now])
    p.tick(db, provider, clock=lambda: next(ticks, provider.now))
    row = db.execute('select * from positions').fetchone()
    assert row and row['opened_at'] == provider.now.isoformat()


def test_signal_expiring_during_quote_request_cannot_open(tmp_path):
    db = book(tmp_path)
    provider = Provider()
    current = [NOW]
    original_quotes = provider.quotes

    def delayed_quotes(symbols):
        current[0] = provider.now = NOW + timedelta(seconds=121)
        return original_quotes(symbols)

    provider.quotes = delayed_quotes
    p.ingest(db, message(), NOW)
    p.tick(db, provider, clock=lambda: current[0])
    assert not db.execute('select 1 from positions').fetchone()
    assert db.execute('select reason from candidates').fetchone()[0] == 'expired_signal'


def test_cached_deadline_survives_restart_and_calendar_outage(tmp_path):
    db = book(tmp_path)
    provider = Provider()
    p.ingest(db, message(text=TEXT.replace('swing', 'same-day')), NOW)
    p.tick(db, provider, NOW)
    db.close()
    db = p.connect(tmp_path/'quote.sqlite')

    def outage(*args):
        raise RuntimeError('calendar_unavailable')

    provider.session = provider.contract_close = outage
    p.tick(db, provider, NOW.replace(hour=19, minute=45))
    row = db.execute('select * from positions').fetchone()
    assert row['status'] == 'OPEN'
    assert row['pending_reason'] == 'session_deadline'
    assert row['pnl'] is None and row['mark_pnl'] is None
    assert db.execute("select count(*) from events where event_id like '%:pending:%'").fetchone()[0] == 1


def test_open_positions_checked_before_new_entry_metadata(tmp_path):
    db = book(tmp_path)
    provider = Provider()
    p.ingest(db, message(), NOW)
    p.tick(db, provider, NOW)
    p.ingest(db, message(2, TEXT.replace('600C', '610C')), NOW)
    calls = []
    contracts, quotes = provider.contracts, provider.quotes
    provider.contracts = lambda *args: (calls.append('contracts'), contracts(*args))[1]
    provider.quotes = lambda *args: (calls.append('quotes'), quotes(*args))[1]
    p.tick(db, provider, NOW)
    assert calls[0] == 'quotes'


def test_idle_session_does_not_claim_quote_health(tmp_path):
    db = book(tmp_path, False)
    p.tick(db, Provider(), NOW)
    result = p.snapshot(tmp_path/'quote.sqlite', NOW)
    assert result['data_health'] == 'no_quote_evidence'
    assert result['quote_coverage'] == {'passes': 1, 'requested': 0, 'covered': 0}
    assert not result['rollout']['eligible']


def test_nontrading_day_is_not_calendar_outage(tmp_path):
    db = book(tmp_path, False)
    provider = Provider()
    provider.session = lambda day: None
    p.tick(db, provider, NOW)
    assert p.snapshot(tmp_path/'quote.sqlite', NOW)['data_health'] == 'outside_session'
    assert not db.execute('select 1 from incidents where active=1').fetchone()


@pytest.mark.parametrize('seconds,closed', [(2, True), (902, False)])
def test_exit_quote_request_crossing_deadline_or_close(tmp_path, seconds, closed):
    db = book(tmp_path)
    provider = Provider()
    p.ingest(db, message(text=TEXT.replace('swing', 'same-day')), NOW)
    p.tick(db, provider, NOW)
    current = [NOW.replace(hour=19, minute=44, second=59)]
    original_quotes = provider.quotes

    def delayed_quotes(symbols):
        current[0] += timedelta(seconds=seconds)
        provider.now = current[0]
        return original_quotes(symbols)

    provider.quotes = delayed_quotes
    p.tick(db, provider, clock=lambda: current[0])
    row = db.execute('select * from positions').fetchone()
    assert (row['status'] == 'CLOSED') == closed
    assert row['pending_reason'] == 'session_deadline'
    if not closed:
        assert row['pnl'] is None and row['exit_json'] is None


def test_review_racing_decision_does_not_overwrite_or_audit(tmp_path, monkeypatch):
    db = book(tmp_path)
    p.ingest(db, message(text='incomplete'), NOW)
    parse = e.parse

    def concurrent_rejection(*args):
        other = p.connect(tmp_path/'quote.sqlite')
        try:
            p.review(other, 1, 'reject', 'owner', NOW)
        finally:
            other.close()
        return parse(*args)

    monkeypatch.setattr(e, 'parse', concurrent_rejection)
    with pytest.raises(ValueError, match='candidate_not_reviewable'):
        p.review(db, 1, 'approve', 'owner', NOW, TEXT)
    assert db.execute('select status from candidates').fetchone()[0] == 'rejected'
    assert [r[0] for r in db.execute('select action from reviews')] == ['reject']
