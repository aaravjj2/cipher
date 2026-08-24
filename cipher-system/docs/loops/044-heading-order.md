# Loop 044 plan — one h1 per panel

**Goal:** Dense panels that currently have no document heading get a single screen-reader `h1`. Panels that already expose a visible `h1` are unchanged. Nested GuestShowcase under Night Vision / Strike Matrix uses `h2` so those pages stay at one `h1`.

**Why this loop exists:** PROGRAM 044. Header dropped the visible panel title. Strike Matrix, Night Vision, Spyglass, Trident, News, Beliefs, Portfolio Risk, Backtest, Strategy Catalog, Options Backtest, GEX Replay, and Alerts have no `h1`.

**Architecture:** `headed(name, node)` in `PanelHost.tsx` prepends `<h1 className="sr-only">`. Do not add a second visual title. Do not change heatmap geometry.

**Files:**
- Modify: `web/src/components/PanelHost.tsx`, `web/src/components/panels/GuestShowcase.tsx`, NightVision.tsx, StrikeMatrix.tsx (GuestShowcase titleTag)
- Create: `web/test/heading-order.test.mjs`
- Create: `gates/loop-044-heading-order.md`

**Preserve:** Guest catalog, existing visible h1 copy, overflow heatmap contracts.

**Anti-goals:** No loading-status redo. No commit.

**Checks:** heading-order + guest catalog tests; full web Node; lint; typecheck; sync.
