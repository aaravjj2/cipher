# Judge-Ready Guest Showcase Audit — 2026-08-23

Guest mode now exposes Cipher's complete non-system research workflow rather
than three isolated panels. The implementation remains read-only and does not
weaken the hosted security boundary.

## Product behavior

- All TODAY, DISCOVER, ANALYZE, PLAN, REVIEW, and LABS navigation is visible.
- Ticker Workbench, Night Vision, and Strike Matrix retain bounded live market
  views.
- Every other panel renders deterministic, panel-specific showcase content.
- Static values carry an `Illustrative judge demo` label and state that they
  are not current quotes, recommendations, or performance claims.
- MAG7 plus SPY, QQQ, AMD, MU, and AVGO are supported in the showcase ticker
  universe.
- The guest ticker tape uses visibly approximate `demo` values and does not
  spend anonymous provider quota.
- Watchlists, holdings, alerts, journal, and chart saves show their product
  purpose while clearly locking private writes.
- Ask Cipher explains its tool-backed workflow without making anonymous LLM
  requests.
- Settings and Operator Status remain absent. Provider connections, system
  controls, saved workspaces, and all order authority remain unavailable.

## Security boundary

The server still proxies only the bounded GET-only guest market allowlist,
enforces the symbol universe, caps bars and expirations, rejects forced refresh,
rate-limits anonymous sessions, and forwards no access token. Private state and
provider-session routes remain HTTP 403. The access profile still reports
`liveOrders=false`, `savedWorkspace=false`, and `providerConnection=false`.

## Verification

- Access-profile and hosted guest boundary: 5/5 passed.
- App Node suite: 33/33 passed.
- Web Node suite: 62/62 passed.
- Web lint, TypeScript, and production build: passed.
- Research-only guard: 11/11 passed.
- Full Python suite: 1,099 passed, 1 skipped.
- Hosted headed Chrome journey: Strike Matrix, Morning Brief showcase, Options
  Terminal showcase, and locked Holdings passed with no browser, console, or
  failed-request errors.
- Published screenshot: `guest-full-showcase.png` in the headed audit results.
