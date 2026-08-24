# Loop 002 plan — Night Vision chrome

**Goal:** Flatten Night Vision chrome to DESIGN.md / Skew Map surfaces without touching candlestick geometry, session filters, GEX formula, or replay identity.

**Why this loop exists:** `NightVision.tsx` still uses `rounded-[10px]` / `rounded-lg` cards on the toolbar, evidence drawer, regime summary, chart frame, and X-Ray dock. Headed workbench Chart tab will inherit that chrome. Geometry tests in `nightVisionGeometry.test.mjs` are the contract and must not change.

**Architecture:** Class-only preserve-mode pass. Shared atoms (`PillGroup`, `ToggleButton`, `TextButton`) get 4px controls, border groups, and gold `focus-visible` rings. Legend dots and ATM/status pills may stay `rounded-full`. Missing gamma/OI copy stays “unavailable”. Public-OI heuristic sentence stays.

**Files:**
- Modify: `web/src/components/panels/NightVision.tsx`
- Create: `web/test/night-vision-ui.test.mjs`
- Create: `gates/loop-002-night-vision.md`
- Do not modify: `web/src/lib/nightVisionGeometry.ts`

**Preserve:** overlays, expiration presets, timeframes, RTH/extended, range counts, replay freeze, Save chart, Auto refresh, SkeletonChart, guest fallback, `aria-label` on the SVG chart, `fetchNightVisionReplay`.

**Anti-goals:** No scale/viewBox changes. No GEX color-token retune in `gexCellColor`. No paper/order work. No commit.

**Checks:**
- `node --test web/test/night-vision-ui.test.mjs web/test/nightVisionGeometry.test.mjs web/test/heatmap-accessibility.test.mjs web/test/product-hardening.test.mjs`
- lint + typecheck
- build + sync (guest hybrid surface)

## Result 2026-08-23

- Toolbar, evidence drawer, regime summary, chart frame, X-Ray dock, and error empty-state use border surfaces. Legend dots and ATM/status pills remain round.
- Gold `focus-visible` rings added on pills, toggles, refresh, and range/session controls.
- `nightVisionGeometry.ts` untouched. GEX heuristic and “unavailable” copy unchanged.
- Web Node 68 passed / 0 failed. Lint and typecheck passed. Published tree in sync.
- Guest E2E deferred to the every-5-UI-loops cadence (this is loop 2).
- `gates/loop-002-night-vision.md` ALL MET.
- Not committed.
