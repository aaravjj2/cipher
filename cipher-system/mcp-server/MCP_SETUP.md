# MCP Servers — current setup (Claude Code)

Three servers are registered in the repo-root `.mcp.json`. None autostarts;
enable per session with `claude mcp` or by launching Claude Code inside this
repository and selecting them.

| Server | Command | Purpose | Can trade? |
|---|---|---|---|
| `cipher-market` | `cipher-system/mcp-server/market_server.py` | Read-only research: quotes, GEX levels, night vision, strategies, paper-autopilot status/ledger/decision-quality/prospective log | No |
| `alpaca` | `scripts/alpaca_paper_mcp.sh` | Alpaca official MCP server, **paper-locked**, toolsets without order capability | No |
| `alpaca-trading` | same wrapper + trading toolset | Adds order/position tools for the hackathon agent. Still paper-locked (`ALPACA_PAPER_TRADE=true` forced; PK-prefix keys only) | Paper only |

## Credentials

`scripts/alpaca_paper_mcp.sh` reads exactly two lines from
`/home/aarav/Aarav/cipher/runtime/config/alpaca-paper.env` (mode 0600):

```
ALPACA_API_KEY=PK...
ALPACA_SECRET_KEY=...
```

A missing file, missing key, or a key without the paper `PK` prefix fails
closed — the server refuses to start rather than silently widening scope.

## Windows/Codex legacy

The historical Codex registration (removed from this file but preserved in
git history): a project-scoped `cipher_research` STDIO server running
`run.py` from the `mcp-server` directory, configured in `.codex/config.toml`.
That host used only local SQLite and Markdown exports; the servers above
supersede it on Linux.
