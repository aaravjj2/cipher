# Loop 001 plan — Ticker Workbench shell

**Goal:** Make the hybrid Ticker Workbench match the Skew Map / Options Terminal workstation chrome, and close a guest capability leak on Overview.

**Why this loop exists (evidence):** Headed screenshot `artifacts/options-terminal-guest-workbench.png` (2026-08-23) showed the new Options Terminal inside a workbench whose tablist and Overview tiles still used `rounded-lg` / `rounded-xl` cards. Source also always renders Flow/Company/Agent jump tiles, so a guest can `setTab("Flow")` even though `visibleTabs` omits those tabs. Guest copy says flow, company, and agent require sign-in.

**Architecture:** Preserve-mode redesign only. Same fetch, same six tabs for signed-in users, same three tabs for guests, same child panels. Fix guest Overview to jump only to Chart and Options. Fix `End` to use `visibleTabs`, not `TABS`.

**Files:**
- Modify: `web/src/components/panels/TickerWorkbench.tsx`
- Create: `web/test/ticker-workbench-ui.test.mjs`
- Create: `gates/loop-001-ticker-workbench.md`

**Preserve:**
- `role="tablist"` / `role="tab"` / `role="tabpanel"` and ids
- ArrowLeft/ArrowRight/Home/End
- Guest fallback to `GuestShowcase` when quote fails
- `data-guest-panel` / `data-guest-source`
- Positive/negative on day change only
- Options Terminal / Night Vision children unchanged
- `research only` copy
- No order identifiers

**Anti-goals:** Do not restyle Night Vision/Spyglass internals. Do not add tabs. Do not change quote API. Do not commit.

**Implementation sketch:**
1. Shared control classes: border, `h-8`/`px-3`, `focus-visible:ring-[var(--gold)]`.
2. Header + price: `font-sans` title, `tabular-nums` price, missing price `—`.
3. Tablist: border, no rounded-lg; selected tab uses `--nav-active` and gold focus.
4. Guest Overview: Chart + Options tiles only. Signed-in: existing six destinations including journal.
5. Readiness grid: border cells, status pill may remain `rounded-full` (DESIGN.md allows pills for status).
6. `End` → `visibleTabs.length - 1`.

**Checks:**
- `node --test web/test/ticker-workbench-ui.test.mjs web/test/product-hardening.test.mjs`
- `cd web && npm run lint && npm run typecheck`
- `sync_web_build.sh` after build

## Result 2026-08-23

- Guest Overview no longer jumps to Flow/Company/Agent; those panels render only when `!guestMode`.
- `End` now indexes `visibleTabs`.
- Chrome matches DESIGN.md border surfaces; status READY remains a pill.
- Web Node 67 passed / 0 failed. Lint and typecheck passed. Published tree in sync.
- Hosted guest E2E 2 passed (24.9s).
- `gates/loop-001-ticker-workbench.md` ALL MET (3).
- Not committed.
