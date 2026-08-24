# Loop 082 plan — Backtest labelled scrollport

**Goal:** The filter-partition results table scrolls inside a named overflow-x/y region.

**Why this loop exists:** PROGRAM 082. Loop 074 skipped sticky headers. The partition table wrapper is unnamed `overflow-x-auto` only.

**Architecture:** Change the wrapper to `overflow-x-auto overflow-y-auto` + `role="region"` + `aria-label="Backtest partition results scrollport"`. Add a table `aria-label`. Do not invent sticky headers.

**Files:**
- Modify: `web/src/components/panels/Backtest.tsx`
- Modify: `web/test/remaining-chrome-ui.test.mjs`
- Create: `gates/loop-082-backtest-table-scroll.md`

**Preserve:** `next-open · stop-first · research only`, chronological holdouts, no order surface.

**Anti-goals:** No sticky thead, no OptionsBacktest density rewrite, no catalog/PanelHost, no commit.

**Checks:** remaining-chrome-ui; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Partition table wraps in named `overflow-x-auto overflow-y-auto` region. Table labelled.
- `next-open · stop-first · research only` unchanged. No sticky. OptionsBacktest density untouched.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.

