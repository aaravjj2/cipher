# Phase V2.7 audit — full product integration

Date: 2026-08-14 UTC

## Outcome

The trader workflow now connects scheduled research, quality-gated scanning,
chart validation, option structure research, reproducible testing, journaling,
and paper review without introducing an execution surface. The V2 static bundle
is deployed locally and through the existing authenticated Tailscale Funnel.

## Integrated workflow

1. Morning Brief starts with current session, data health, market/watchlist
   movers, significant captured flow, scan history, and paper portfolio changes.
2. Research Desk ranks intraday and weekly candidates from the scheduled,
   read-only 26-symbol universe and exposes evidence, blockers, target,
   invalidation, source, and capture time.
3. Setup Scanner applies liquidity and coverage gates before scoring, exposes its
   rejection funnel, and links each normal candidate directly to Night Vision,
   Options Terminal, Backtest, and Trader Journal.
4. Night Vision validates price/volume/session behavior and exposure context on
   one shared geometry with bounded overlays and honest unavailable states.
5. Options Terminal builds research structures from bid/ask marks, shows
   liquidity/OI/IV-history coverage, and links to chart validation or recording.
6. Trader Journal stores exact option legs, saved chart evidence, and captured
   mark excursions. Paper Portfolios remains a separate simulation boundary.

A compact six-step navigation rail on Morning Brief makes this path visible
without adding another permanent sidebar or dashboard grid.

## Final reliability finding

The full suite exposed a warm-cache deadlock introduced while instrumenting cache
hits: hit accounting was called while the cache lock was already held. The lock
is now explicitly re-entrant and a regression test exercises that exact nested
path. Two consecutive live NVDA quote requests then produced one miss, one hit,
and a 50% hit rate without blocking the core.

## Verification

- Active Python suite: **895 passed, 2 skipped** in 49.09 seconds.
- Node proxy/auth/ingest suite: **18 passed**.
- Web source/geometry/accessibility suite: **51 passed**.
- Authenticated Playwright product journey: **11 passed** in 25.7 seconds,
  including desktop, 390 px mobile, scanner, Night Vision, backtest, option
  history, journal, and the end-to-end daily workflow.
- ESLint, TypeScript, Python compileall, Node syntax checks, and production Next
  build: passed. `app/public/app.js` is not applicable to the current hashed
  Next.js static build; source and emitted bundles are covered by lint/build and
  browser execution.
- npm dependency audit: **0 vulnerabilities**.
- Atomic publication: `app/public` matches `web/out`.
- Core and web services: active; health reports OPRA/SIP configured and
  `read_only: true`.
- Research, option-history, event-context, and operational-metrics timers:
  enabled and waiting.
- Local authenticated app and Tailscale Funnel both returned HTTP 200.
- Operator status: no exceptions, verified local backup, verified archive
  receipts, and `execution_capability: false`.

## Current access

- Local browser: `http://127.0.0.1:8283`
- Persistent browser URL: `https://cipher-main.tail39504f.ts.net:8443`
- Core API remains loopback-only: `http://127.0.0.1:8282`

## Remaining honest gaps

- IV rank needs 20 distinct captured sessions; repeated same-day samples do not
  manufacture history.
- Authoritative earnings dates remain unavailable until a suitable licensed or
  user-provided source is configured; corporate actions are revisioned locally.
- Option replay only covers contracts actually captured. Missing marks remain
  unavailable and multi-leg asynchronous marks are not interpolated.
- Provider telemetry and storage runway are newly seeded; runway needs a second
  daily observation before an estimate is displayed.
- Flash/Cluster legacy result cards retain their specialized views; the fully
  connected workflow actions currently apply to the normal quality-gated scanner
  comparison and scheduled Research Desk.
- Every strategy result remains research evidence or paper simulation. Promotion
  stops at `LIVE_REVIEW_REQUIRED`; there is no broker client or order endpoint.

Phase verdict: accepted. V2 is materially more coherent, testable, observable,
and useful for daily stock/options research while preserving the non-live boundary.
