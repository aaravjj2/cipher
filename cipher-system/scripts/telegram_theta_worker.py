#!/usr/bin/env python3
"""Ten-second Theta Telegram -> isolated Cipher local-paper worker."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import theta_portfolio as portfolio  # noqa: E402
from core.theta_market_data import Provider  # noqa: E402
from scripts.telegram_theta_reader import BOT, CONFIG, MEDIA_DIR, SESSION_DIR, load_env  # noqa: E402
from telethon import TelegramClient  # noqa: E402

STOP = False


def format_notice(result, source):
    action = result['action']
    icon = {'opened': '🟢', 'closed': '🔴', 'blocked': '🛑', 'exit_pending': '⚠️'}[action]
    lines = [f"{icon} **Theta local paper — {action.upper().replace('_', ' ')}**",
             f"Message {result['message_id']} · {result.get('symbol') or 'unmatched position'}"]
    if action == 'opened':
        lines.append(f"{result['legs']} legs · quantity 1 · reported entry ${result['entry_price']:.2f}")
    elif action == 'closed':
        lines.append(f"Quantity 1 · reported estimated P&L ${result['pnl']:+.2f} · {result['reason']}")
    elif action == 'exit_pending':
        lines.append('Position remains open: no executable exit price available. Close request recorded.')
    else:
        lines.append(f"Why: {result.get('explanation') or result.get('reason')}")
        lines.append(f"Source: {source[:400] or 'image-only / no text'}")
    lines.append('Paper simulation only; no broker orders.')
    return '\n'.join(lines)


def discord(text: str) -> None:
    webhook = os.environ.get("DISCORD_PROGRESS_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError('Discord webhook is not configured')
    request = urllib.request.Request(webhook, data=json.dumps({"content": text[:1900]}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Cipher-Theta-Paper/1.0"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Discord HTTP {response.status}")


def ingest_row(row):
    db = portfolio.connect()
    try:
        return portfolio.ingest(db, row, datetime.now(timezone.utc))
    finally:
        db.close()


def monitor(provider):
    db = portfolio.connect()
    try:
        portfolio.tick(db, provider)
    finally:
        db.close()


def deliver():
    db = portfolio.connect()
    try:
        # Delivery is at-least-once: the stable event ID remains in the message
        # if a process dies after Discord accepts it but before SQLite commits.
        for pending in db.execute('select * from events where delivered_at is null order by created_at limit 20').fetchall():
            try:
                payload = json.loads(pending['payload'])
                discord('Theta Cipher paper · ' + payload['action'] + '\n' + json.dumps(payload, ensure_ascii=False)[:1500] + '\nEvent: ' + pending['event_id'])
                with db:
                    db.execute('update events set delivered_at=?,attempts=attempts+1,last_error=null where event_id=?', (datetime.now(timezone.utc).isoformat(), pending['event_id']))
            except Exception as exc:
                with db:
                    db.execute('update events set attempts=attempts+1,last_error=? where event_id=?', (type(exc).__name__, pending['event_id']))
                break
    finally:
        db.close()


async def periodic(fn):
    while not STOP:
        started = time.monotonic()
        try:
            await asyncio.to_thread(fn)
        except Exception as exc:
            print(json.dumps({'component': fn.__name__, 'error': type(exc).__name__}), flush=True)
        await asyncio.sleep(max(.1, 10 - (time.monotonic() - started)))


async def run(once=False, initialize=False):
    env = load_env(CONFIG)
    SESSION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = TelegramClient(str(SESSION_DIR / "theta_readonly"), int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"])
    db = portfolio.connect()
    provider = Provider()
    tasks = [] if once else [asyncio.create_task(periodic(lambda: monitor(provider))), asyncio.create_task(periodic(deliver))]
    try:
        entity = None
        while entity is None and not STOP:
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise RuntimeError('telegram_authorization_required')
                entity = await client.get_entity(BOT)
            except Exception as exc:
                print(json.dumps({'telegram_startup_error': type(exc).__name__}), flush=True)
                if once:
                    raise
                await asyncio.sleep(10)
        if entity is None:
            return
        if portfolio.get(db, 'cursor') is None:
            latest = await client.get_messages(entity, limit=1)
            portfolio.initialize(db, latest[0].id if latest else 0, datetime.now(timezone.utc))
        else:
            portfolio.initialize(db, int(portfolio.get(db, 'cursor')), datetime.now(timezone.utc))
        while not STOP:
            after = int(portfolio.get(db, 'cursor', 0))
            try:
                messages = [m async for m in client.iter_messages(entity, min_id=after, reverse=True, limit=100)]
            except Exception as exc:
                print(json.dumps({'telegram_error': type(exc).__name__}), flush=True)
                if once:
                    raise
                await asyncio.sleep(10)
                continue
            for message in messages:
                media = None
                if message.media:
                    target = MEDIA_DIR / str(message.id)
                    try:
                        saved = await message.download_media(file=str(target))
                        if saved:
                            Path(saved).chmod(0o600)
                            media = saved
                    except Exception as exc:
                        media = f'unavailable:{type(exc).__name__}'
                        print(json.dumps({'media_error': type(exc).__name__, 'message_id': message.id}), flush=True)
                row = {"id": message.id, "date": message.date.isoformat(), "text": message.raw_text or "", "media_path": media, "reply_to_message_id": message.reply_to_msg_id}
                result = await asyncio.to_thread(ingest_row, row)
                print(json.dumps(result), flush=True)
            # Durable outbox: a transport failure cannot silently lose an alert
            # after the Telegram cursor advances. Historical messages are not backfilled.
            if once:
                await asyncio.to_thread(monitor, provider)
                await asyncio.to_thread(deliver)
                break
            await asyncio.sleep(10)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        db.close()
        await client.disconnect()


def main():
    global STOP
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__('STOP', True))
    once = "--once" in sys.argv
    initialize = "--initialize" in sys.argv
    asyncio.run(run(once=once, initialize=initialize))


if __name__ == "__main__":
    main()
