# Loop 055 plan — Options workbench Overview has no order ticket

**Goal:** Ticker Workbench Overview states there is no order ticket for both guest and signed-in. Options jump must not say “executable”.

**Why this loop exists:** PROGRAM 055. Guest Options tile already says “No order ticket.” Signed-in Overview still says “Executable bid/ask research,” which reads as an order surface. Options Terminal already has the kicker; Overview did not.

**Architecture:** One Options jump body for both modes. Signed-in Evidence readiness adds “No order ticket.” Do not add a ticket, broker client, or Alpaca path.

**Files:**
- Modify: `web/src/components/panels/TickerWorkbench.tsx`
- Create: `web/test/workbench-no-order-ticket.test.mjs`
- Create: `gates/loop-055-workbench-no-order-ticket.md`

**Preserve:** Guest tabs Overview/Chart/Options only; Flow/Company/Agent locked; `research only`; 12 guest tickers; no order surface.

**Anti-goals:** No catalog/PanelHost change. No hosted E2E. No commit.

**Checks:** workbench-no-order-ticket + ticker-workbench-ui + product-hardening workbench test; full web Node; lint; typecheck; sync.
