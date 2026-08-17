# Phase V4.1 audit — live observation truth and workbench milestone

Date: 2026-08-17 UTC
Verdict: Phase 0 observation plane accepted; Phase 1 primary information hierarchy accepted

## Live session evidence

The first eligible five-minute candle was observed at 09:35 America/New_York.
The monitor initially recorded seven weekly radar candidates and fourteen option
legs. Live review exposed that META and GOOGL had already opened beyond their
first downside targets. Those observations cannot represent a prospective entry.

The records were preserved and changed to `VOID` with outcome
`TARGET_ALREADY_PASSED_AT_SIGNAL`; their four option legs were also voided and no
P/L was assigned. A `SIGNAL_VOIDED` event retains the correction. A permanent
geometry invariant now blocks any long whose first target is at/below entry and
any short whose first target is at/above entry.

Current eligible weekly observations at this audit point:

- SPY pivot failure / short.
- AAPL pivot failure / short.
- AMZN pivot failure / short.
- AMZN later pivot hold / long; the cohort intentionally permits one observation
  per ticker and direction.
- NFLX pivot failure / short.
- MU pivot hold / long.
- TSLA pivot hold / long, recorded later from the separate weekly-radar rule.

The dedicated TSLA stable-wall rejection rule still has no qualifying signal and
remains at 0/20. The weekly-radar TSLA observation does not count toward that
separate strategy. No strategy outcome is claimed while the observations are open.

## Coverage truth

Every in-session run now stores one observation row for every weekly-radar name
plus a separate TSLA-rule row. The ledger distinguishes:

- fresh no-cross,
- signal opened,
- previously recorded/deduplicated,
- provider error,
- no closed RTH bar,
- genuine stale bar,
- between five-minute signal windows,
- target already passed,
- missing/stale GEX,
- low GEX coverage or OI,
- insufficient GEX balance,
- price-rule rejection,
- invalid risk geometry.

The 09:34 pass correctly saw Friday data before the opening candle had closed.
The coverage model initially called the interval between completed five-minute
bars stale. That was corrected: the three-minute no-backfill eligibility remains
strict, while a completed bar is considered healthy feed coverage for six
minutes. The next live pass reported 11/11 fresh observations, including an
explicit `PRICE_RULE_NOT_QUALIFIED` for TSLA.

## Phase 1 product milestone

- Navigation now follows the trader's jobs: Today, Discover, Analyze, Plan,
  Review, Labs, and System.
- Specialist engines remain available but are no longer presented as equal
  top-level concepts.
- A new Ticker Workbench provides one ticker context with Overview, Chart,
  Options, Flow, Company, and Agent tabs.
- Overview shows session/freshness readiness and links evidence review directly
  to the Trader Journal.
- The workbench reuses the existing Night Vision, Options Terminal, Spyglass,
  Company Context, and Ask Cipher components; it introduces no duplicate market
  calculations or order surface.
- Morning Brief is now an ordered decision feed rather than a uniform widget
  grid: integrity/data exceptions first, registered prospective observations
  second, setups/flow/GEX third, broad market/watchlist context fourth, and
  shadow portfolio review last.
- The Morning Brief API supplies a bounded read-only prospective projection with
  program state, latest coverage, eligible open signals, and explicit no-signal
  reasons. The UI does not infer cohort state from unrelated portfolio totals.
- Open paper observations link to Ticker Workbench; the complete immutable ledger
  remains in Paper Portfolios.
- Ticker Workbench now implements the ARIA tab keyboard pattern: one tab stop,
  Left/Right wrapping, Home/End movement, and labelled tabpanel linkage.
- Primary content uses reduced 390 px padding, and the deployed Morning Brief was
  exercised at a 390 × 844 viewport with no horizontal document overflow.
- The authenticated task journey now covers all primary paths rather than relying
  only on static source assertions.

## Verification

- Full active Python suite after the browser-discovered JSON fix: **935 passed, 2 skipped**.
- Focused suite after between-window coverage refinement: **17 passed**.
- Node application/web suites: **72 passed**.
- TypeScript and ESLint: passed.
- Next.js production build and atomic publication: passed.
- Served build matches `web/out`.
- Live Morning Brief response after managed core recovery contains seven eligible
  open weekly observations, two preserved/void integrity exclusions, separate
  TSLA rejection monitoring state, and `execution_capability: false`.
- Unified product audit after publication: **COMPLETE**, all checks true, live
  execution absent.
- Authenticated Playwright suite against the deployed web/core services: **14/14
  passed**, including desktop, 390 px mobile, keyboard tabs, scanner, chart,
  options, journal, paper portfolios, research, and the complete daily workflow.
- Live monitor recovery/deduplication and provider-error fixtures: passed.
- Latest pass at 14:11 UTC: 11 fresh observations, zero partial/stale/missing.
- Fresh five-store backup: restore verified at 13:46:54 UTC. The first backup
  attempt raced an active SQLite writer and left an incomplete partial directory;
  it was never considered valid, was removed, and bounded WAL-safe retries plus
  automatic partial cleanup were added before the successful backup.
- Execution capability remains false.

## Defect found by the phase audit

The first full authenticated run failed Research Desk because cached Finviz
records contained IEEE `NaN` for absent valuation fields. Python's JSON encoder
had emitted that non-standard token, while browser `JSON.parse` correctly rejected
the entire response. The fix is applied twice:

- Finviz normalization maps non-finite numeric provider sentinels to `None` and
  writes new cache files with strict `allow_nan=False`.
- The shared HTTP and SSE JSON boundary recursively maps any remaining non-finite
  value to JSON `null` and rejects non-standard numeric tokens.

The live Research Desk response now passes a strict JSON parser with zero `NaN`
tokens. Missing provider values remain unknown rather than becoming zero.

## Carry-forward findings

- The 542-symbol GEX loop is healthy but sequential: the current pass took about
  26 minutes and then sleeps 15 minutes. High-priority names at the front of the
  universe can therefore become stale before the next pass reaches them. Morning
  Brief now reports this honestly; Phase 4 must split priority and broad-universe
  capture cadences rather than hiding the lag.
- ARTW's latest five-minute bar lagged the liquid names and was correctly marked
  stale in the prospective coverage row. Illiquid-symbol absence remains an
  observation, not a manufactured no-signal.
- Two broad-capture requests received Alpaca HTTP 429 responses (SHOP and VTI);
  both remained explicit errors and were not converted to zero exposure.

## Remaining Phase 1 work

- Reduce duplication between standalone panels and workbench without breaking
  saved layouts.
- Run a manual contrast review and first-use timing study with a human operator;
  automated mobile, keyboard, and full task journeys now pass.
