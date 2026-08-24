# Loop 038 plan — dialog and drawer overscroll contain

**Goal:** Overlay dialogs and the mobile nav drawer keep wheel/touch overscroll inside themselves (`overscroll-behavior: contain`) so the workspace behind them does not scroll.

**Why this loop exists:** PROGRAM 038. No `overscroll-behavior` in `web/src`. Command palette list, Night Vision evidence dialog, and the mobile sidebar drawer all have nested scrollports.

**Architecture:**
- Global `[role="dialog"] { overscroll-behavior: contain; }` in `globals.css` so every dialog root is covered.
- Tailwind `overscroll-contain` on the actual overflow scrollports: Command palette list, mobile drawer inner scroller, mobile drawer shell.
- Do not retune heatmap `overflow-x-auto rounded-[10px]` / `rounded-[8px]` contracts. Do not add overscroll to in-panel tables (Strike Matrix, Trident, Spyglass).

**Files:**
- Modify: `web/src/app/globals.css`, `web/src/components/CommandPalette.tsx`, `web/src/components/Sidebar.tsx`, `web/src/components/panels/NightVision.tsx` (EvidenceDrawer class only)
- Create: `web/test/overlay-overscroll.test.mjs`
- Create: `gates/loop-038-dialog-overscroll.md`

**Preserve:** Guest cannot open the palette. Sidebar guest nav filter. Night Vision geometry. Side inference (Spyglass). No order surface.

**Anti-goals:** No live broker, no commit, no 039 skip-link work in this loop.

**Checks:** overlay-overscroll + command-palette-a11y + sidebar-a11y + heatmap-accessibility; full web Node; lint; typecheck; sync. Hosted guest E2E (fifth UI loop since 034).
