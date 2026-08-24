# Loop 087 plan — Strike Matrix fallback vs live

**Goal:** Guest Strike Matrix must label demo fallback as demo (not error), must not call a loading grid "live", and retry after fallback must exist. Heatmap cells stay 26px; `overflow-auto` on `grid-scroll` stays.

**Why this loop exists:** PROGRAM 087. Same honesty gap as 086: `data-guest-source` is `"error"` while GuestShowcase is `"demo"`. Loading copy claims live before cells exist.

**Architecture:**
- Guest source: `ready` → `live`, `error` → `demo`, else `status`.
- Loading label drops premature "live".
- Guest error keeps GuestShowcase and adds Retry.
- Guest ready shows "Live matrix · not demo fallback".
- Do not edit heatmap cell height, `overflow-auto` on `grid-scroll`, guestCatalog, or PanelHost.

**Files:**
- Modify: `web/src/components/panels/StrikeMatrix.tsx`
- Modify: `web/test/strike-matrix-a11y.test.mjs`
- Create: `gates/loop-087-strike-matrix-fallback.md`

**Preserve:** unknown ≠ 0, OI-as-of on legend, 26px cells, `overflow-auto` scrollport, public-OI GEX heuristic.

**Anti-goals:** No catalog/PanelHost, no commit, no overflow-x-auto-only on grid-scroll.

**Checks:** strike-matrix-a11y + heatmap-accessibility + oi-date-public-oi; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Guest `data-guest-source` is `live` when ready and `demo` on fallback.
- Loading copy no longer says "live". Guest fallback has Retry. Ready guests see "Live matrix · not demo fallback".
- Heatmap cells stay 26px. `grid-scroll` stays `overflow-auto`. Catalog/PanelHost unchanged.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.

