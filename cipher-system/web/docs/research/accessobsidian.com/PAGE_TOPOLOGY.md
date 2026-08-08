# Access Obsidian / Cipher — Page Topology

Target: `https://www.accessobsidian.com/app` — single-page app, client-side routed via URL hash (`#TICKER`, e.g. `#AAPL`).
Scope: 9 user-facing panels. WO Admin / Mispricing Admin / Storm Admin explicitly EXCLUDED (internal security/anti-abuse tooling, not for public repo — user decision).

## Global Shell (present on every panel)

- **Header** (fixed, full-width): logo (CIPHER wordmark + obsidian-icon image) + active panel name, ticker search box (`AAPL`), live quote (`$311.56 +0.18%`), workspace tabs (`1` / `2`), Logout. Panel-specific action buttons appear here contextually (e.g. Night Vision's chart toggles, Spyglass's Bio/Contract Search links).
- **Sidebar** (fixed left, collapsible via chevron button): grouped nav —
  - WORKSPACE: Strike Matrix, Night Vision, Spyglass, My Watchlists, Journal
  - CIPHER X (premium-tagged): Trident, Chart Saves, Setup Scanner
  - ACCOUNT: Settings
- **Mobile (<~600px)**: sidebar collapses to a hamburger icon top-left; header restructures to stacked rows; data tables become horizontally scrollable containers (columns clipped, scroll to reveal).
- Interaction model: **click-driven** — sidebar/header nav items are `<button>` elements with onClick handlers (no scroll-driven or hover-driven panel switching observed at the nav level).

## Panels (in sidebar order)

1. **Strike Matrix** — options gamma/premium heatmap table. Rows = strikes, columns = expirations. Cell color intensity/hue encodes value magnitude (purple=positive/call-heavy, red=negative/put-heavy, gold=highlighted "top pull" strike). Header row controls: Full/Compact/Sniper density toggle, ±3%/±6%/±12%/All strike-range filter, GEX/VEX metric toggle, Matrix/Sniper mode. Spot price row is pinned/highlighted inline (`SPOT 311.56`).
2. **Night Vision** — candlestick chart (canvas-rendered, not in accessibility tree — must extract via screenshot only). Header controls: timeframe (1D/5m/More→1m/15m/1H/4H/1W/EOD), expiration mode (1 Exp/Compact/Full/Leap), overlay toggles (SP strike panel, SPY/QQQ split, TS trade tape, VP volume profile, X-Ray matrix overlay), Capture/Save chart buttons, hot-strikes legend (Top pull/Above spot/Below spot dots) with Refresh + Auto-refresh(15s) controls.
3. **Spyglass** — live order-flow scanner table (Ticker/Time/Size/Contracts/Px/Strike/Expiration/C-P/Bid-Ask/%OTM columns), shows "Scanning {TICKER}..." loading state. Filters: trade date, premium-size buckets (≤$0.50/≤$0.25/≤$0.10/All px), size buckets ($5k/$10k/$25k/$50k/$100k), Calls/Puts/All, Bid/Ask/Ask/Bid, Moneyness/OTM/ITM. Header gains two extra links here: **Bio**, **Contract Search**.
   - **Bio** (sub-view of Spyglass's header nav): "Bio Flow" — same table shape, scans "pharma, biotech & medtech" specifically. Filters: %OTM buckets (≥5%/10%/25%/50%), size buckets, Calls/Puts/All, Rescan button.
   - **Contract Search**: not yet captured (transient header link, only mounted in certain Spyglass sub-states — revisit).
4. **My Watchlists** — simple ticker table (Ticker/%Change/$Change/Price/Compact-100-score), add-ticker input + button, remove (×) per row.
5. **Journal** — monthly calendar grid (Sun–Sat columns), month P&L summary badge, Save-image button, day cells (clickable, empty in current data).
6. **Trident** *(CIPHER X)* — 3-column side-by-side gamma matrix for SPY/QQQ/IWM simultaneously, each an independent scrollable strike table with its own spot-price pin. Controls: GEX/VEX, expiration selector, Snap-to-spot/golden, FC, Auto, TR, SP toggles.
7. **Chart Saves** *(CIPHER X)* — masonry/grid gallery of saved chart snapshot cards (image + ticker/price/view/top-levels metadata + delete ×), populated from Night Vision's Save-chart action.
8. **Setup Scanner** *(CIPHER X)* — scan configuration + results view. Mode tabs (Short term/Long term/LEAP), primary CTA ("Cipher Model Scan"), secondary scan buttons (Liq scan, Cluster scan, Flash/Flash Index/Flash Agentic — each BETA-tagged), cluster-expiration selector. Empty state: "Pick a timeframe and run a scan..."
9. **Settings** — account/config page, NOT a data panel. Sections: "Your plan" (tier badge, currently CIPHER X), "Preferences" (timezone, auto-refresh interval chips: 5s/15s/30s), "Connect API" (Alpaca key connection flow with numbered instructions, CONNECTED status badge, explicit "keys never leave your browser" disclosure copy).

## Not in scope (excluded)

WO Admin, Mispricing Admin, Storm Admin — mount asynchronously in sidebar between Setup Scanner and Settings (permission-gated, not visible on initial render). Contain internal monitoring toggles (New IP, Logins, DB writes, Tamper, Devtools, Scrape). Excluded per user decision — not to be cloned into any public-facing repo.

## Open items for next pass

- Contract Search view (not yet screenshotted — header link disappears/reappears depending on Spyglass sub-state)
- Setup Scanner's actual scan-results state (only empty state captured so far — need to trigger a scan and capture populated results)
- Night Vision overlay toggle states (SP/SPY-QQQ/TS/VP/X-Ray) individually
- Strike Matrix Sniper mode + GEX vs VEX toggle states
