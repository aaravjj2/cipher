# Cipher Copilot — Design

**Date:** 2026-08-25
**Status:** Implemented (`cipher-system/core/copilot/`)
**Surfaces:** CLI (`python -m core.copilot`), editor skill (`cipher-copilot`), Discord bot (token-gated)

## Problem

Cipher holds rich captured market data (option chains every 5 minutes, GEX
history, Tradier flow tape, trained earnings models, paper portfolio, journal)
but answering a financial question requires knowing which store, module, or UI
surface holds the evidence. Ask Cipher covers browser chat only, with a narrow
tool set. The copilot makes the whole evidence base answer questions directly:
one engine, any surface.

## Architecture

```
question ──► adapters (cli.py / discord_bot.py / editor skill → cli)
                 │
            engine.py  ── grounded tool loop (≤6 rounds)
                 │           providers.py: groq → anthropic → openrouter → deterministic mode
                 ▼
            tools.py ──► local stores + core/app.py fetchers
              ├─ runtime/data/live_option_chains/*.jsonl   (5-min OPRA captures)
              ├─ runtime/data/gex_history.sqlite           (GEX/VEX time series)
              ├─ runtime/data/option_history.sqlite        (IV term structure/skew)
              ├─ tradier_flow.py → tradier stream tape     (when captured)
              ├─ scanner.analyze_ticker(app.matrix, …)     (real setup scoring)
              ├─ earnings_model (joblib artifacts + SQLite)
              ├─ paper_portfolio_api / trader_journal / watchlists
              ├─ market_research_agent.latest()
              └─ app.quote/app.bars (live Alpaca) + RSS headlines
```

Key decisions:

1. **Reuse over reimplementation.** Quotes/bars/matrix go through `core/app.py`
   functions (credential handling, caching, feed resolution already proven);
   scanner runs via `analyze_ticker` against `app.matrix`. No new market-data code.
2. **Capture-first honesty.** GEX fallback derives from the newest local chain
   capture using the canonical formula; every result carries `source` + `as_of`
   so staleness is disclosed rather than hidden.
3. **Provider chain** mirrors ask_cipher.py's failover rules: skip a provider
   only before anything was emitted; never splice models mid-answer.
4. **One shared daily budget** (`data/copilot/usage.json`, default 200) across
   CLI and Discord; Discord adds a per-user cap (default 20).
5. **Deterministic degradation.** With no provider keys, the engine returns raw
   evidence (quote + GEX walls for tickers found in the question), labelled as
   such — never a silent failure.

## Guardrails

- Read-only research: the package submits, amends, and cancels nothing. The
  `account.py` adapter performs GET requests against the Alpaca PAPER API only
  (positions/orders/account/FIFO trade history); it shares no code path with
  the order-submitting paper broker.
- System prompt hard rules: numbers only from tool results this turn;
  `[knowledge]` prefix on general-finance education; describe-don't-dictate
  (no buy/sell directives); GEX = OI heuristic caveat preserved.
- Secrets stay server-side in `runtime/config/cipher.env`; `DISCORD_BOT_TOKEN`
  absence leaves the bot dormant with setup instructions.
- Provider-aware tool budgets (Groq's 8k TPM ceiling gets 3.5k-char tool
  results) so long histories never trip HTTP 413.

## Account awareness (added 2026-08-26)

`core/copilot/account.py` + four registry tools (`get_account`,
`get_positions`, `get_orders`, `get_trade_history`). Trade history pairs fills
FIFO per symbol, reports per-unit AND dollar P&L (OCC option symbols = x100),
open remainders, win rate. All broker reads route through the sanctioned
`alpaca_paper_broker` adapter (research-only guard stays intact). The engine
prompt routes "my position/P&L/entries" questions to these tools first.

## Later increments (2026-08-26, same day)

- Registry grew to **20 tools**: `get_technicals`, `get_exposure_term_structure`
  (GEX/VEX/OI by DTE bucket), `scan_strategies` (cipher+flash+cluster
  consensus), `get_earnings_dates` (live provider calendar - the local store
  only holds reported events).
- IV term structure computes ATM IV + 25-delta skew from chain captures;
  expiries under 2 DTE are dropped (degenerate quotes).
- Engine dispatches batched tool calls concurrently (ThreadPoolExecutor,
  max 4) with order/id-preserving replay.
- Capture universe extended to 14 tickers (+AMKR, +RMBS) so held names accrue
  gamma history; earnings DB collected for both.
- `scripts/copilot_daily_digest.py`: nightly deterministic Discord digest with
  **stateful regime-change alerts** (gamma-flip crossings, trend flips vs the
  previous run's saved baseline), watchlist-driven ticker coverage. Cron
  17:40 ET weekdays. Named User-Agent required (Discord 403s python default).

## Testing

32 tests in `cipher-system/tests/test_copilot_*.py`: capture parsing,
canonical-GEX math, wall/flip derivation, gamma-history aggregation, bounded
JSON, dispatch error-as-data contract, engine loop/fallback/cap/history bounds,
deterministic mode, CLI exit codes, Discord chunking/budgets, and the OpenAI
wire-format regression (replayed assistant tool_calls must carry
`type:"function"` — Groq rejects otherwise).

## Future

MCP exposure of the same registry; streaming into the web UI; scheduled
digest pushes through the existing Hermes/Discord plumbing.
