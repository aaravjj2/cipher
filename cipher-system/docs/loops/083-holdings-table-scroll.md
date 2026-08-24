# Loop 083 plan — Holdings labelled scrollport

**Goal:** Holdings position grids scroll inside named overflow-x/y regions so wide option contracts do not stretch the page.

**Why this loop exists:** PROGRAM 083. The options grid has unnamed `overflow-x-auto`. Open and closed position grids have no overflow wrapper.

**Architecture:** Wrap each of the three position lists in `overflow-x-auto overflow-y-auto` + `role="region"` + a distinct `aria-label`. Keep `min-w-[760px]` on options. Do not add a holdings skeleton.

**Files:**
- Modify: `web/src/components/panels/Holdings.tsx`
- Modify: `web/test/remaining-chrome-ui.test.mjs`
- Create: `gates/loop-083-holdings-table-scroll.md`

**Preserve:** `does not read a brokerage account or place orders`, no holdings skeleton, guest lock unchanged.

**Anti-goals:** No broker connect, no catalog/PanelHost, no commit, no skeleton.

**Checks:** remaining-chrome-ui + loading-status-a11y; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Open, option, and closed position grids wrap in named `overflow-x-auto overflow-y-auto` regions. Option `min-w-[760px]` kept.
- Broker copy unchanged. No holdings skeleton. Guest lock/catalog untouched.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.

