# Loop 068 plan — Headed Night Vision hybrid

**Goal:** Prove guest Night Vision (standalone and Workbench Chart) is the hybrid live/fallback surface and does not blow page overflow. Geometry stays untouched.

**Why this loop exists:** PROGRAM 068. Catalog E2E opens Night Vision but does not assert hybrid wiring, Workbench Chart, or overflow on that view. 086 remains fallback-vs-live copy later.

**Architecture:** Source-lock catalog `hybrid`, PanelHost `guestMode`, Workbench Chart `guestMode`, error `GuestShowcase`. Hosted Playwright at 1440×900 and 390×844: Night Vision panel then Workbench → Chart; page overflow ≤ 1. No edits to `nightVisionGeometry.ts`.

**Files:**
- Modify: `web/test/night-vision-ui.test.mjs`
- Create: `web/e2e/night-vision-hybrid.spec.ts`
- Create: `gates/loop-068-night-vision-hybrid.md`

**Preserve:** Geometry mapping, heatmap 26px, public-OI heuristic, unknown ≠ 0, 12 tickers.

**Anti-goals:** No geometry retune, no catalog/PanelHost behavior change, no commit.

**Checks:** night-vision-ui; hosted night-vision-hybrid; full web Node.

## Result 2026-08-23

- Source locks catalog hybrid, PanelHost `guestMode`, Workbench Chart `guestMode`, error `GuestShowcase`. Geometry file untouched.
- Hosted guest Night Vision + Workbench Chart: **2 passed** (desktop+mobile), page overflow ≤ 1px.
- Web Node **109 pass / 0 fail**. No product file change.
