# Loop 089 plan — Workbench Options is the live chain

**Goal:** Guest Ticker Workbench → Options mounts the live `OptionsTerminal` chain and says so. It must not look like the demo Options Terminal nav showcase.

**Why this loop exists:** PROGRAM 089. The Options tab already mounts `OptionsTerminal`, but guests get no "live chain" label, so 088's demo-nav copy has no matching live surface.

**Architecture:** Keep mounting `OptionsTerminal` on the Options tab. For guests, add an explicit live-chain caption. Overview copy names Workbench Options as the live chain vs the demo nav item. Do not add Flow. Do not change guestCatalog or PanelHost.

**Files:**
- Modify: `web/src/components/panels/TickerWorkbench.tsx`
- Modify: `web/test/ticker-workbench-ui.test.mjs`
- Modify: `web/test/workbench-no-order-ticket.test.mjs`
- Create: `gates/loop-089-workbench-options-live-chain.md`

**Preserve:** Guest tabs Overview/Chart/Options only; no order ticket; Flow/Company/Agent stay `!guestMode`; Yahoo delayed labelling.

**Anti-goals:** No catalog/PanelHost, no Flow for guests, no commit.

**Checks:** ticker-workbench-ui + workbench-no-order-ticket + guest-catalog; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Guest Options tab still mounts live `OptionsTerminal` and now says “Live chain · not the Options Terminal demo nav”.
- Overview names Workbench Options as the live chain vs the demo nav item. Flow stays locked. No order ticket.
- Catalog/PanelHost unchanged. Web Node 110 pass / 0 fail. Gates ALL MET. Sync OK.

