# Alpaca hackathon product release audit — 2026-08-23

## Outcome

Cipher now has two deliberately separated surfaces:

1. A read-only browser/research product with a complete, deterministic judge
   guest journey.
2. A loopback-only executor that can automate limit orders in one reconciled
   Alpaca paper account. Live-capital execution is absent.

Public submission source: <https://github.com/ajtopper2412-crypto/cipher-alpaca-agent>

Hosted judge URL: <https://cipher-main.tail39504f.ts.net:8443>

## Guest product evidence

- One typed catalog owns 28 guest-safe panels across 6 workflow sections.
- One typed catalog owns the 12-symbol guest universe: SPY, QQQ, AAPL, MSFT,
  NVDA, AMZN, GOOGL, META, TSLA, AMD, MU, and AVGO.
- Autopilot is the default judge landing panel.
- Live/hybrid/demo/locked behavior is explicit per panel; demo values are
  labelled and private writes remain locked.
- Hybrid Ticker Workbench, Night Vision, and Strike Matrix fall back to useful
  deterministic content rather than exposing a provider error.
- The hosted headed audit traversed all 28 panels at 1440×900 and 390×844 in
  23.2 seconds with no console errors, page errors, private requests, or
  horizontal overflow.
- That audit found and fixed a real mobile defect: the guest hamburger was
  hidden even though the guest sidebar was already capability-filtered.

## Alpaca paper evidence

- Adapter host is a constant: `https://paper-api.alpaca.markets`.
- Non-paper host overrides and non-paper key shapes are rejected.
- Only DAY limit orders are supported.
- Stable client order IDs make submission idempotent.
- Cipher persists the immutable TradeIntent before broker submission.
- Broker uncertainty creates a classified block and never a synthetic local fill.
- Restart reconciliation compares paper positions with Cipher's open-position
  ledger and blocks on unknown positions.
- Browser/core routes remain read-only; no order-submission route was added.

Deployed canary at 02:53 UTC:

- effective mode: `paper`
- execution backend: `alpaca_paper`
- account: `ACTIVE`, paper-only, reconciliation ready
- unknown broker positions: 0
- broker orders: 0
- worker health: 4/4 running
- current entry blocker: none
- ledger: 34 batches, 191 cards, 14 episodes, 0 contract candidates, 0
  orders, 0 open positions
- next automatic premarket cycle: Monday 2026-08-24 08:45 ET

The status still retains a historical Aug 21 option-chain worker exception for
auditability. It is stale, not the current readiness state; the deployed OPRA
and broker reconciliations now pass.

## Distribution evidence

- Standalone source bundle: 373 allowlisted files.
- MIT license, public README, `.gitignore`, Dockerfile, Compose file, and CI are included.
- Runtime databases, environment files, private-data symlinks, caches, rollback
  releases, and compiled environment-specific hosted bundles are excluded.
- The release builder scans for private keys and common credential formats and
  rejects a live Alpaca hostname in the paper adapter.
- GitHub CI passed on both published release commits, including the final
  image-refresh commit.

## Verification

- Focused paper/status/safety: 29 passed.
- App Node suite: 33 passed.
- Web Node suite: 65 passed.
- Hosted exhaustive guest audit: 2 passed.
- Production web lint, typecheck, and build passed.
- Published static tree matches `web/out`.
- Full Python suite: 1,112 passed, 1 skipped.

## Deliberately deferred

- Monday is the first possible end-to-end broker-order observation. A valid
  no-setup day must remain no-trade rather than manufacturing a demo fill.
- Video narration/editing and the lablab.ai submission form remain human-facing
  submission work.
- Historical option quotes that were never captured will not be fabricated.
- Live brokerage, mobile-native packaging, model fine-tuning, and additional
  brokers remain post-hackathon work. The immediate product sequence after the
  event is: prospective paper evidence, broker-portability interface, alerting,
  then broader strategy graduation.
