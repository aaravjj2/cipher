# Hackathon Submission — Cipher Options Alpha Agent

> Draft. Final numbers, demo link, and video go in before Sep 4 15:00 UTC.
> Every claim below must remain true at submission time; anything unproven is
> labelled as such. That honesty IS the pitch.

## What we built

An autonomous options trading agent on Alpaca whose distinguishing feature is
that it is **auditable end-to-end and honest about its own performance**:

- **Brain** — `cipher-market` MCP server (16 read-only tools) exposing the
  research terminal: quotes, gamma walls/flip, stored gamma regime, strategy
  standings, paper-autopilot health, ledger truth, decision-quality statistics,
  prospective prediction log.
- **Hands** — Alpaca's official MCP server behind a wrapper that forces paper
  mode (`ALPACA_PAPER_TRADE=true`, PK-prefix fail-closed) and registers order
  tools only on explicit opt-in.
- **Spine** — deterministic scripts no model can argue with:
  - `pretrade_gate.py`: PASS/BLOCKED on kill switch, session clock, portfolio
    limits, cost cap, duplicate intents.
  - `agent_decision_log.py`: append-only intent→outcome trail with per-decision
    chain integrity and a derived position book (open contracts, closed
    round-turns).
  - `agent_reconcile.py`: broker-order JSON in, RECONCILED verdict out —
    id/symbol/quantity/price checked against the chain, mismatches named.
- **Rules** — AGENT_CHARTER.md (paper-only, intent-before-submission,
  classified outcomes) and PLAYBOOK.md (the seven-step loop).
- **Mirror** — the production autopilot runs the same discipline continuously:
  every confirmation becomes a contract candidate, a classified rejection, or a
  classified data failure. Zero trades is reported as the healthy outcome when
  no setup qualifies.

## Why it matters

Most agentic-trading demos show wins and hide the funnel. Cipher's agent shows
the funnel: refusals with reasons are first-class results, performance claims
are generated from the ledger with sample-size caveats attached, and a public,
judge-visible feed serves the actual record — including blocked decisions.

## Architecture

(Insert diagram: browser/guest → Node proxy → read-only core API + MCP
servers → Alpaca paper API; scripts as deterministic authority; append-only
ledgers.)

## Live evidence to include before submission

1. Decision log excerpt: at least one FILLED chain (INTENT→…→RECONCILED) and
   one BLOCKED chain with its gate reason.
2. `decision_quality` output verbatim, caveats included.
3. Guest panel screenshot: live paper record above the illustrative card.
4. GEX capture continuity stats (542-ticker matrix, expirations=3).

## Honest limitations to state

- Paper only by construction; no live-capital path exists in this repo.
- Sample sizes are small; expectancy readings carry explicit n-gates.
- GEX is a public-open-interest heuristic, not dealer positioning.
- Historical earnings dates were single-sourced until the Aug 2026 cross-check.

## Build-with list (Alpaca stack usage)

- Trading API via official **MCP Server v2** (paper) — orders, positions.
- **Trading CLI** for scripted reconciliation checks.
- Market data through Cipher's existing OPRA/SIP core (Alpaca feeds).

## Demo script (3 minutes)

1. Guest panel: live paper record vs illustrative card (30s).
2. `/options-alpha SPY …` in Claude Code: research → thesis → gate → BLOCKED,
   showing the reason land in the public feed (60s).
3. A market-hours replay of a FILLED chain with broker order id and
   reconciliation (60s).
4. Governance view: REJECTED strategies stay rejected; promotion ladder caps
   at review (30s).
