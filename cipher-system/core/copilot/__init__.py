"""Cipher Copilot — a read-only financial research copilot over Cipher's own
data stores.

One engine, three surfaces: a CLI (`python -m core.copilot "...""`), an optional
Discord bot (`discord_bot.run()` behind DISCORD_BOT_TOKEN), and an editor skill
that drives the same CLI. The engine grounds every answer in typed tool calls
against local captures (option chains, GEX history, flow tape, earnings models,
paper portfolio, journal), live Alpaca quotes/bars through `core/app.py`'s
existing fetchers, and READ-ONLY views of the Alpaca paper account
(`core/copilot/account.py`: positions, orders, FIFO trade history).

Research only. This package never submits, amends, or cancels an order on any
broker API, and never writes to any store it reads.
"""
