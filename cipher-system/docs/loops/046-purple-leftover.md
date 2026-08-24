# Loop 046 plan — purple leftover in `web/src`

**Goal:** User-facing copy, comments, and hardcoded hex in `web/src` stop claiming a purple accent. Tokens already use amber `--accent` (`#f0b90b`). Heatmap cells, overflow contracts, and Night Vision geometry stay unchanged.

**Why this loop exists:** PROGRAM 046. DESIGN.md is amber + green/red. `HeatmapGrid` still labels positive exposure “purple”; Strategy Catalog still paints BLOCKED with `#7c3aed` / `#c4b5fd`; Setup Scanner primary CTA still uses lavender `#f8f2ff`; comments still describe a purple P/L convention.

**Architecture:**
- Legend + comment: “amber”, not “purple”.
- BLOCKED and accrual copy use `--gold` mixes, not violet hex.
- Evaluate button: `--accent` with `--accent-foreground`; drop `#7c3aed` fallback.
- Scanner CTA text: `--accent-foreground`.
- Comments in Chart Saves, Night Vision, Holdings match current tokens.

**Files:**
- Modify: HeatmapGrid.tsx, StrategyCatalog.tsx, SetupScanner.tsx, ChartSaves.tsx, NightVision.tsx, Holdings.tsx, heatmap-accessibility.test.mjs
- Create: `web/test/purple-leftover.test.mjs`
- Create: `gates/loop-046-purple-leftover.md`

**Preserve:** Heatmap `getCellColor` formula, unknown ≠ 0, catalog `overflow-x-auto rounded-[8px]`, scanner scoring copy, no order surface.

**Anti-goals:** Do not retint every `--accent` P/L usage to green (later honesty loops). No Night Vision geometry. No commit. No hosted E2E (copy-only; last guest E2E was 045).

**Checks:** purple-leftover + heatmap; full web Node; lint; typecheck; sync.
