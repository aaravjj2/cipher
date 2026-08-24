# Loop 079 plan — Paper table labelled scrollport

**Goal:** Wide paper ledger tables scroll inside named regions so they do not stretch the page.

**Why this loop exists:** PROGRAM 079. Six `overflow-x-auto` wrappers have no region name; the positions table has no table label.

**Architecture:** Shared `ScrollTable` wrapper: `overflow-x-auto overflow-y-auto` + `role="region"` + `aria-label`. Do not change captured vs estimated P&L copy.

**Files:**
- Modify: `web/src/components/panels/PaperPortfolios.tsx`
- Modify: `web/test/paper-portfolios-ui.test.mjs`
- Create: `gates/loop-079-paper-table-scroll.md`

**Preserve:** `not hypothetical option fills or P&L`, HEALTHY_NO_SETUP elsewhere, no order surface, heatmap 26px.

**Anti-goals:** No sticky invention, no catalog/PanelHost, no commit.

**Checks:** paper-portfolios-ui + paper-marks-honesty; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Six ledger tables wrap in `ScrollTable` (`overflow-x-auto overflow-y-auto`, named `role="region"`). Positions table has an `aria-label`.
- Captured vs estimated P&L copy unchanged. No sticky headers.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.
