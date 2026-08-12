This directory holds **two** MCP servers, for different jobs:

| file | what it does | needs cipher-core? |
|---|---|---|
| `market_server.py` | **Cipher Market MCP** — read-only live analysis: quotes, bars, GEX walls and flip level, Night Vision levels, option-contract tape, headlines, strategy standing | yes, `127.0.0.1:8282` |
| `server.py` | Cipher Research MCP — records a browser research workflow into local SQLite | no |

---

# Cipher Market MCP

Point an MCP host at this to analyse tickers directly against Cipher's live data.

## Attach to Claude Desktop

Add to `claude_desktop_config.json` (macOS
`~/Library/Application Support/Claude/claude_desktop_config.json`, Windows
`%APPDATA%\Claude\claude_desktop_config.json`), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "cipher-market": {
      "command": "python3",
      "args": ["/home/aarav/Aarav/cipher/cipher-github/cipher-system/mcp-server/market_server.py"],
      "env": { "CIPHER_CORE_URL": "http://127.0.0.1:8282" }
    }
  }
}
```

The host must be on the same machine as cipher-core, or reach it over Tailscale — set
`CIPHER_CORE_URL` to the tailnet address in that case. Standard library only, so no
install step and no virtualenv.

## Tools

| tool | returns |
|---|---|
| `cipher_health` | service reachability and configured feeds |
| `get_quote` | bid/ask/mid/last, prior close, day change (SIP) |
| `get_bars` | OHLCV, any timeframe, up to 500 bars |
| `get_gex_levels` | call wall, put wall, gamma flip, net GEX, per-strike totals near spot |
| `get_night_vision` | quote, walls, peak exposure, levels above/below spot, PDH/PDL/PWH/PWL and pre/post-market extremes |
| `search_contract` | every trade in one contract for one session, bought vs sold, VWAP, OI, largest prints |
| `get_news_headlines` | Yahoo Finance RSS headlines, unscored |
| `list_strategies` | researched strategies and the control standard each must beat |
| `get_research_standing` | forward-test registrations and sample progress |

There is also an `analyze_ticker` prompt that walks the evidence in a fixed order.

## Two things it guarantees

**Read-only.** `_get` issues HTTP GET only, against a fixed path allowlist that contains no
broker, account, holdings or order endpoint. cipher-core's POST routes are unreachable from
this file, and no tool can place, size, modify or cancel an order.

**Results that fit a context.** `/api/night-vision` and `/api/matrix` are ~730 KB each —
about 180k tokens, enough to exhaust a host's context in one call. The big endpoints are
projected down to Cipher's own computed levels: 730 KB → ~9 KB, 200 KB → ~4 KB. A hard
120 KB cap catches anything unexpected.

Data caveats are forwarded rather than stripped. An unavailable exposure cell means "no
listed or calculable exposure", not zero — it is skipped rather than summed, and
`cell_coverage` reports how many cells were unknown, so unknown is never read as empty.

## Tests

    ../.venv-research-py312/bin/python -m pytest mcp-server/test_market_server.py -q

22 tests, driving the real stdio protocol in a subprocess. The read-only assertions run
without a network; live-data tests skip when cipher-core is stopped.

## ChatGPT — the remote bridge

ChatGPT cannot launch a local process; it only connects outward to an HTTPS MCP endpoint.
`remote_bridge.py` serves the *same* server over Streamable HTTP, importing `handle()`,
`tool_specs()` and `handle_tool()` from `market_server.py` rather than reimplementing them,
so the read-only allowlist and the payload projections cannot drift between transports.

**Live at** `https://cipher-main.tail39504f.ts.net:10000/mcp` (Tailscale Funnel → 127.0.0.1:8284).

Add it in ChatGPT under Settings → Connectors → Add custom connector:

| field | value |
|---|---|
| URL | `https://cipher-main.tail39504f.ts.net:10000/mcp` |
| Authentication | Bearer token / custom header |
| Header | `Authorization: Bearer <token>` |

Read the token with `cat runtime/config/app-password.txt`'s neighbour:

```bash
cat /home/aarav/Aarav/cipher/runtime/config/mcp-bearer-token.txt
```

It is stored there, and deliberately **not** in `/etc/cipher/cipher.env`: that file is
rebuilt from scratch by `sync-secrets.py` and lost every credential during the 2026-08-12
reboot, while `runtime/config/` secrets survived it.

`search` and `fetch` exist alongside the specific tools because ChatGPT's deep-research mode
looks for that pair by name and will not drive arbitrary tools. `search` resolves a ticker or
a company name against Cipher's covered universe and never invents a symbol; `fetch` returns
one ticker's quote, gamma levels, session levels and headlines.

### Operating it

```bash
sudo systemctl status cipher-mcp-bridge.service
curl -s https://cipher-main.tail39504f.ts.net:10000/health     # no token needed, reveals nothing
sudo tailscale funnel --https=10000 off                        # take it off the internet
```

### What the gate does and does not do

Every `/mcp` request needs `Authorization: Bearer <token>`, compared with
`hmac.compare_digest` so a wrong token cannot be recovered from response timing. A missing or
empty token file makes the bridge refuse **every** request rather than serve openly — absent
configuration disables the service, never the gate, which is the lesson from
`CIPHER_APP_AUTH=off`. `/health` is the one unauthenticated route and returns only liveness.

The bridge binds to 127.0.0.1; Funnel is the only path in. It stays read-only: no mutating
cipher-core route is in the allowlist and no tool can place, size, modify or cancel an order.

There is one thing the token does not do: it is a single shared secret with no per-caller
identity, no expiry and no rate limit. Anyone holding it has full read access to your market
data. Rotate it by writing a new value and restarting:

```bash
openssl rand -hex 32 > /home/aarav/Aarav/cipher/runtime/config/mcp-bearer-token.txt
sudo systemctl restart cipher-mcp-bridge.service
```

### A bug worth remembering

`_authorized()` originally answered 401 without reading the request body. Under HTTP/1.1
keep-alive the unread body stayed in the socket buffer, so the next request on that
connection was parsed starting mid-JSON — request lines like `{"jsonrpc":...}POST /mcp`,
answered 501. It only appeared through Funnel, which reuses connections: the public endpoint
returned 401 and 501 alternately for the same wrong token while localhost was always 401.
Every early return now drains the body or closes the connection, and
`test_a_rejected_post_does_not_desync_the_next_request_on_the_same_connection` pipelines two
requests down one socket to hold the property.

---

# Cipher Research MCP

Browser-first options research workflow for an authorized Cipher browser session. The server keeps evidence, screenshots, option-chain snapshots, and neutral research reports locally. It never submits orders, changes broker settings, or returns secret values.

## Start

Open the project in Codex, then start the configured MCP server. The launcher is:

    C:\Aarav\cipher-system\mcp-server\run.py

It loads the local market-data configuration from the existing app environment without exposing the credentials.

## Daily sequence

1. Start a run with start_daily_workflow.
2. Use Browser or Computer to collect visible Cipher evidence: Strike Matrix, Night Vision/X-Ray, flow, and Setup Scanner.
3. Save each visible result with record_observation and attach_screenshot.
4. Pull approved Alpaca data with pull_option_chain. Select OPRA only when the Alpaca entitlement supports it; otherwise the stored feed label records indicative data.
5. Run score_option_liquidity, detect_unusual_flow, rank_research_candidates, and compare_scan_to_previous.
6. Export a ticker brief or daily report.

## Research tools

- pull_option_chain: bid, ask, mid, last, volume, open interest when supplied, IV, Greeks, and timestamps.
- score_option_liquidity: spread, volume, open interest, and quote-quality screening.
- detect_unusual_flow: volume/OI and estimated premium screening. Quote side is explicitly inferred, not verified.
- rank_research_candidates: prioritizes items for further research using data and saved Cipher evidence. It is not advice.
- compare_scan_to_previous: observes changes between locally saved snapshots.
- record_outcome: preserves later observations for calibration, not performance claims.

SQLite data is under data. Reports are under exports. The database and reports remain local to this project.
