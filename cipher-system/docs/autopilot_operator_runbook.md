# Paper Autopilot operator runbook

Cipher's autopilot is a staged, shadow-only workflow:

1. At 08:45 and 09:15 ET it scans the liquid foundation universe plus delayed
   Finviz discovery. It writes a watch plan; it cannot open a position.
2. From 09:35 through 11:30 ET it rescans only planned names. A fresh OPRA-backed,
   sufficient-coverage, `triggered` Flash Agentic card in the same direction is
   required before submission to the local simulator.
3. The executor selects a 1–3 DTE option, simulates the entry at ask plus
   slippage, marks liquidation at bid, and exits on the underlying target or
   invalidation, +20% / -15% option P&L, 45 minutes, or 15:45 ET.
4. The post-close job exports point-in-time features and later outcomes. Training
   stays blocked until at least 100 replayable outcomes across 20 market dates
   provide a chronological, embargoed holdout.

FinBERT is advisory context only. FinGPT is not enabled. Neither a language
model nor a future custom ranker can authorize a live order.

The optional local sentiment environment is declared in
`requirements-sentiment.txt`. `cipher-finbert-context.timer` refreshes public
SEC/Yahoo documents before planning and runs the revision-pinned
`ProsusAI/finbert` model locally on CPU. If the model or sources are unavailable,
the plan records sentiment as stale/unavailable; price/structure rules do not
silently substitute a score.

## Safety controls

- Executor binds only to `127.0.0.1:8787`.
- Market data comes through Cipher core's Alpaca SIP/OPRA read-only endpoints.
- Indicative option fallback blocks an entry.
- Maximum 3 open positions, 1 per ticker, 5 new positions/day, 2 stopped
  positions/day, 1 contract/position, and $500 maximum option cost.
- Premarket entries and overnight positions are disabled.
- Creating `runtime/data/paper_runtime/STOP_PAPER_EXECUTOR` blocks new entries.
- The active package has no broker client or order submission endpoint.

## Repository units

- `cipher-paper-autopilot-executor.service`
- `cipher-paper-autopilot.service` / `.timer`
- `cipher-paper-autopilot-training.service` / `.timer`
- `cipher-finbert-context.service` / `.timer`

Install these only on the private Cipher host after the core and OPRA health
checks pass. The status is visible at `/api/autopilot-status` and on Morning
Brief. `executor offline` is a hard failure, not a signal to bypass controls.
