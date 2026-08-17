# Phase V5.3 Audit — Evidence Timeline and Product Trust Surface

Date: 2026-08-17
Scope: scanner/Night Vision trust and trader-facing provenance UI.

## Changes

- Added a Night Vision Evidence timeline drawer accessible from the evidence badge.
- Timeline shows observed time, capture time, market session, freshness age, coverage, provider/feed, snapshot identity, missing inputs, caveats, and execution boundary.
- Cached-provider responses remain visibly distinct from live responses.
- Added `data_status`, `provider_error`, and `cache_note` frontend types for explicit runtime state.
- Existing scanner-to-Night-Vision replay identity remains content-addressed through the shared evidence snapshot ID.

## Verification

| Check | Result |
|---|---|
| Full Python suite | 960 passed, 2 skipped |
| Frontend node tests | 54 passed |
| ESLint | passed |
| TypeScript | passed |
| Production build and atomic publish | passed |
| Core/web runtime smoke | passed |

## Product outcome

A trader can now inspect why a Night Vision level is present, how fresh it is, which inputs were unavailable, and whether the chart is live or a bounded stale replay without leaving the workbench. This closes the first major provenance-to-UI gap.

## Remaining roadmap gate

The next shippable-product gate is options-flow freshness and scanner/chart parity fixtures: every displayed setup should link to a replayable source snapshot and use the same session/level evaluator across scanner, chart, historical, and prospective modes.
