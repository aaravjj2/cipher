# Loop 048 plan — GEX heuristic on remaining panels

**Goal:** Every GEX-facing panel states the public-OI heuristic, not verified dealer positioning. Heatmap geometry, unknown≠0, and Replay overflow stay unchanged.

**Why this loop exists:** PROGRAM 048. Night Vision, Chart Workbench, Settings, and loaded GEX Replay already carry the caveat. Strike Matrix and Trident share `ExposureLegend` with no GEX sentence. Morning Brief, Replay empty state, Alerts, and guest Matrix/Trident fallbacks also omit it.

**Architecture:** One sentence in `ExposureLegend` covers both live heatmaps. Same sentence on Morning Brief, Replay empty, Alerts, and guest Matrix/Trident copy.

**Files:**
- Modify: HeatmapGrid.tsx, MorningBrief.tsx, GexReplay.tsx, Alerts.tsx, GuestShowcase.tsx, heatmap-accessibility.test.mjs
- Create: `web/test/gex-heuristic-copy.test.mjs`
- Create: `gates/loop-048-gex-heuristic.md`

**Preserve:** Heatmap cell height 26px, overflow axes, Replay `min-w-[620px]`, Night Vision geometry, no order surface.

**Anti-goals:** Do not invent dealer positioning. No formula change. No commit. No hosted E2E (copy-only).

**Checks:** gex-heuristic-copy + heatmap + morning-brief + options-backtest-density (alerts); full web Node; lint; typecheck; sync.
