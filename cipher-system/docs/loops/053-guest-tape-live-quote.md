# Loop 053 plan — Guest tape demo vs live quote

**Goal:** Guests can tell the ticker **tape** (canned demo prices) from the **header/workbench live quote** (`/api/quote`).

**Why this loop exists:** PROGRAM 053. Guest banner says live and illustrative values are labelled. The tape only tacks `demo` onto the change %. The header quote is live and unlabelled. Workbench shows the same live print with no “live” word. Easy to treat both as one feed.

**Architecture:** Label at the source. Tape: `Demo tape · not live` plus aria-label. Header (guest): `live` on the quote. Workbench: `live quote` next to feed. Do not swap tape onto live quotes or hide the live quote. Catalog/PanelHost unchanged.

**Files:**
- Modify: `web/src/components/TickerStrip.tsx`, `web/src/components/Header.tsx`, `web/src/components/panels/TickerWorkbench.tsx`
- Create: `web/test/guest-tape-live-quote.test.mjs`
- Create: `gates/loop-053-guest-tape-live-quote.md`

**Preserve:** 12 guest tickers, `GUEST_DEMO_QUOTES` values, header 32px icons, heatmap 26px, no Flow tab in guest, no order surface.

**Anti-goals:** No catalog change. No hosted E2E (copy honesty; catalog/PanelHost untouched). No commit.

**Checks:** guest-tape-live-quote + ticker-workbench-ui + guest-catalog; full web Node; lint; typecheck; sync.
