# Loop 035 plan — Night Vision toggle accessible names

**Goal:** Name remaining Night Vision controls that are icon-only or abbreviation-only. Refresh already has `aria-label`; hide the decorative icon. Timeframe pills and strike rows need names. Do not edit geometry.

**Why this loop exists:** PROGRAM 035.

**Architecture:** Attributes on `NightVision.tsx` only. Leave `nightVisionGeometry.ts` untouched.

**Files:**
- Modify: `web/src/components/panels/NightVision.tsx`
- Create: `web/test/night-vision-a11y.test.mjs`
- Create: `gates/loop-035-night-vision-aria.md`

**Preserve:** `fetchNightVisionReplay`, public-OI GEX heuristic copy, `aria-label={`${ticker} candlestick chart`}`, missing gamma/OI unavailable.

**Anti-goals:** No scale/GEX formula change. No commit.

**Checks:** night-vision-a11y + night-vision-ui + geometry tests; full web Node; lint; typecheck; sync.

## Result 2026-08-23

Refresh remains `aria-label="Refresh chart"` with `aria-hidden` on the icon. Timeframe group, More menu, extra timeframes, and X-Ray strike rows have accessible names. `nightVisionGeometry.ts` unchanged.

