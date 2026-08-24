# Loop 067 plan — Headed workbench Options overflow

**Goal:** Prove guest Ticker Workbench → Options does not blow page-level overflow. Guest catalog E2E measures overflow on the last panel, not on the live chain.

**Why this loop exists:** PROGRAM 067. The option chain table is `min-w-[1180px]` with a sticky header. That must stay an internal labelled scrollport (`overflow-auto`), not document overflow.

**Architecture:** Source-lock the chain region in `options-terminal-ui.test.mjs`. Add hosted Playwright at 1440×900 and 390×844: guest → Ticker Workbench → Options tab → `scrollWidth - clientWidth ≤ 1`. Do not change heatmap cells or catalog.

**Files:**
- Modify: `web/test/options-terminal-ui.test.mjs`
- Create: `web/e2e/workbench-options-overflow.spec.ts`
- Create: `gates/loop-067-workbench-options-overflow.md`

**Preserve:** No order ticket, guest tabs Overview/Chart/Options, `data-testid="options-terminal"`, OI-as-of copy, 12 tickers.

**Anti-goals:** Do not retarget nav Options Terminal (still demo). No commit. No geometry edits.

**Checks:** options-terminal-ui; hosted workbench-options-overflow; full web Node.

## Result 2026-08-23

- Source locks Option chain as `overflow-auto` + `min-w-[1180px]` + sticky thead.
- Hosted guest Workbench → Options: **2 passed** (1440×900 and 390×844), page overflow ≤ 1px.
- No product file change. Web Node **108 pass / 0 fail**. Nav Options Terminal remains demo.
