# Loop 076 plan — Scanner results labelled scrollport

**Goal:** Ranked scan results scroll inside a labelled region instead of clipping (`overflow-hidden`) or stretching the page horizontally.

**Why this loop exists:** PROGRAM 076. The 7-column results header (`42+86+86+70+90+90px`) sits in `overflow-hidden`, so narrow viewports clip Rank/Ticker/path instead of scrolling.

**Architecture:** Same as Earnings Radar: `overflow-x-auto overflow-y-auto` + `role="region"` + `aria-label="Ranked scan results"`. Do not change scoring, empty-job copy, or Compare tray.

**Files:**
- Modify: `web/src/components/panels/SetupScanner.tsx`
- Modify: `web/test/setup-scanner-ui.test.mjs`
- Create: `gates/loop-076-scanner-results-scroll.md`

**Preserve:** Confidence = evidence coverage, empty job copy, no order identifiers, heatmap 26px.

**Anti-goals:** No sticky invention, no catalog/PanelHost change, no commit.

**Checks:** setup-scanner-ui + scanner-empty-job + scanner-score-honesty; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Ranked results wrapper is `overflow-x-auto overflow-y-auto` `role="region"` `aria-label="Ranked scan results"` (was `overflow-hidden`).
- Scoring, empty-job copy, and Compare tray unchanged.
- Web Node **109 pass / 0 fail**. Lint, typecheck, sync OK.
