# Loop 029 plan — Header focus-visible

**Goal:** Keyboard focus on Header controls must be visible. The ticker input currently uses `outline-none` with no replacement; menu, workspace, watchlist, suggestions, and profile buttons have no `focus-visible` ring.

**Why this loop exists:** PROGRAM 029. Vercel guidelines: never remove outline without a visible replacement. Gold ring matches Options Terminal / DESIGN.md.

**Architecture:** Class-only on `Header.tsx`. Keep combobox keyboard behavior, guest universe, watchlist shortcut, and “Research only”. Do not change quote fetch or toolbar portal overflow (later overflow loop).

**Files:**
- Modify: `web/src/components/Header.tsx`
- Create: `web/test/header-a11y.test.mjs`
- Create: `gates/loop-029-header-focus.md`

**Preserve:** `Open navigation`, ticker combobox/`ticker-suggestions`, `GUEST_TICKERS`, `Research only`, workspace `aria-label={`Workspace ${n}`}`, `+ Watchlist`.

**Anti-goals:** No auth/session change. No commit. No live orders.

**Checks:** focused header + guest-catalog tests; full web Node; lint; typecheck.

## Result 2026-08-23

Ticker search uses gold `focus-within` so `outline-none` on the input is not a silent keyboard trap. Nav, workspace tabs, suggestion options, watchlist, and profile buttons have gold `focus-visible` rings. Combobox behavior, guest universe, and “Research only” unchanged. Lint/typecheck/sync OK.

