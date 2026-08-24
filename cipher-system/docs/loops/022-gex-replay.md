# Loop 022 plan — GEX Replay chrome

**Goal:** Flatten chrome to DESIGN.md surfaces without behavior change.

**Files:** `web/src/components/panels/GexReplay.tsx` plus `web/test/remaining-chrome-ui.test.mjs`.

**Preserve:** missing gamma/OI unknown

**Anti-goals:** No live orders, no reconstructed missing data, no commit.

**Checks:** remaining-chrome + heatmap + product-hardening; full web Node; lint; typecheck; sync after this chrome batch.

**Result 2026-08-23:** done — class-only flatten; see remaining-chrome-ui.test.mjs
