# Loop 057 plan — OI date where public OI is used

**Goal:** GEX heatmaps and the options chain state the open-interest session date. Missing date is **unknown**, not implied current.

**Why this loop exists:** PROGRAM 057. Night Vision already shows `OI {date}`. Strike Matrix, Trident, and ExposureLegend only say “public-OI heuristic.” GEX is gamma × OI, so a stale OI date changes the surface.

**Architecture:** `ExposureLegend` takes `oiAsOf`. Matrix and Trident pass coverage dates. Options Terminal surfaces `open_interest_date` next to the existing caveat. Do not invent dates. Heatmap cells stay 26px.

**Files:**
- Modify: `HeatmapGrid.tsx`, `StrikeMatrix.tsx`, `Trident.tsx`, `OptionsTerminal.tsx`
- Create: `web/test/oi-date-public-oi.test.mjs`
- Create: `gates/loop-057-oi-date.md`

**Preserve:** Public-OI heuristic sentence, heatmap `height: "26px"`, `overflow-x-auto`, GEX Replay `min-w-[620px]`, no order surface.

**Anti-goals:** No catalog/PanelHost change. No hosted E2E. No commit. No geometry edits.

**Checks:** oi-date-public-oi + gex-heuristic-copy; full web Node; lint; typecheck; sync.
