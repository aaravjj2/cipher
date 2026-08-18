# Devpost submission draft

## Project name

**Cipher — The Auditable AI Options Copilot**

## Elevator pitch (Devpost tagline, ≤200 chars)

**An auditable AI research copilot for options traders — evidence-backed
scans, replayable analysis, and a paper-only autopilot, with no broker order
authority.**

## One-line pitch

An all-in-one research workstation that helps individual stock and options
traders discover opportunities, verify the evidence, choose a liquid structure,
and automatically paper-test the decision—with a complete audit trail and no
broker-order authority.

## The problem

Individual traders routinely assemble a decision from disconnected tools: a
screener, chart, options chain, news feed, exposure model, spreadsheet, and AI
chat. Those tools disagree about timestamps and data coverage. Missing options
data can look like zero, backtests can hide rejected opportunities, and an AI
answer can sound certain without showing what evidence was actually available.

The result is not just inconvenience. It is false confidence.

## The solution

Cipher turns the complete research loop into one calm, traceable workflow:

- A daily brief identifies stale or missing inputs before presenting ideas.
- A setup scanner ranks only candidates with fresh, sufficient evidence and
  preserves every rejection reason.
- Night Vision combines price, session levels, and public-OI exposure while
  letting the user replay the exact evidence snapshot behind a scan.
- The options terminal evaluates observed contracts, spreads, OI, volume,
  Greeks, and IV without inventing unavailable fields.
- A research agent analyzes large, liquid, and volatile names on a schedule and
  stores bounded evidence-backed memos rather than hidden reasoning.
- Six isolated paper portfolios and registered prospective cohorts record fills,
  outcomes, missed opportunities, and sample sufficiency.
- A paper autopilot performs premarket discovery, waits for post-open closed-bar
  confirmation, applies hard loss/position/time limits, manages simulated exits,
  and writes a replayable decision trace.

## What is innovative

Most finance demos optimize for a confident answer. Cipher optimizes for an
answer that can be challenged.

Its shared evidence envelope joins scanner, chart, options, agents, and paper
experiments with provider, feed, event time, capture time, freshness, coverage,
missing reasons, and a stable snapshot ID. The interface separates observed,
inferred, modeled, stale, and unavailable data. Paper results separate realized
equity, midpoint marks, and conservative liquidation value. Strategies cannot
be ranked until they pass a declared prospective sample gate.

## Real-world impact

Cipher gives a normal trader institutional-style research discipline without
pretending to be an institution or taking control of capital. It makes data
quality and uncertainty visible, reduces tab switching, and teaches users to
evaluate a thesis, invalidation, liquidity, and evidence before acting.

## Technical implementation

- Python standard-library research and market-data API
- Node.js same-origin authenticated proxy
- Next.js/React/TypeScript trader interface
- Alpaca OPRA options and SIP/IEX stock data
- SQLite/WAL event, experiment, GEX, journal, and paper ledgers
- FinBERT sentiment context with a pinned model revision
- Multi-agent research orchestration through allowlisted read-only tools
- Event-time backtests, frozen evidence replay, prospective validation, and
  deterministic configuration hashes
- systemd scheduling, health checks, backups, Discord reports, and operator
  telemetry

## Safety and honesty

Cipher is research-only. It has no order endpoint, TradingClient, or live-order
runner. The autonomous component trades only simulated positions. Promotion
stops at `LIVE_REVIEW_REQUIRED`. Public-OI GEX is always identified as a
heuristic, and missing gamma or OI is never silently converted to zero.

## Installation

```bash
git clone <PUBLIC_SANITIZED_REPOSITORY>
cd <PUBLIC_REPOSITORY_DIRECTORY>
cp .env.example app/.env
# Add read-only Alpaca market-data credentials.
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm ci && npm run build && cd ..
bash scripts/sync_web_build.sh
./Start-Cipher-App.sh
```

Open `http://127.0.0.1:8283/`.

## Team

- Aarav Jain — product, research architecture, full-stack implementation
