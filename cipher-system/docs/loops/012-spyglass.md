# Loop 012 plan — Spyglass control chrome

**Goal:** Flatten chrome to DESIGN.md surfaces without behavior change.

**Files:** `web/src/components/panels/Spyglass.tsx` plus `web/test/remaining-chrome-ui.test.mjs`.

**Preserve:** keep overflow-x-auto rounded-[10px] heatmap contract

**Anti-goals:** No live orders, no reconstructed missing data, no commit.

**Checks:** remaining-chrome + heatmap + product-hardening; full web Node; lint; typecheck; sync after this chrome batch.

**Result 2026-08-23:** done — class-only flatten; see remaining-chrome-ui.test.mjs
