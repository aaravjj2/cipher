# Phase V4.2 audit — shared evidence and setup comparison

Date: 2026-08-17 UTC
Verdict: shared-evidence milestone accepted; overall Phase 2 remains in progress

## Implemented

- Added a view-neutral `EvidenceSnapshot` contract used by Setup Scanner and
  Night Vision. It records event and capture time, Alpaca feed, spot, New York
  market phase, freshness, options coverage, public-OI exposure levels, missing
  reasons, and the read-only execution boundary.
- Added a deterministic SHA-256 snapshot identifier. Identical frozen matrix
  inputs generate the same identifier on both surfaces. Different live quote
  observations intentionally generate different identifiers rather than being
  presented as the same evidence.
- Kept missing gamma, open interest, spot, time, and coverage explicit. The
  evidence builder does not replace an unavailable value with zero.
- Moved scanner evidence qualification onto the shared coverage contract. A
  legacy card cannot rank as high-confidence by asserting richer coverage than
  its evidence snapshot contains.
- Preserved evidence and rejection reasons for rejected scanner examples and
  exposed scan-level evidence schema/version identifiers.
- Attached prior-day, prior-week, premarket, and postmarket levels to Night
  Vision's evidence drawer when those bar-derived levels are available.
- Final ranked candidates now persist a bounded, secret-free frozen matrix
  artifact keyed by evidence ID. Night Vision can replay that artifact without a
  new options request, freezes chart bars at the observation timestamp, disables
  auto-refresh, and provides an explicit return-to-live control.
- Replay does not recompute uncaptured session levels using later bars. Those
  overlays are omitted with an explicit explanation, preserving temporal truth.
- Added a three-candidate comparison tray to Setup Scanner. It compares setup,
  direction, score, target, invalidation, reward/risk, coverage, contract count,
  event time, freshness, evidence identifier, and evidence gaps.
- Expected move and catalyst remain labelled `Not observed` because the scanner
  response does not currently carry those facts. The interface does not infer or
  invent them.
- Comparison handoffs remain research-only links to Night Vision, Options
  Terminal, Backtest, and Trader Journal. No order surface was added.

## Agreement and identity findings

Golden-fixture tests prove that Scanner and Night Vision produce the same
snapshot identifier, exposure levels, session phase, and coverage from the same
matrix payload. Summer and winter New York session boundaries are both tested.

Live Scanner and Night Vision calls made seconds apart can have different IDs
because their quote event/capture timestamps differ. That is correct evidence
identity behavior. A replay link now avoids that mismatch by loading the saved
matrix and cutting the price chart off at the original event time.

## Verification

- Python compile checks: passed.
- Active Python suite: **941 passed, 2 skipped**.
- Node application and web suites: **72 passed**.
- TypeScript and ESLint: passed.
- Next.js production build and atomic publication: passed.
- Published `app/public` matches `web/out`.
- Authenticated Playwright suite against ports 8283/8282: **14/14 passed**.
- Added browser interaction coverage for selecting a setup into the comparison
  tray and confirming the explicit unavailable expected-move/catalyst states.
- Core and web health endpoints: healthy; Alpaca market data configured; core
  reports read-only mode.
- Unified product audit: **COMPLETE**; every check true and
  `execution_authority: false`.
- Live AAPL smoke: scanner artifact available, replay mode `frozen`, identical
  evidence ID, and ten exposure levels reconstructed from the saved matrix.

The repository instructions still name `node --check app/public/app.js`, but the
active UI is now a generated Next.js static export and that legacy source file no
longer exists. Syntax, type, lint, production-build, static-publication, Node
tests, and deployed browser tests cover the active frontend instead.

## Phase 2 work still open

- Unify bar-derived session levels for a selected scanner candidate without
  adding hundreds of rate-heavy bar requests to every broad scan. A lazy replay
  hydration path is preferable to eagerly fetching session bars for the full
  universe. Current replay truthfully omits levels that were not captured.
- Add expected move, catalyst provenance, and option-spread quality to the shared
  evidence contract only when supported by timestamped provider observations.
- Measure and enforce first-render, ticker-switch, and 500-name scan performance
  budgets. The current async partial leaderboard remains useful, but it is not a
  substitute for a recorded budget.
- Continue Night Vision simplification toward a price-first evidence drawer and
  add browser fixture coverage proving frozen levels remain byte-stable across a
  replay session. Unit and live endpoint identity checks already pass.
- Split priority-name and broad-universe GEX capture cadences; the current broad
  sequential loop can leave the front of the universe stale before the next
  cycle returns.

## Safety boundary

All new components are research-only. No broker client, order endpoint,
scheduled live-order runner, or execution authority was introduced.
