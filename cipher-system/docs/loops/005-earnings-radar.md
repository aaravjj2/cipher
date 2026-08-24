# Loop 005 plan — Earnings Radar chrome

**Goal:** Flatten Earnings Radar chrome; keep missing data unknown and paper-only caveats.

**Why this loop exists:** Six rounded containers; radar must not look like a live order desk.

**Architecture:** Class-only. Keep `rounded-full` status pill. Do not retrofit quotes.

**Files:**
- Modify: `web/src/components/panels/EarningsRadar.tsx`
- Create: `web/test/earnings-radar-ui.test.mjs`
- Create: `gates/loop-005-earnings-radar.md`

**Preserve:** `unavailable, never zero`; `{data.caveat}`; `Paper-only recommendations — no order authority`; estimated win/P&L labels; table `aria-label`.

**Anti-goals:** No quote retrofit. No commit.

**Checks:** focused + product-hardening; full web Node; lint; typecheck; build+sync; hosted guest E2E (5-loop cadence).

## Result 2026-08-23

Radar banners/table flattened; overflow-x and overflow-y both set; caveat preserved. Web Node later in this batch. Guest E2E at loop 005 cadence.
