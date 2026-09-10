#!/usr/bin/env python3
"""Read-only importer for trade messages sent directly by @ThetaWireMid_bot.

First run requires interactive Telegram authorization in this terminal. The
session is stored outside Git; this script never sends messages or marks them read.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient

CONFIG = Path(os.environ.get("CIPHER_TELEGRAM_ENV", "/home/aarav/Aarav/cipher/runtime/config/telegram/app.env"))
SESSION_DIR = Path(os.environ.get("CIPHER_TELEGRAM_SESSION_DIR", "/home/aarav/Aarav/cipher/runtime/config/telegram/sessions"))
MEDIA_DIR = Path(os.environ.get("CIPHER_TELEGRAM_MEDIA_DIR", "/home/aarav/Aarav/cipher/runtime/data/telegram/media"))
BOT = "ThetaWireMid_bot"


def notify_discord(message: str, webhook: str) -> None:
    body = json.dumps({"content": message[:1900]}).encode()
    request = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json", "User-Agent": "Cipher-Telegram-Paper/1.0"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Discord returned HTTP {response.status}")


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


async def read_messages(limit: int, output: Path | None, notify: bool = False, download_media: bool = True) -> int:
    env = load_env(CONFIG)
    api_id = int(env["TELEGRAM_API_ID"])
    api_hash = env["TELEGRAM_API_HASH"]
    SESSION_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if download_media:
        MEDIA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = TelegramClient(str(SESSION_DIR / "theta_readonly"), api_id, api_hash)
    await client.start()  # prompts locally for phone/code/2FA on first run
    try:
        entity = await client.get_entity(BOT)
        rows = []
        async for message in client.iter_messages(entity, limit=max(1, min(limit, 5000)), wait_time=0):
            media_path = None
            if download_media and message.media:
                try:
                    saved = await message.download_media(file=str(MEDIA_DIR / str(message.id)))
                    if saved:
                        media_path = str(Path(saved))
                        Path(saved).chmod(0o600)
                except Exception as exc:  # attachment failure must not lose the message
                    media_path = f"unavailable:{type(exc).__name__}"
            rows.append({
                "id": message.id,
                "date": message.date.astimezone(timezone.utc).isoformat() if message.date else None,
                "text": message.raw_text or "",
                "has_media": bool(message.media),
                "media_path": media_path,
                "reply_to_message_id": message.reply_to_msg_id,
            })
        rows.sort(key=lambda row: (row['date'] or '', row['id']))
        payload = {"source": f"telegram:{BOT}", "fetched_at": datetime.now(timezone.utc).isoformat(), "count": len(rows), "messages": rows}
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded + "\n", encoding="utf-8")
            output.chmod(0o600)
            print(f"wrote {len(rows)} messages to {output}")
        else:
            print(encoded)
        if notify:
            webhook = os.environ.get("DISCORD_PROGRESS_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK_URL")
            if not webhook:
                raise RuntimeError("Discord webhook is not configured")
            notify_discord(f"Theta Telegram import: fetched {len(rows)} messages from @{BOT}; paper execution remains local-only.", webhook)
        return 0
    finally:
        await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("/home/aarav/Aarav/cipher/runtime/data/telegram/theta_trades.json"))
    parser.add_argument("--notify-discord", action="store_true", help="send a fetch summary to the configured Cipher Discord webhook")
    parser.add_argument("--no-media", action="store_true", help="do not download attached media")
    args = parser.parse_args()
    return asyncio.run(read_messages(args.limit, args.output, args.notify_discord, not args.no_media))


if __name__ == "__main__":
    raise SystemExit(main())
