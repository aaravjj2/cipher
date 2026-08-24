# Loop 056 plan — Yahoo feed labelled delayed

**Goal:** When the quote or chain feed is Yahoo/yfinance, the UI says **delayed**, never **live**.

**Why this loop exists:** PROGRAM 056. Backend already marks Yahoo as delayed. Workbench, Options Terminal, Night Vision snapshot, and Research Desk print the raw `yahoo` token. Guest Overview still appends “live quote” even on a Yahoo fallback.

**Architecture:** One `feedLabel` helper. Header gets the quote feed so guest chrome can say delayed vs live. Do not rename the API token (`yahoo`). Do not change yfinance_provider.

**Files:**
- Create: `web/src/lib/feedLabel.ts`
- Modify: `TickerWorkbench.tsx`, `OptionsTerminal.tsx`, `NightVision.tsx`, `ResearchDesk.tsx`, `Header.tsx`, `app/page.tsx`
- Create: `web/test/yahoo-feed-delayed.test.mjs`
- Create: `gates/loop-056-yahoo-feed-delayed.md`

**Preserve:** Guest tape demo copy; header 32px icons; no order surface; API `feed: "yahoo"`.

**Anti-goals:** No catalog change. No hosted E2E. No commit. No broker edits.

**Checks:** yahoo-feed-delayed + guest-tape-live-quote + options-terminal-ui; full web Node; lint; typecheck; sync.
