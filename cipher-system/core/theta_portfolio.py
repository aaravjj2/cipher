"""Versioned SQLite Theta portfolio, observation gates and transactional outbox."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from core import theta_evidence as evidence
from core.theta_quote_execution import Contract, Leg, entry, trigger

DB = Path('/home/aarav/Aarav/cipher/runtime/data/telegram/theta_quote_v1.sqlite')
VERSION = 'theta-quote-v1'
NY = ZoneInfo('America/New_York')
STARTING_CASH = 10000.0
SLIPPAGE_BPS = 10.0
FEE = .65
SIGNAL_AGE = 120


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone_required')
    return result


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def connect(path=DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('pragma journal_mode=WAL')
    db.execute('pragma foreign_keys=ON')
    db.executescript('''
      create table if not exists settings(key text primary key,value text not null);
      create table if not exists candidates(
        id integer primary key, observed_at text not null, body text not null, reply_id integer,
        media_json text, parsed_json text, status text not null, reason text, reviewed_by text,
        reviewed_at text, correction_json text);
      create table if not exists positions(
        id integer primary key, candidate_id integer unique not null references candidates(id),
        symbol text not null, expiration text not null, holding text not null, legs_json text not null,
        entry_json text not null, collateral real not null, opened_at text not null,
        status text not null default 'OPEN', mark_pnl real, quote_at text, mark_error text,
        pending_reason text, requested_at text, exit_json text, closed_at text, pnl real);
      create table if not exists events(
        event_id text primary key, created_at text not null, payload text not null,
        delivered_at text, attempts integer not null default 0, last_error text);
      create table if not exists passes(
        at text primary key, mode text not null, session_open text, session_close text,
        requested integer not null, covered integer not null, reconciled integer not null);
      create table if not exists observations(
        candidate_id integer primary key, at text not null, result_json text not null);
      create table if not exists incidents(key text primary key, active integer not null, at text not null);
      create table if not exists verified_sessions(
        position_id integer not null references positions(id), day text not null,
        session_open text not null, session_close text not null,
        primary key(position_id,day));
      create table if not exists reviews(
        id integer primary key, candidate_id integer not null, actor text not null,
        at text not null, action text not null, correction_json text);
    ''')
    with db:
        db.execute("insert or ignore into settings values('mode','observe')")
        db.execute("insert or ignore into settings values('version',?)", (VERSION,))
    return db


def get(db, key, default=None):
    row = db.execute('select value from settings where key=?', (key,)).fetchone()
    return row[0] if row else default


def put(db, key, value):
    db.execute('insert into settings values(?,?) on conflict(key) do update set value=excluded.value', (key, str(value)))


def event(db, key, payload, now):
    event_id = f'{VERSION}:{key}'
    db.execute('insert or ignore into events(event_id,created_at,payload) values(?,?,?)',
               (event_id, now.isoformat(), dump({**payload, 'event_id': event_id, 'portfolio': VERSION})))


def incident(db, key, active, now):
    previous = db.execute('select active from incidents where key=?', (key,)).fetchone()
    if active and (not previous or not previous[0]):
        event(db, f'incident:{key}:{now.isoformat()}', {'action': 'incident', 'reason': key}, now)
    db.execute('insert into incidents values(?,?,?) on conflict(key) do update set active=excluded.active,at=excluded.at', (key, int(active), now.isoformat()))


def initialize(db, latest_id, now):
    with db:
        if get(db, 'cursor') is None:
            put(db, 'cursor', latest_id)
            put(db, 'initialized_at', now.isoformat())
        if not reconcile(db):
            raise ValueError('restart_reconciliation_failed')
        put(db, 'restart_checked_at', now.isoformat())
        put(db, 'restart_count', int(get(db, 'restart_count', 0)) + 1)


def ingestion_status(db, now, error=None):
    """Transport heartbeat is separate from quote execution and new arrivals."""
    with db:
        if error:
            put(db, 'ingestion_error', error)
        else:
            put(db, 'last_ingestion_poll', now.isoformat())
            put(db, 'ingestion_error', '')
        incident(db, 'telegram_ingestion_unavailable', bool(error), now)


def review_incident(db, now):
    pending = db.execute("select 1 from candidates where status='needs_review' limit 1").fetchone()
    incident(db, 'review_required', bool(pending), now)


def reconcile(db):
    if db.execute('pragma quick_check').fetchone()[0] != 'ok':
        return False
    for row in db.execute('select * from positions'):
        opening = json.loads(row['entry_json'])
        if opening.get('version') != VERSION or opening.get('quantity') != 1:
            return False
        if not db.execute('select 1 from events where event_id=?', (f'{VERSION}:open:{row["id"]}',)).fetchone():
            return False
        if row['status'] == 'CLOSED' and (row['pnl'] is None or not row['exit_json']):
            return False
    return True


def _matches(db, message):
    rows = list(db.execute("select * from positions where status='OPEN'"))
    # A supplied identity is authoritative; an unknown identity never falls back.
    if message.get('reply_to_message_id'):
        return [r for r in rows if r['candidate_id'] == int(message['reply_to_message_id'])]
    text = evidence.source(message.get('text') or '').upper()
    identity = re.search(r'\bPOSITION\s*#?(\d+)\b', text)
    if identity:
        return [r for r in rows if r['id'] == int(identity[1])]
    words = set(re.findall(r'\b[A-Z]{1,6}\b', text))
    symbols = words - {'CLOSE', 'TRIM', 'TAKE', 'TAKING', 'GAINS', 'ON', 'CALL', 'CALLS', 'PUT', 'PUTS', 'C', 'P', 'QTY', 'ALL', 'THE', 'AT', 'FOR', 'OF'}
    if len(symbols) > 1:
        return []
    strikes = {(float(s), k[0]) for s, k in re.findall(r'\b(\d+(?:\.\d+)?)\s*(CALL|PUT|C|P)\b', text)}
    for a, b, k in re.findall(r'\b(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)(C|P)\b', text):
        strikes.update({(float(a), k), (float(b), k)})
    dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)
    for month, day, year in re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b', text):
        try:
            yy = int(year) + (2000 if len(year) == 2 else 0)
            dates.append(datetime(yy, int(month), int(day)).date().isoformat())
        except ValueError:
            return []
    if not symbols and not strikes:
        return []
    return [r for r in rows if (not symbols or r['symbol'] in symbols) and
            (not dates or set(dates) == {r['expiration']}) and strikes.issubset(
                {(x['contract']['strike'], x['contract']['option_type']) for x in json.loads(r['legs_json'])})]


def ingest(db, message, now):
    mid = int(message['id'])
    if db.execute('select 1 from candidates where id=?', (mid,)).fetchone():
        return 'duplicate'
    text = evidence.source(message.get('text') or '')
    administrative = bool(evidence.ADMIN.search(text) or evidence.ADMIN.search(message.get('text') or ''))
    close = bool(re.search(r'\b(?:close|trim|take gains|taking gains)\b', text, re.I)) and not administrative
    parsed, media, reason = (None, None, 'administrative') if administrative or close else evidence.evidence(message)
    status = 'ignored' if administrative else 'ready' if parsed else 'needs_review'
    if parsed and db.execute('select 1 from candidates where parsed_json=? and substr(observed_at,1,10)=?', (dump(parsed), message['date'][:10])).fetchone():
        status, reason = 'duplicate', 'duplicate_structure_same_day'
    with db:
        db.execute('insert into candidates(id,observed_at,body,reply_id,media_json,parsed_json,status,reason) values(?,?,?,?,?,?,?,?)',
                   (mid, message['date'], message.get('text') or '', message.get('reply_to_message_id'), dump(media) if media else None, dump(parsed) if parsed else None, status, reason))
        if close:
            matched = _matches(db, message)
            status = 'exit_pending' if len(matched) == 1 else 'needs_review'
            db.execute('update candidates set status=?,reason=? where id=?', (status, 'signal' if len(matched) == 1 else 'ambiguous_close', mid))
            if len(matched) == 1:
                request_exit(db, matched[0], 'signal', now)
        put(db, 'cursor', max(mid, int(get(db, 'cursor', 0))))
        review_incident(db, now)
    return status


def request_exit(db, row, reason, now):
    if row['pending_reason']:
        return
    db.execute('update positions set pending_reason=?,requested_at=? where id=?', (reason, now.isoformat(), row['id']))
    event(db, f'pending:{row["id"]}', {'action': 'exit_pending', 'position_id': row['id'], 'reason': reason}, now)


def resolve(candidate, metadata):
    result = []
    for leg in candidate['legs']:
        matches = [c for c in metadata if c.underlying == candidate['symbol'] and
                   c.expiration == candidate['expiration'] and c.option_type == leg['kind'] and c.strike == leg['strike']]
        if len(matches) != 1 or not matches[0].settlement:
            raise ValueError('contract_or_settlement_unresolved')
        result.append(Leg(matches[0], leg['side']))
    return result


def collateral(legs, opening):
    # Worst terminal payoff across every kink, with an explicit unbounded-call
    # check. All contracts must share expiry/settlement, quantity one.
    if len({(l.contract.underlying, l.contract.expiration, l.contract.settlement) for l in legs}) != 1:
        raise ValueError('mixed_settlement_structure')
    if sum(1 if l.side == 'BUY' else -1 for l in legs if l.contract.option_type == 'C') < 0:
        raise ValueError('unbounded_short_call')
    def payoff(spot):
        return sum((1 if l.side == 'BUY' else -1) * max(0, spot-l.contract.strike if l.contract.option_type == 'C' else l.contract.strike-spot) * 100 for l in legs)
    return round(max(0, opening['entry_value'] - min(payoff(s) for s in [0, *[l.contract.strike for l in legs]])), 2)


def _legs(row):
    return [Leg(Contract(**x['contract']), x['side']) for x in json.loads(row['legs_json'])]


def available(db):
    pnl = db.execute("select coalesce(sum(pnl),0) from positions where status='CLOSED'").fetchone()[0]
    reserved = db.execute("select coalesce(sum(collateral),0) from positions where status='OPEN'").fetchone()[0]
    return STARTING_CASH + pnl - reserved


def tick(db, provider, now=None, *, clock=None):
    # Explicit timestamps keep offline replays deterministic; production passes
    # no timestamp and refreshes UTC after every provider operation.
    clock = clock or ((lambda: now) if now is not None else lambda: datetime.now(timezone.utc))
    now = clock()
    mode = get(db, 'mode', 'observe')
    requested = covered = 0
    session = None
    session_error = False
    try:
        session = provider.session(now.astimezone(NY).date().isoformat())
    except Exception:
        session_error = True
    now = clock()
    with db:
        incident(db, 'session_unavailable', session_error, now)
    market_open = session and session[0] <= now < session[1]
    for row in db.execute("select * from positions where status='OPEN'").fetchall():
        now = clock()
        requested += 1
        legs = _legs(row)
        try:
            day = now.astimezone(NY).date().isoformat()
            if day > row['expiration']:
                raise ValueError('unsupported_expiry_settlement')
            cached = db.execute('select * from verified_sessions where position_id=? and day=?', (row['id'], day)).fetchone()
            # Persist an already-attested deadline before any network request.
            with db:
                if row['holding'] == 'same_day' and stamp(row['opened_at']).astimezone(NY).date() < now.astimezone(NY).date():
                    request_exit(db, row, 'session_deadline', now)
                elif cached and (row['holding'] == 'same_day' or day == row['expiration']) and now >= stamp(cached['session_close']) - timedelta(minutes=15):
                    request_exit(db, row, 'session_deadline', now)
            closes = [provider.contract_close(l.contract, day) for l in legs]
            if any(c is None for c in closes):
                raise ValueError('contract_session_unresolved')
            close = min(closes)
            now = clock()
            with db:
                if session:
                    db.execute('insert or replace into verified_sessions values(?,?,?,?)',
                               (row['id'], day, session[0].isoformat(), close.isoformat()))
            with db:
                if row['holding'] == 'same_day' or day == row['expiration']:
                    if now >= close - timedelta(minutes=15) or stamp(row['opened_at']).astimezone(NY).date() < now.astimezone(NY).date() and row['holding'] == 'same_day':
                        request_exit(db, row, 'session_deadline', now)
            if not session or now < session[0] or now >= close:
                raise ValueError('outside_verified_session')
            reverse = [Leg(l.contract, 'SELL' if l.side == 'BUY' else 'BUY') for l in legs]
            quotes = provider.quotes([l.contract.symbol for l in legs])
            now = clock()
            with db:
                if (row['holding'] == 'same_day' or day == row['expiration']) and now >= close - timedelta(minutes=15):
                    latest = db.execute('select * from positions where id=?', (row['id'],)).fetchone()
                    request_exit(db, latest, 'session_deadline', now)
            if not session[0] <= now < close:
                raise ValueError('outside_verified_session')
            fill = entry(reverse, {l.contract.symbol: l.contract for l in legs}, quotes, now=now, slippage_bps=SLIPPAGE_BPS, fee_per_contract=FEE)
            opening = json.loads(row['entry_json'])
            pnl = round(-fill['entry_value'] - opening['entry_value'], 2)
            covered += 1
            with db:
                db.execute('update positions set mark_pnl=?,quote_at=?,mark_error=null where id=?', (pnl, fill['quote_as_of'], row['id']))
                latest = db.execute('select * from positions where id=?', (row['id'],)).fetchone()
                reason = latest['pending_reason'] or trigger(opening, pnl)
                if reason:
                    request_exit(db, latest, reason, now)
                    db.execute("update positions set status='CLOSED',exit_json=?,closed_at=?,pnl=? where id=?", (dump(fill), now.isoformat(), pnl, row['id']))
                    event(db, f'close:{row["id"]}', {'action': 'closed', 'position_id': row['id'], 'reason': reason, 'pnl': pnl, 'fill': fill}, now)
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            with db:
                db.execute('update positions set mark_pnl=null,mark_error=? where id=?', (str(exc)[:200], row['id']))
                if str(exc) == 'unsupported_expiry_settlement':
                    request_exit(db, row, str(exc), now)
    for row in db.execute("select * from candidates where status='ready' order by observed_at,id").fetchall():
        now = clock()
        parsed = json.loads(row['correction_json'] or row['parsed_json'])
        try:
            age = (now - stamp(row['observed_at'])).total_seconds()
            if not 0 <= age <= SIGNAL_AGE or parsed['expiration'] < now.astimezone(NY).date().isoformat():
                raise ValueError('expired_signal')
            if not session or not session[0] <= now < session[1]:
                raise ValueError('outside_verified_entry_session')
            requested += 1
            legs = resolve(parsed, provider.contracts(parsed['symbol'], parsed['expiration']))
            # Provider must attest session close for this exact contract class.
            closes = [provider.contract_close(l.contract, now.astimezone(NY).date().isoformat()) for l in legs]
            if any(c is None for c in closes):
                raise ValueError('contract_session_unresolved')
            if now >= min(closes) - timedelta(minutes=15):
                raise ValueError('outside_verified_entry_session')
            quotes = provider.quotes([l.contract.symbol for l in legs])
            now = clock()
            if not 0 <= (now - stamp(row['observed_at'])).total_seconds() <= SIGNAL_AGE:
                raise ValueError('expired_signal')
            if not session[0] <= now < min(session[1], min(closes) - timedelta(minutes=15)):
                raise ValueError('outside_verified_entry_session')
            fill = entry(legs, {l.contract.symbol: l.contract for l in legs}, quotes, now=now, slippage_bps=SLIPPAGE_BPS, fee_per_contract=FEE)
            if fill['entry_convention'] != parsed['price_style'] or not fill['premium']:
                raise ValueError('price_convention_conflict')
            reserve = collateral(legs, fill)
            covered += 1
            with db:
                db.execute('insert or replace into observations values(?,?,?)', (row['id'], now.isoformat(), dump({'fill': fill, 'legs': [asdict(l) for l in legs], 'collateral': reserve})))
                if mode == 'paper':
                    if available(db) < reserve:
                        raise ValueError('insufficient_available_cash')
                    if stamp(row['observed_at']) < stamp(get(db, 'activated_at')):
                        raise ValueError('preactivation_signal')
                    db.execute('insert into positions(id,candidate_id,symbol,expiration,holding,legs_json,entry_json,collateral,opened_at) values(?,?,?,?,?,?,?,?,?)',
                               (row['id'], row['id'], parsed['symbol'], parsed['expiration'], parsed['holding'], dump([asdict(l) for l in legs]), dump(fill), reserve, now.isoformat()))
                    db.execute('insert or replace into verified_sessions values(?,?,?,?)',
                               (row['id'], now.astimezone(NY).date().isoformat(), session[0].isoformat(), min(closes).isoformat()))
                    event(db, f'open:{row["id"]}', {'action': 'opened', 'position_id': row['id'], 'symbol': parsed['symbol'], 'fill': fill}, now)
                db.execute('update candidates set status=?,reason=null where id=?', ('opened' if mode == 'paper' else 'observed', row['id']))
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            with db:
                db.execute("update candidates set status='needs_review',reason=? where id=?", (str(exc)[:200], row['id']))
    # Continue sampling every observed structure until that session ends. This
    # exercises repeated quote retrieval without opening a paper position.
    if mode == 'observe' and market_open:
        for row in db.execute('select * from observations where substr(at,1,10)=?', (now.isoformat()[:10],)).fetchall():
            requested += 1
            try:
                observed = json.loads(row['result_json'])
                legs = [Leg(Contract(**l['contract']), l['side']) for l in observed['legs']]
                quotes = provider.quotes([l.contract.symbol for l in legs])
                now = clock()
                if not session[0] <= now < session[1]:
                    raise ValueError('outside_verified_session')
                entry(legs, {l.contract.symbol:l.contract for l in legs}, quotes, now=now)
                covered += 1
            except (ValueError, RuntimeError, OSError, KeyError, TypeError):
                pass
    now = clock()
    with db:
        ok = reconcile(db)
        review_incident(db, now)
        incident(db, 'quote_coverage_incomplete', covered < requested, now)
        db.execute('insert or replace into passes values(?,?,?,?,?,?,?)', (now.isoformat(), mode, session[0].isoformat() if session else None, session[1].isoformat() if session else None, requested, covered, int(ok)))
        put(db, 'last_tick', now.isoformat())


def review(db, candidate_id, action, actor, now, correction=None):
    if not actor or actor == 'guest':
        raise PermissionError('authenticated_review_required')
    if action not in {'approve', 'reject'}:
        raise ValueError('invalid_review_action')
    row = db.execute('select * from candidates where id=?', (int(candidate_id),)).fetchone()
    if not row or row['status'] != 'needs_review':
        raise ValueError('candidate_not_reviewable')
    parsed = None
    if action == 'approve':
        if not 0 <= (now-stamp(row['observed_at'])).total_seconds() <= SIGNAL_AGE:
            raise ValueError('expired_signal')
        parsed = evidence.parse(correction, row['observed_at']) if correction else json.loads(row['parsed_json'] or 'null')
        if not parsed:
            raise ValueError('complete_correction_required')
    with db:
        updated = db.execute("update candidates set status=?,reviewed_by=?,reviewed_at=?,correction_json=? where id=? and status='needs_review'", ('ready' if action == 'approve' else 'rejected', actor, now.isoformat(), dump(parsed) if parsed else None, row['id']))
        if updated.rowcount != 1:
            raise ValueError('candidate_not_reviewable')
        db.execute('insert into reviews(candidate_id,actor,at,action,correction_json) values(?,?,?,?,?)', (row['id'], actor, now.isoformat(), action, dump(parsed) if parsed else None))
        review_incident(db, now)
    return {'status': 'queued_for_fresh_validation' if parsed else 'rejected'}


def rollout(db):
    reasons = []
    complete = None
    for row in db.execute("select distinct session_open,session_close from passes where mode='observe' and session_open is not null"):
        start, end = stamp(row[0]), stamp(row[1])
        if (end-start).total_seconds() != 23400:
            continue  # one complete regular (6.5-hour) session, not an early close
        passes = list(db.execute("select * from passes where mode='observe' and julianday(at)>=julianday(?) and julianday(at)<=julianday(?) order by julianday(at)", (start.isoformat(), end.isoformat())))
        times = [start, *[stamp(p['at']) for p in passes], end]
        if passes and max((b-a).total_seconds() for a, b in zip(times, times[1:])) <= 20 and all(p['reconciled'] and p['covered'] == p['requested'] for p in passes) and sum(p['requested'] for p in passes) > 0:
            complete = row[0]
    if not complete:
        reasons.append('complete_regular_session_with_full_quote_coverage_required')
    if int(get(db, 'restart_count', 0)) < 2 or not reconcile(db):
        reasons.append('restart_reconciliation_required')
    return {'eligible': not reasons, 'reasons': reasons, 'session': complete}


def activate(db, now):
    if get(db, 'mode') == 'paper':
        return
    result = rollout(db)
    if not result['eligible']:
        raise ValueError(';'.join(result['reasons']))
    with db:
        put(db, 'mode', 'paper')
        put(db, 'activated_at', now.isoformat())
        db.execute("update candidates set status='expired',reason='activation_no_replay' where status='ready'")


def snapshot(path=DB, now=None):
    now = now or datetime.now(timezone.utc)
    if not Path(path).exists():
        return {'version': VERSION, 'mode': 'not_initialized', 'positions': [], 'candidates': [], 'available_cash': None, 'realized_pnl': None}
    db = sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        positions = [dict(r) for r in db.execute('select * from positions order by id desc')]
        for p in positions:
            p['quote_age_seconds'] = (now-stamp(p['quote_at'])).total_seconds() if p['quote_at'] else None
            if p['status'] == 'OPEN' and (p['quote_age_seconds'] is None or p['quote_age_seconds'] > 15):
                p['mark_pnl'] = None
                p['mark_error'] = p['mark_error'] or 'stale_quote'
        opens = [p for p in positions if p['status'] == 'OPEN']
        last = get(db, 'last_tick')
        latest_pass = db.execute('select * from passes order by julianday(at) desc limit 1').fetchone()
        coverage = dict(db.execute('select count(*) as passes, coalesce(sum(requested),0) as requested, coalesce(sum(covered),0) as covered from passes').fetchone())
        data_health = 'no_quote_evidence'
        if latest_pass and latest_pass['requested']:
            data_health = 'current' if latest_pass['covered'] == latest_pass['requested'] else 'unresolved'
        elif latest_pass and (not latest_pass['session_open'] or not stamp(latest_pass['session_open']) <= now < stamp(latest_pass['session_close'])):
            data_health = 'outside_session'
        if any(p['mark_error'] for p in opens) or db.execute("select 1 from incidents where active=1 and key in ('session_unavailable','quote_coverage_incomplete')").fetchone():
            data_health = 'unresolved'
        elif data_health == 'current' and (not last or not 0 <= (now-stamp(last)).total_seconds() <= 30):
            data_health = 'stale'
        poll = get(db, 'last_ingestion_poll')
        ingestion_health = 'error' if get(db, 'ingestion_error') else 'current' if poll and 0 <= (now-stamp(poll)).total_seconds() <= 45 else 'stopped_or_stale'
        review_counts = {r[0]: r[1] for r in db.execute("select reason,count(*) from candidates where status='needs_review' group by reason")}
        return {'version': VERSION, 'mode': get(db, 'mode'), 'available_cash': round(available(db), 2),
                'ingestion_health': ingestion_health, 'last_ingestion_poll': poll,
                'ingestion_error': get(db, 'ingestion_error') or None,
                'review_pending': sum(review_counts.values()), 'review_reasons': review_counts,
                'open_exposure': sum(p['collateral'] for p in opens), 'pending_exits': sum(bool(p['pending_reason']) for p in opens),
                'realized_pnl': round(sum(p['pnl'] or 0 for p in positions if p['status'] == 'CLOSED'), 2),
                'unresolved_pnl': sum(p['mark_pnl'] is None for p in opens),
                'execution_health': 'current' if last and (now-stamp(last)).total_seconds() <= 30 else 'stopped_or_stale',
                'data_health': data_health, 'quote_coverage': coverage,
                'notification_health': 'retrying' if db.execute('select 1 from events where delivered_at is null and attempts>0').fetchone() else 'pending' if db.execute('select 1 from events where delivered_at is null').fetchone() else 'current',
                'last_tick': last, 'positions': positions[:100], 'candidates': [dict(r) for r in db.execute("select id,observed_at,body,media_json,status,reason from candidates where status='needs_review' order by id desc limit 100")],
                'rollout': rollout(db), 'external_order_capability': False}
    finally:
        db.close()


def dashboard():
    from core.telegram_paper import DB as LEGACY_DB
    quote = snapshot()
    # Message text and OCR are only returned by the authenticated review route.
    quote.pop('candidates', None)
    historical = {'version': 'theta-signal-v1', 'realized_pnl': None, 'positions': [], 'pnl_source': 'unvalidated_signal_prices'}
    if LEGACY_DB.exists():
        db = sqlite3.connect(f'file:{LEGACY_DB.resolve()}?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            historical['positions'] = [dict(r) for r in db.execute('select id,symbol,expiration,status,entry_price,realized_pnl,exit_reason from positions order by id desc')]
            historical['reported_estimated_pnl'] = sum(p['realized_pnl'] or 0 for p in historical['positions'])
            historical['unknown_closed_pnl'] = sum(p['status'] == 'CLOSED' and p['realized_pnl'] is None for p in historical['positions'])
        finally:
            db.close()
    return {'historical': historical, 'quote_based': quote}
