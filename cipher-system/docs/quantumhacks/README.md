# Cipher — QuantumHacks release package

Submission deadline: **2026-08-20 20:00 America/New_York**
Official challenge: <https://quantumhacks.devpost.com/>

## Release thesis

Cipher is an auditable AI stocks-and-options research terminal for individual
traders. It replaces the usual pile of screeners, charts, option-chain tabs,
news feeds, spreadsheets, and untraceable AI answers with one workflow:

```text
Discover → verify evidence → compare an option structure → record the thesis
        → paper-test the decision → audit what happened
```

The differentiator is **decision integrity**. Every important output carries an
event time, source/feed, freshness, coverage, missing-data reasons, and a
replayable identity. The paper autopilot plans before the open, waits for a
closed regular-session confirmation, applies hard portfolio limits, and records
every accepted or rejected decision. It cannot submit a broker order.

## Judge path

1. **Morning Brief** — session, data exceptions, watchlist, research, and
   paper-autopilot state in one view.
2. **Setup Scanner** — quality funnel and explicit rejection reasons instead of
   hiding the denominator.
3. **Night Vision** — price-first chart with the exact scanner evidence replay,
   public-OI GEX caveats, and missing cells preserved as unknown.
4. **Options Terminal** — contract liquidity, Greeks/IV/OI provenance, and
   defined-risk structure research.
5. **Paper Portfolios** — marked versus realized equity, liquidation value,
   risk locks, complete fills, skipped-signal counterfactuals, prospective
   cohorts, and the autopilot decision trace.

## Submission assets

- [Devpost draft](submission.md)
- [2–5 minute demo script](demo-script.md)
- [Architecture diagram](architecture.mmd)
- [Release and eligibility checklist](release-checklist.md)
- [QuantumHacks runbook pointer](three-day-win-plan.md) — the canonical schedule
  is maintained in the main roadmap.

The release builder also collects seven deterministic Playwright screenshots
under `docs/quantumhacks/screenshots/` after the authenticated browser suite has
passed. They contain no credentials; the final public review still requires a
human check for local paths, notifications, or other identifying details.

## Claims the demo may make

- The active market-data path is Alpaca OPRA for options and SIP with IEX
  fallback for stocks.
- GEX is a public-open-interest heuristic, not verified dealer positioning.
- Missing gamma, OI, quotes, or bars remain unavailable rather than becoming
  zero.
- Paper fills cross the observed spread and add modeled slippage.
- FinBERT is advisory context only and cannot authorize an entry.
- The active application has no live-order endpoint or broker-order client.

## Claims the demo must not make

- Guaranteed returns, validated profitability, verified dealer positioning, or
  autonomous live trading.
- That a tiny prospective cohort proves a strategy works.
- That midpoint marks are realizable fills; liquidation estimates are displayed
  separately.
- That the public submission contains private credentials or captured vendor
  archives.
