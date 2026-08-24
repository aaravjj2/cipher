# Loop 085 plan — News list labelled scrollport

**Goal:** The Yahoo RSS headline list scrolls inside a named overflow-x/y region so a long feed does not stretch the page.

**Why this loop exists:** PROGRAM 085. `News.tsx` renders up to 25 headlines in an unwrapped `<ul>` with no overflow region.

**Architecture:** Wrap the existing `<ul>` in `overflow-x-auto overflow-y-auto` + `role="region"` + `aria-label={\`${ticker} headlines scrollport\`}`. Keep feed order, verbatim `caveat`, and the skeleton.

**Files:**
- Modify: `web/src/components/panels/News.tsx`
- Modify: `web/test/remaining-chrome-ui.test.mjs`
- Create: `gates/loop-085-news-list-scroll.md`

**Preserve:** `data.caveat` verbatim, no sentiment/ranking, SkeletonCards loading label, chrome flatten (no rounded-xl).

**Anti-goals:** No catalog/PanelHost, no commit, no invented headlines.

**Checks:** remaining-chrome-ui + heatmap-accessibility (News skeleton needles); full web Node; lint; typecheck; sync. Hosted guest catalog E2E after this fifth UI loop since 080 (081–085).

## Result 2026-08-23

- Headline `<ul>` wraps in named `overflow-x-auto overflow-y-auto` region (`{ticker} headlines scrollport`).
- Caveat verbatim. SkeletonCards loading label unchanged. No sentiment/ranking.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.
- Hosted guest catalog E2E passed desktop + mobile after UI loops 081–085.

