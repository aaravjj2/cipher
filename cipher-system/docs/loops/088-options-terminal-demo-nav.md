# Loop 088 plan — Options Terminal nav stays demo showcase

**Goal:** Guest Options Terminal in the nav remains a labelled demo showcase. It must not be mistaken for the live chain (Ticker Workbench → Options).

**Why this loop exists:** PROGRAM 088. Catalog mode is already `demo` and PanelHost already routes demo panels to GuestShowcase. The showcase copy still reads like a live structure terminal and never says the live chain lives on Ticker Workbench Options.

**Architecture:** Update GuestShowcase copy only. Keep catalog mode `demo`. Do not change PanelHost guest filter. Do not mount `OptionsTerminal` from this nav item for guests.

**Files:**
- Modify: `web/src/components/panels/GuestShowcase.tsx`
- Modify: `web/test/guest-catalog.test.mjs`
- Create: `gates/loop-088-options-terminal-demo-nav.md`

**Preserve:** 29 guest panels, Options Terminal `mode: "demo"`, PanelHost `guestModeType !== "hybrid" && !== "live"` → GuestShowcase, no order ticket, Workbench Options still mounts live `OptionsTerminal` (loop 089).

**Anti-goals:** No catalog/PanelHost edits, no promoting this nav item to hybrid/live, no commit.

**Checks:** guest-catalog + options-terminal-ui; full web Node; lint; typecheck; sync.

## Result 2026-08-23

- Guest Options Terminal showcase now says this nav item is demo, not the live chain.
- Points guests to Ticker Workbench → Options for the live chain. Catalog mode stays `demo`. PanelHost filter unchanged.
- Web Node 110 pass / 0 fail. Gates ALL MET. Sync OK.

