# Loop 077 plan — Strike Matrix labelled scrollport

**Goal:** The Strike Matrix `grid-scroll` overflow container is a named region. Cells stay 26px. Overflow stays `overflow-auto` (both axes; do not use `overflow-x-auto` alone).

**Why this loop exists:** PROGRAM 077. The table has an accessible name; the actual scrollport (`grid-scroll`) does not, so AT cannot find the scrollable matrix independently.

**Architecture:** Add `role="region"` and a ticker/metric `aria-label` on the existing `overflow-auto` wrapper. Do not retune heatmap geometry, sticky headers, or GEX heuristic.

**Files:**
- Modify: `web/src/components/panels/StrikeMatrix.tsx`
- Modify: `web/test/heatmap-accessibility.test.mjs`
- Create: `gates/loop-077-strike-matrix-scroll.md`

**Preserve:** `overflow-auto` on `grid-scroll`, sticky expiration headers, `height: "26px"`, unknown ≠ 0, public-OI caveat.

**Anti-goals:** Do not change Strategy Catalog `overflow-x-auto rounded-[8px]`. No catalog/PanelHost change. No commit.

**Checks:** heatmap-accessibility + oi-date-public-oi; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- `grid-scroll` is `role="region"` labelled `{ticker} {METRIC} strike-matrix scrollport`.
- `overflow-auto` and heatmap `height: "26px"` unchanged.
- Web Node **109 pass / 0 fail**. Lint, typecheck, sync OK.
