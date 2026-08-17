# Phase V5.5 Audit — bounded provider response budgets

Date: 2026-08-17 UTC  
Scope: flow, quote, and Morning Brief read-only operator surfaces

## Outcome

Cold Alpaca fallback work no longer blocks the browser indefinitely. Flow
refreshes are deduplicated by query and continue in a two-worker background
pool, while the HTTP request returns within a 2.5-second budget. A timed-out
response has `freshness.status=unknown`, `availability.status=refreshing`, and
an explicit caveat that missing flow is not zero. Provider failures are a
truthful unavailable data state.

Morning Brief uses a 1.5-second bounded quote adapter and resolves SPY/QQQ/IWM
in parallel under a shared two-second market-context budget. Its cards render
refreshing/unavailable labels instead of stale-looking dashes. The dedicated
flow card distinguishes pending refresh, unavailable data, and an empty
available session.

## Live smoke

| Check | Result |
|---|---|
| Cold `/api/spyglass?symbol=AAPL&min=50000` | 2.504s; `source=unavailable`, `freshness=unknown`, `availability=refreshing` |
| Cold `/api/morning-brief?ticker=AAPL` after restart | 2.31s; quote/market/flow states carried explicitly |
| Warm `/api/product-status?ticker=AAPL` | 0.58s |
| Warm `/api/morning-brief?ticker=AAPL` | 1.42s before the budget changes; 2.31s cold-path smoke after restart |
| Core service | active |

## Verification

- Flow, quote-budget, Morning Brief, and Tradier focused tests: 12 passed.
- Evidence/scanner safety focused tests: 17 passed.
- Frontend typecheck: passed.
- Frontend Node suite: 54 passed.
- Python compileall and `git diff --check`: passed.

## Carry-forward

Provider calls continue in bounded background workers so caches can warm; a
restart can therefore show `refreshing` briefly. This is intentional. A future
phase may add a shared request-level trace ID and latency histogram, but must
not turn a timeout into a fabricated current value.
