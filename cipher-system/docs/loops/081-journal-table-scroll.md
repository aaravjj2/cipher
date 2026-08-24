# Loop 081 plan — Journal labelled scrollport

**Goal:** The trader journal entry list scrolls inside a named overflow-x/y region so wide option marks and OCC symbols do not stretch the page.

**Why this loop exists:** PROGRAM 081. `TraderJournal.tsx` has no overflow wrapper. Entries are a two-column card grid with contract symbols and option-mark numbers.

**Architecture:** Wrap the entries grid in `overflow-x-auto overflow-y-auto` + `role="region"` + `aria-label="Trader journal entries scrollport"`. Do not retune MFE/MAE copy.

**Files:**
- Modify: `web/src/components/panels/TraderJournal.tsx`
- Modify: `web/test/remaining-chrome-ui.test.mjs`
- Create: `gates/loop-081-journal-table-scroll.md`

**Preserve:** `not option-premium P/L`, captured marks are valuation evidence not fills, no order surface.

**Anti-goals:** No sticky, no catalog/PanelHost, no commit, no treating journal marks as fills.

**Checks:** remaining-chrome-ui; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Entry list wraps in `overflow-x-auto overflow-y-auto` named `Trader journal entries scrollport`.
- `not option-premium P/L` unchanged. No sticky, no catalog/PanelHost.
- Web Node 109 pass / 0 fail. Gates ALL MET. Sync OK.

