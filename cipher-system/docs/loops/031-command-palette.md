# Loop 031 plan — Command palette keyboard and focus

**Goal:** Palette input and items must show gold keyboard focus/selection. Input currently uses `outline-none` with no replacement; items have no selected or `focus-visible` chrome.

**Why this loop exists:** PROGRAM 031. Cmd+K is a primary keyboard path.

**Architecture:** Class-only on `CommandPalette.tsx`. Keep `NAV_SECTIONS`, ticker ranking, `shouldFilter={false}`, Escape, and guest hiding in `page.tsx`. Mark the overlay as `role="dialog"` / `aria-modal` so screen readers treat it as a modal.

**Files:**
- Modify: `web/src/components/CommandPalette.tsx`
- Create: `web/test/command-palette-a11y.test.mjs`
- Create: `gates/loop-031-command-palette.md`

**Preserve:** `NAV_SECTIONS.flatMap`, `rankTickers`, `MAX_TICKER_RESULTS = 8`, `useCommandPaletteShortcut`, `Jump to a panel or ticker…`.

**Anti-goals:** Do not enable palette in guest. No new dependency. No commit.

**Checks:** focused palette test; full web Node; lint; typecheck; sync (signed-in surface, still in the published tree).

## Result 2026-08-23

Overlay is `role="dialog"` / `aria-modal`. Input has a gold inset `focus-visible` ring. Items use `data-[selected=true]` plus gold `focus-visible`. Guest still does not get the palette. Sync OK.

