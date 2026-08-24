# Loop 086 plan — Night Vision fallback vs live

**Goal:** Guest Night Vision must not label demo fallback as `error`, must not call a loading chart "live", and retry after fallback must exist. Geometry stays untouched.

**Why this loop exists:** PROGRAM 086. Hybrid wiring is present, but `data-guest-source` stays `"error"` while GuestShowcase is `"demo"`. Loading copy says "live" before bars exist. Fallback copy says retry with no retry control.

**Architecture:**
- Guest source: `ready` → `live`, `error` → `demo`, else `status`.
- Loading label drops premature "live".
- Guest error keeps GuestShowcase and adds the existing Retry control.
- Guest ready shows a "Live chart · not demo fallback" caption.
- Do not edit `nightVisionGeometry.ts`, guestCatalog, or PanelHost.

**Files:**
- Modify: `web/src/components/panels/NightVision.tsx`
- Modify: `web/test/night-vision-ui.test.mjs`
- Modify: `web/test/heatmap-accessibility.test.mjs`
- Modify: `web/e2e/night-vision-hybrid.spec.ts`
- Create: `gates/loop-086-night-vision-fallback.md`

**Preserve:** public-OI GEX heuristic, missing stays unknown, 32px chrome / 26px heatmap, no order surface.

**Anti-goals:** No geometry retune, no catalog/PanelHost, no commit.

**Checks:** night-vision-ui + heatmap-accessibility + night-vision-missing-levels; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Guest `data-guest-source` is `live` when ready and `demo` on fallback (not `error`).
- Loading copy no longer says "live" before bars exist. Guest fallback has Retry. Ready guests see "Live chart · not demo fallback".
- `nightVisionGeometry.ts` untouched. Catalog/PanelHost unchanged.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.

