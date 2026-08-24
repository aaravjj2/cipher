# Loop 060 plan — Locked guest panels, one boundary sentence

**Goal:** Every locked guest panel uses the same Guest boundary sentence.

**Why this loop exists:** PROGRAM 060. Six locked panels each invent a different lock reason. Guests should read one rule: no writes, no saved private state, no broker or LLM connection.

**Architecture:** One `GUEST_LOCKED_BOUNDARY` constant. Showcase shows it when `guestPanelMode(panel) === "locked"`. Drop per-panel `locked:` strings. Catalog modes unchanged. `data-guest-source` stays `demo` so hosted E2E still accepts the source.

**Files:**
- Modify: `web/src/components/panels/GuestShowcase.tsx`
- Create: `web/test/guest-locked-boundary.test.mjs`
- Create: `gates/loop-060-guest-locked-boundary.md`

**Preserve:** 29-panel catalog, 12 tickers, hybrid/live/demo modes, footer no-order sentence, earnings UNVALIDATED token, GEX heuristic on Matrix/Trident demos.

**Anti-goals:** No catalog/PanelHost change. No hosted E2E. No commit.

**Checks:** guest-locked-boundary + guest-catalog; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- `GUEST_LOCKED_BOUNDARY` is the only Guest boundary sentence. Showcase shows it when `guestPanelMode(panel) === "locked"`.
- Dropped six per-panel lock reasons. Per-panel `next` copy is unchanged.
- Catalog, PanelHost, and `data-guest-source="demo"` unchanged. No hosted E2E (not a catalog/PanelHost change).
- Web Node **107 pass / 0 fail**. Gates ALL MET. Sync OK.
