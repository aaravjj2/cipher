# Loop 034 plan — Scanner icon-only / unnamed controls

**Goal:** Give Setup Scanner’s icon-adjacent and history-row controls accessible names. There are no nameless icon-only buttons; CSV still has a decorative `DownloadIcon` exposed to AT, and history rows have no `aria-label` or focus ring.

**Why this loop exists:** PROGRAM 034. Closest real defect in this panel.

**Architecture:** Attribute-only on `SetupScanner.tsx`. Do not change scan jobs or scoring.

**Files:**
- Modify: `web/src/components/panels/SetupScanner.tsx`
- Create: `web/test/setup-scanner-a11y.test.mjs`
- Create: `gates/loop-034-scanner-aria.md`

**Preserve:** product-hardening scanner strings (presets, confidence copy, comparison tray, `cipher:night-vision-replay`).

**Anti-goals:** No scoring change. No commit.

**Checks:** setup-scanner-a11y + setup-scanner-ui + product-hardening scanner test; full web Node; lint; typecheck; sync.

## Result 2026-08-23

No nameless icon-only buttons existed. CSV control now has `aria-label="Download scan results as CSV"` and `aria-hidden` on the icon. Saved history is a labelled listbox; each row has a load `aria-label` and gold focus. Scoring copy unchanged.

