# Loop 052 plan — Night Vision missing levels ≠ 0

**Goal:** Missing GEX/VEX on Night Vision is shown as unknown, not `$0`. Candle geometry file stays untouched. Domain mapping keeps using the existing level-price list.

**Why this loop exists:** PROGRAM 052. Core sums `net_gex or 0.0`, so X-Ray `formatDollar(v)` and GEX bands label missing exposure as `$0`. Strike Matrix already treats unavailable as unknown.

**Architecture:** For each strike, sum only finite cell nets from `nightVision.rows`. If none, X-Ray prints `unknown` and bands skip that level. Do not change `nightVisionGeometry.ts` or the `buildNightVisionGeometry(...)` price list.

**Files:**
- Create: `web/src/lib/nightVisionKnownNet.ts`
- Modify: `web/src/components/panels/NightVision.tsx`
- Create: `web/test/night-vision-missing-levels.test.mjs`
- Create: `gates/loop-052-nv-missing-levels.md`

**Preserve:** Public-OI heuristic copy, SkeletonChart, guest fallback, heatmap 26px SP cells, no order surface.

**Anti-goals:** No WoW. No geometry file edits. No commit. No hosted E2E (rendering honesty, geometry contract covered by Node tests).

**Checks:** night-vision-missing-levels + night-vision-ui + nightVisionGeometry; full web Node; lint; typecheck; sync.
