# Loop 078 plan — Spyglass tape labelled scrollport

**Goal:** Spyglass flow and contract-search tapes scroll inside named regions. Keep `overflow-x-auto rounded-[10px]` and `min-w-[560px]` on contract search. Side inference stays ASK for buy.

**Why this loop exists:** PROGRAM 078. Tables are labelled; the `overflow-x-auto` wrappers are not, so the scrollport itself has no accessible name.

**Architecture:** `role="region"` + scrollport `aria-label` on the existing wrappers. Do not retune column min-widths or buy→ASK.

**Files:**
- Modify: `web/src/components/panels/Spyglass.tsx`
- Modify: `web/test/spyglass-a11y.test.mjs`
- Create: `gates/loop-078-spyglass-tape-scroll.md`

**Preserve:** `overflow-x-auto rounded-[10px]`, `min-w-[560px]`, `p.side === "buy" ? "ASK"`, heatmap 26px.

**Anti-goals:** No FlowTape geometry change, no catalog/PanelHost, no commit.

**Checks:** spyglass-a11y + heatmap-accessibility; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Flow prints and contract-search tapes are named `role="region"` scrollports.
- Kept `overflow-x-auto rounded-[10px]` and `min-w-[560px]`. Buy-side inference still ASK.
- Web Node **109 pass / 0 fail**. Lint, typecheck, sync OK.
