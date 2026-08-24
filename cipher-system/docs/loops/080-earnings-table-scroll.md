# Loop 080 plan — Earnings table labelled scrollport

**Goal:** The wide earnings cards table scrolls inside a named overflow-x/y region.

**Why this loop exists:** PROGRAM 080. Loop 073 already asserted both overflow axes and skipped sticky headers. The wrapper is still an unnamed `div`, so the scrollport has no region name.

**Architecture:** Add `role="region"` and `aria-label="Upcoming earnings radar scrollport"` on the existing `overflow-x-auto overflow-y-auto` wrapper. Keep the table `aria-label`. Do not invent sticky headers.

**Files:**
- Modify: `web/src/components/panels/EarningsRadar.tsx`
- Modify: `web/test/earnings-radar-ui.test.mjs`
- Create: `gates/loop-080-earnings-table-scroll.md`

**Preserve:** `UNVALIDATED_FOR_LIVE_OPTIONS_PNL`, `unavailable, never zero`, `{data.caveat}`, estimated vs live P&L wording, no order surface.

**Anti-goals:** No sticky thead, no catalog/PanelHost, no commit, no gate token rewrite.

**Checks:** earnings-radar-ui + earnings-unvalidated; full web Node; lint; typecheck; sync. Hosted guest catalog E2E after this fifth UI loop since 066 (076–080).

## Result 2026-08-23

- Earnings cards wrapper is `role="region"` labelled `Upcoming earnings radar scrollport`. Both overflow axes kept. Table label unchanged.
- `UNVALIDATED_FOR_LIVE_OPTIONS_PNL` and unavailable-never-zero copy unchanged.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.
- Hosted guest catalog E2E (desktop + mobile) passed after UI loops 076–080.

