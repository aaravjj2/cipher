"""Discord adapter for Cipher Copilot. Dormant unless DISCORD_BOT_TOKEN is set.

@mention the bot (or DM it) with any financial question; it runs the same
engine as the CLI and replies with chunked markdown. Per-user daily cap keeps a
noisy channel from draining provider balances.

Run:  /home/aarav/.venvs/cipher/bin/python -m core.copilot.discord_bot   (long-lived)
Needs `discord.py>=2.3` in the venv (`pip install discord.py`) - imported lazily
so every other copilot surface works without it installed.
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
from datetime import datetime, timezone

DISCORD_MESSAGE_LIMIT = 1900  # reply budget below Discord's 2000-char ceiling
PER_USER_DAILY_LIMIT_DEFAULT = 20

_MENTION_RE = re.compile(r"<@!?\d+>")


def chunk_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Splits markdown on blank lines (never mid-table-row) so chunks render;
    falls back to hard slices for a single oversized block."""
    if len(text) <= limit:
        return [text]
    blocks = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        chunks.append(current)
    return chunks


class UserBudget:
    """In-memory per-user daily counter; resets naturally each UTC day and on
    restart, which is acceptable for a politeness cap rather than billing."""

    def __init__(self, limit: int):
        self.limit = limit
        self._lock = threading.Lock()
        self._day: str = ""
        self._counts: dict[int, int] = {}

    def check(self, user_id: int) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            if self._day != today:
                self._day, self._counts = today, {}
            count = self._counts.get(user_id, 0)
            if count >= self.limit:
                return False
            self._counts[user_id] = count + 1
            return True


def extract_question(content: str | None) -> str | None:
    """The mention-stripped question, or None when nothing remains to ask."""
    text = _MENTION_RE.sub("", content or "").strip()
    return text[:8000] if text else None


def format_reply(result: dict) -> str:
    answer = (result.get("answer") or "").strip() or "No answer was produced."
    caveats = result.get("caveats") or []
    if caveats:
        joined = "\n".join(f"> {c}" for c in caveats[:3])
        answer = f"{answer}\n\n{joined}"
    return answer


def run() -> int:
    from core.copilot.tools import env_key

    token = env_key("DISCORD_BOT_TOKEN")
    if not token:
        print(
            "DISCORD_BOT_TOKEN is not set.\n"
            "1. Create an application at https://discord.com/developers/applications\n"
            "2. Bot tab: enable MESSAGE CONTENT INTENT\n"
            "3. OAuth2 URL generator: scopes=bot, permissions=Send Messages + Read Message History\n"
            "4. Invite it to your server, then put DISCORD_BOT_TOKEN=... in cipher-system/.env",
            file=sys.stderr,
        )
        return 2

    try:
        import discord
    except ImportError:
        print(
            "discord.py is not installed: run `/home/aarav/.venvs/cipher/bin/pip install 'discord.py>=2.3'`",
            file=sys.stderr,
        )
        return 2

    from core.copilot import engine

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    limit = engine._env_int("CIPHER_COPILOT_DISCORD_PER_USER_LIMIT", PER_USER_DAILY_LIMIT_DEFAULT)
    budget = UserBudget(limit)

    @client.event
    async def on_ready():
        print(f"cipher-copilot discord bot online as {client.user}", flush=True)

    @client.event
    async def on_message(message):
        if message.author.bot or client.user is None:
            return
        mentioned = client.user in getattr(message, "mentions", [])
        is_dm = message.guild is None
        if not mentioned and not is_dm:
            return
        question = extract_question(message.content)
        if not question:
            return
        if not budget.check(message.author.id):
            await message.reply(f"Daily limit reached ({limit} questions/day). Try again tomorrow.")
            return

        rows = [m async for m in message.channel.history(limit=8, before=message)]
        history: list[dict] = []
        for msg in reversed(rows):
            if msg.author.bot or not msg.content:
                continue
            role = "assistant" if msg.author.id == client.user.id else "user"
            content = extract_question(msg.content) or ""
            if content:
                history.append({"role": role, "content": content})
        history = history[-6:]
        result = await asyncio.to_thread(engine.run_query, question, history)
        for chunk in chunk_message(format_reply(result)):
            await message.reply(chunk[:2000])

    try:
        client.run(token)
    except Exception as exc:  # noqa: BLE001 - login failures must be legible
        print(f"discord bot failed to start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
