# Loop 084 plan — Strategy list labelled scrollport

**Goal:** The Strategy Catalog verdict register scrolls inside a named region. Keep `overflow-x-auto rounded-[8px]` and `min-w-[760px]`.

**Why this loop exists:** PROGRAM 084. The wrapper already owns horizontal scroll; it has no region name.

**Architecture:** Add `role="region"` and `aria-label="Strategy catalog verdicts scrollport"` on the existing wrapper. Optionally append `overflow-y-auto` after `rounded-[8px]` so both axes are named without breaking the contiguous `overflow-x-auto rounded-[8px]` contract.

**Files:**
- Modify: `web/src/components/panels/StrategyCatalog.tsx`
- Modify: `web/test/remaining-chrome-ui.test.mjs`
- Modify: `web/test/heatmap-accessibility.test.mjs`
- Create: `gates/loop-084-strategy-list-scroll.md`

**Preserve:** `overflow-x-auto rounded-[8px]`, `min-w-[760px]`, blocked-not-scored copy, heatmap 26px, no catalog/PanelHost change.

**Anti-goals:** Do not drop `rounded-[8px]`. No commit.

**Checks:** remaining-chrome-ui + heatmap-accessibility + purple-leftover; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Verdict register is `role="region"` labelled `Strategy catalog verdicts scrollport`.
- Kept contiguous `overflow-x-auto rounded-[8px]` and `min-w-[760px]`.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.

