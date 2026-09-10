"""Deterministic local-paper ledger for Theta Telegram option alerts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
import re
import sqlite3
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

DB = Path("/home/aarav/Aarav/cipher/runtime/data/telegram/theta_paper.sqlite")
LEGACY_VERSION = "theta-signal-v1"
QUOTE_VERSION = "theta-quote-v1"
OCR_VERSION = "local-ocr-v1"


def extract_local_ocr(media_path: str) -> tuple[str, str]:
    """Extract text on-host; intentionally has no network/model fallback."""
    try:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(media_path)).strip()
        return text, OCR_VERSION
    except Exception as exc:
        raise RuntimeError("local OCR unavailable") from exc


@dataclass(frozen=True)
class Leg:
    side: str
    strike: float
    kind: str


def _source_text(text: str) -> str:
    match = re.search(r"(?im)^Text:\s*(.+)$", text)
    return (match.group(1) if match else text).strip()


def _source_reason(text: str) -> str | None:
    match = re.search(r"(?im)^Reasoning:\s*(.+)$", text)
    return match.group(1).strip() if match else None


def _price(text: str) -> tuple[float | None, str | None]:
    patterns = [
        (r"collect(?:ing)?\s*\$?([\d.]+)", "credit"),
        (r"(?:\bat\b|for)\s*\$?([\d.]+)(?!\s*/)", "debit"),
    ]
    for pattern, style in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = float(match.group(1))
            # Alert convention sometimes writes contract dollars ($520) and
            # sometimes option points ($1.35). Normalize to option points.
            if value > 20:
                value /= 100
            return value, style
    return None, None


def parse_entry(message_id: int, timestamp: str, body: str) -> dict | None:
    text = _source_text(body)
    if re.search(r"\b(take gains|taking gains|trim|close)\b", text, re.I):
        return None
    symbol_match = re.search(r"\b(SPX|SPY|QQQ|RUT|IWM|TSLA|USO|IONQ)\b", text.upper())
    if not symbol_match:
        symbol_match = re.search(r"\b([A-Z]{1,5})\s+\d+(?:\.\d+)?\s*[CP]\b", text.upper())
    symbol = symbol_match.group(1) if symbol_match else None
    legs = []
    for side, strike, kind in re.findall(r"\b(Sell|Buy)\s+(?:[A-Z]{2,5}\s+)?(\d+(?:\.\d+)?)\s*([CP])\b", text, re.I):
        legs.append(Leg(side.upper(), float(strike), kind.upper()))
    iron = re.search(r"Sell\s+(?:[A-Z]{2,5}\s+)?(\d+(?:\.\d+)?)\s*C\s*/?\s*P.*?Buy\s+(\d+(?:\.\d+)?)\s*P.*?buy\s+(\d+(?:\.\d+)?)\s*C", text, re.I)
    if iron:
        center, put_wing, call_wing = map(float, iron.groups())
        legs = [Leg("BUY", put_wing, "P"), Leg("SELL", center, "P"), Leg("SELL", center, "C"), Leg("BUY", call_wing, "C")]
    single = re.search(r"\b([A-Z]{2,5})\s+(\d+(?:\.\d+)?)\s*([CP])\s+(?:for today\s+)?at\s*\$?([\d.]+)", text, re.I)
    if not legs and single:
        symbol, strike, kind, raw_price = single.groups()
        legs = [Leg("BUY", float(strike), kind.upper())]
    price, price_style = _price(text)
    if single:
        price, price_style = float(single.group(4)), "debit"
    observed_day = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York')).date()
    expiration = observed_day.isoformat() if re.search(r"\btoday\b", text, re.I) else None
    explicit = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b", text)
    if explicit:
        month, day = map(int, explicit.groups()[:2])
        year = int(explicit.group(3)) if explicit.group(3) else observed_day.year
        if year < 100:
            year += 2000
        try:
            expiration = date(year, month, day).isoformat()
        except ValueError:
            return None
    if not symbol or not legs or not 1 <= len(legs) <= 4 or price is None or price <= 0 or not expiration:
        return None
    if date.fromisoformat(expiration) < observed_day:
        return None
    return {"message_id": message_id, "timestamp": timestamp, "symbol": symbol.upper(),
            "expiration": expiration, "legs": [asdict(x) for x in legs], "entry_price": price,
            "price_style": price_style, "quantity": 1, "raw_text": text}


def connect(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
      create table if not exists state(key text primary key,value text not null);
      create table if not exists messages(message_id integer primary key,observed_at text not null,text text not null,status text not null,detail text);
      create table if not exists notification_outbox(message_id integer primary key,payload text not null,source text not null,delivered_at text);
      create table if not exists attachments(message_id integer primary key,media_path text,review_status text not null);
      create table if not exists positions(
        id integer primary key autoincrement,entry_message_id integer unique,symbol text not null,expiration text not null,
        legs_json text not null,price_style text not null,entry_price real not null,quantity integer not null check(quantity=1),
        opened_at text not null,status text not null,closed_at text,exit_message_id integer,realized_pnl real,exit_reason text,
        take_profit_pct real not null default 50,stop_loss_pct real not null default 25);
    """)
    db.commit()
    # Additive migrations keep databases created by the original worker valid.
    for statement in (
        "alter table messages add column reply_to_message_id integer",
        "alter table attachments add column sha256 text",
        "alter table attachments add column extracted_text text",
        "alter table attachments add column parser_version text",
    ):
        try:
            db.execute(statement)
        except sqlite3.OperationalError:
            pass
    db.commit()
    return db


def cursor(db) -> int:
    row = db.execute("select value from state where key='cursor'").fetchone()
    return int(row[0]) if row else 0


def set_cursor(db, value: int) -> None:
    db.execute("insert into state values('cursor',?) on conflict(key) do update set value=excluded.value", (str(value),))
    db.commit()


def _close_matches(db, text: str) -> list[sqlite3.Row]:
    rows = list(db.execute("select * from positions where status='OPEN' order by opened_at desc"))
    upper = text.upper()
    symbols = {row['symbol'] for row in rows if re.search(r'\b' + re.escape(row['symbol']) + r'\b', upper)}
    if len(symbols) > 1:
        return []
    symbol = next(iter(symbols), None)
    strikes = {float(x) for x in re.findall(r"\b(\d+(?:\.\d+)?)\s*(?:CALL|PUT|[CP](?:E)?)(?![A-Z])", upper)}
    for first, second in re.findall(r'\b(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)[CP]\b', upper):
        strikes.update((float(first), float(second)))
    if not symbol and not strikes:
        return []
    contracts = {(float(strike), 'C' if kind.startswith('C') else 'P') for strike, kind in
                 re.findall(r'\b(\d+(?:\.\d+)?)\s*(CALL|PUT|CE|PE|C|P)(?![A-Z])', upper)}
    expiry = re.search(r'\b\d{4}-\d{2}-\d{2}\b', upper)
    matches = []
    for row in rows:
        legs = json.loads(row["legs_json"])
        if symbol and row["symbol"] != symbol:
            continue
        if strikes and not strikes.issubset({float(x["strike"]) for x in legs}):
            continue
        if contracts and not contracts.issubset({(float(x['strike']), x['kind']) for x in legs}):
            continue
        if expiry and expiry.group() != row['expiration']:
            continue
        matches.append(row)
    return matches


def process(db, message: dict) -> dict:
    mid, body = int(message["id"]), str(message.get("text") or "")
    # Historical signal-price accounting must never accept OCR as a new fill.
    existing = db.execute("select status,detail from messages where message_id=?", (mid,)).fetchone()
    if existing:
        return {"action": "duplicate", "message_id": mid}
    source = _source_text(body)
    administrative = bool(re.match(r'^[^\w$]*(?:commands\b|status\b|eod report\b|this month\b|all.time pnl\b|menu\b)', source, re.I)) or source.startswith('💤') or source.strip() in {'(none)', 'none', ''}
    close = bool(re.search(r"\b(take gains|taking gains|trim|close)\b", source, re.I))
    pnl_match = re.search(r"PnL(?: \(live estimate\))?:\s*([+-]?\d+(?:\.\d+)?)", body, re.I)
    result = {"action": "ignored", "message_id": mid, "reason": "not_actionable"}
    if administrative:
        result['reason'] = 'administrative_message'
        if message.get('media_path') and source.strip() in {'', '(none)', 'none'}:
            result.update(action='needs_review', reason='image_extraction_unavailable')
    elif close or (pnl_match and re.search(r'\bqty\s+1\b', body, re.I)):
        matches = _close_matches(db, source)
        if len(matches) == 1:
            row = matches[0]
            pnl = float(pnl_match.group(1)) if pnl_match else None
            threshold = None
            if pnl is not None:
                basis = row["entry_price"] * 100
                threshold = "tp_hit" if pnl >= basis * .5 else "sl_hit" if pnl <= -basis * .25 else None
            if (close or threshold or row['exit_reason'] == 'pending_signal') and pnl is not None:
                reason = threshold or "signal"
                db.execute("update positions set status='CLOSED',closed_at=?,exit_message_id=?,realized_pnl=?,exit_reason=? where id=?",
                           (message["date"], mid, pnl, reason, row["id"]))
                result = {"action": "closed", "message_id": mid, "position_id": row["id"], "symbol": row['symbol'], "quantity": 1, "reason": reason, "pnl": pnl, "pnl_source": "telegram_reported_estimate"}
            elif close:
                result = {"action": "exit_pending", "message_id": mid, "position_id": row['id'], "symbol": row['symbol'], 'reason': 'exit_price_unavailable', 'quantity': 1}
                db.execute("update positions set exit_reason='pending_signal' where id=?", (row['id'],))
            else:
                result = {"action": "marked", "message_id": mid, "position_id": row["id"], "pnl": pnl}
        else:
            result = {"action": "blocked", "message_id": mid, "reason": "close_match_ambiguous", "matches": len(matches)}
    else:
        entry = parse_entry(mid, message["date"], body)
        if entry:
            db.execute("insert into positions(entry_message_id,symbol,expiration,legs_json,price_style,entry_price,quantity,opened_at,status) values(?,?,?,?,?,?,?,?, 'OPEN')",
                       (mid, entry["symbol"], entry["expiration"], json.dumps(entry["legs"]), entry["price_style"], entry["entry_price"], 1, entry["timestamp"]))
            result = {"action": "opened", "message_id": mid, "symbol": entry["symbol"], "legs": len(entry["legs"]), "quantity": 1, "entry_price": entry["entry_price"]}
        elif "Text:" in body and ("Blocked" in body or "Market update" in body) and re.search(r'\b(?:buy|sell|call|put|\d+[CP])\b', source, re.I):
            result = {"action": "blocked", "message_id": mid,
                      "reason": "incomplete_or_unsupported",
                      "explanation": "Missing a complete option structure, expiration or entry price. One through four explicit legs are supported; upstream rejection is not our execution policy.",
                      "upstream_reason": _source_reason(body)}
    db.execute("insert into messages(message_id,observed_at,text,status,detail) values(?,?,?,?,?)", (mid, message["date"], body, result["action"], json.dumps(result)))
    if message.get('media_path'):
        media_path = str(message['media_path'])
        digest = None
        extracted = message.get('extracted_text')
        if not media_path.startswith('unavailable:'):
            try:
                digest = hashlib.sha256(Path(media_path).read_bytes()).hexdigest()
            except OSError:
                pass
        review_status = 'needs_review' if extracted else 'not_extracted'
        db.execute('insert into attachments(message_id,media_path,review_status,sha256,extracted_text,parser_version) values(?,?,?,?,?,?)',
                   (mid, media_path, review_status, digest, extracted, OCR_VERSION if extracted else None))
    if result['action'] in {'opened', 'closed', 'blocked', 'exit_pending'}:
        db.execute('insert into notification_outbox(message_id,payload,source) values(?,?,?)', (mid, json.dumps(result), source))
    db.commit()
    return result


def snapshot(db) -> dict:
    unknown = db.execute("select count(*) from positions where status='CLOSED' and realized_pnl is null").fetchone()[0]
    known_pnl = db.execute("select coalesce(sum(realized_pnl),0) from positions where status='CLOSED'").fetchone()[0]
    return {"paper_only": True, "external_order_capability": False, "ledger_version": LEGACY_VERSION, "pnl_provenance": "telegram_reported_estimate", "quantity_per_trade": 1,
            "take_profit_pct": 50, "stop_loss_pct": 25,
            "open": db.execute("select count(*) from positions where status='OPEN'").fetchone()[0],
            "closed": db.execute("select count(*) from positions where status='CLOSED'").fetchone()[0],
            "closed_with_unknown_pnl": unknown,
            "pending_unpriced_exits": db.execute("select count(*) from positions where status='OPEN' and exit_reason='pending_signal'").fetchone()[0],
            "images_needing_review": db.execute("select count(*) from messages where status='needs_review'").fetchone()[0],
            "known_realized_pnl": known_pnl,
            "realized_pnl": None if unknown else known_pnl}
