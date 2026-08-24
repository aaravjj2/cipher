# Loop 036 plan — Strike Matrix toolbar keyboard

**Goal:** Density/range/metric/mode pill groups use arrow-key roving focus like Ticker Workbench tabs. Add gold `focus-visible` on toolbar controls. Do not change heatmap cells or GEX values.

**Why this loop exists:** PROGRAM 036. Pills are only clickable; Tab visits every pill.

**Architecture:** `PillGroup` becomes a `radiogroup` with `ArrowLeft`/`ArrowRight`, `tabIndex={active ? 0 : -1}`, and `aria-checked`. Icon buttons keep existing `aria-label`s and get gold focus.

**Files:**
- Modify: `web/src/components/panels/StrikeMatrix.tsx`
- Create: `web/test/strike-matrix-a11y.test.mjs`
- Create: `gates/loop-036-strike-matrix-keyboard.md`

**Preserve:** `role="table"`, unknown not zero, `overflow-auto` on grid-scroll, guest hybrid.

**Anti-goals:** No formula change. No commit.

**Checks:** strike-matrix-a11y + heatmap-accessibility; full web Node; lint; typecheck; sync.

## Result 2026-08-23

Density, range, metric, and mode groups are `radiogroup`s with roving `tabIndex` and arrow keys. Toolbar icon buttons keep labels and hide decorative icons. Heatmap unknown-not-zero contract unchanged.

