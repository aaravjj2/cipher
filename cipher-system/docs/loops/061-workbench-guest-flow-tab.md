# Loop 061 plan — Workbench guest Flow tab (source test)

**Goal:** Source test that guests cannot open the Workbench Flow tab.

**Why this loop exists:** PROGRAM 061. Guest Workbench must not expose Flow.

**Skip-if:** Coverage already exists (061–075: tests only where missing).

**Evidence before any new test file:**
- `TickerWorkbench.tsx`: `visibleTabs` is Overview/Chart/Options in guest; Flow/Company/Agent panels gate on `!guestMode`; guest Overview jumps omit Flow.
- `web/test/ticker-workbench-ui.test.mjs` asserts `guestMode ? ["Overview", "Chart", "Options"]` and `tab === "Flow" && !guestMode`.
- Same assertions in `workbench-no-order-ticket.test.mjs` and `guest-tape-live-quote.test.mjs`.

**Files:** none. Do not add a duplicate test.

**Preserve:** Guest tabs, hybrid catalog, no order surface.

**Anti-goals:** No product change, no commit, no invented coverage.

**Checks:** existing workbench tests pass.

## Result 2026-08-23

Skipped — coverage already in `ticker-workbench-ui.test.mjs`, `workbench-no-order-ticket.test.mjs`, and `guest-tape-live-quote.test.mjs` (3 pass / 0 fail). No duplicate test file.
