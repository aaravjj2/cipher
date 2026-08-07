# Access Obsidian / Cipher — Behaviors

Interaction sweep results, `https://www.accessobsidian.com/app`, 2026-08-06. Scope: 9 user-facing panels (admin panels excluded).

## Navigation — click-driven, no scroll/hover switching

Sidebar and header nav are plain `<button>` elements (not semantic `<nav>` links — no `@e` refs in accessibility tree, must target via DOM text match). Clicking a sidebar item swaps the main content area; previously-visited panels appear to stay mounted in the DOM (hidden, not unmounted) rather than being torn down — confirmed by button-text dumps accumulating entries from every panel visited in a session. **Implementation note for the clone: likely a client-side view-switch (CSS display toggle or route state) rather than full remounts — worth deciding intentionally rather than defaulting to Next.js route-per-page, since state (e.g. scan results) seems to persist across nav.**

## Panel-specific behaviors

### Night Vision (chart, canvas-rendered — extract via screenshot only, DOM has no chart internals)
- 5 independent toggle buttons (SP, SPY/QQQ, TS, VP, X-Ray) — **each is a standalone on/off toggle**, not a mutually-exclusive tab set. Confirmed: toggling VP does not affect SP/SPY-QQQ/TS/X-Ray state.
- SP → opens a right-side docked strike-matrix panel alongside the chart (adds ~300px right rail).
- VP → adds a volume-profile histogram overlay on the right edge of the chart itself (bars, not a side panel).
- Timeframe (1D/5m/More) and expiration-mode (1 Exp/Compact/Full/Leap) are click-driven single-select groups (button gets an active/highlighted style — `.on` class observed in DOM).
- Auto-refresh is a toggle button (15s cadence per its label), separate from the manual refresh icon button.

### Strike Matrix / Trident (heatmap tables)
- GEX/VEX is a 2-state exclusive toggle (not additive) — switching recolors/reshapes the whole matrix, confirmed via distinct screenshot sizes.
- Sniper vs Matrix mode changes layout meaningfully (Sniper adds a floating live-price-anchored side panel — seen reused in the Night Vision "SP" toggle, suggesting it's a shared component).
- Strike rows are sorted descending, with the spot price row pinned/highlighted inline via a dashed-line + label treatment (`SPOT 311.56`) rather than always sitting at a fixed table position — it moves to match the actual current spot each refresh.

### Spyglass / Bio / Contract Search
- All 3 share a header-level tab group (Bio, Contract Search) that only mounts while on the Spyglass panel — clicking Bio or Contract Search does NOT change the active sidebar item (Spyglass stays highlighted), confirming these are **sub-views of Spyglass**, not siblings of it in the routing hierarchy.
- Spyglass/Bio show an async "Scanning {X}..." loading state before results populate — real backend latency, not instant.
- Contract Search is the one static form-only view (Ticker/Strike/Call-Put/Date + Search button) — no live-scanning state, just an empty prompt until submitted.

### Setup Scanner
- **Long-running async scan**: clicking "Cipher Model Scan" triggers a full-universe scan (578 tickers observed) with a visible progress bar and live ticker count/percent, taking ~30-45s in practice. This is a real state worth replicating (progress bar + text), not just an instant loading spinner.
- Results render as a card grid (not a table) — rank badge, ticker, bullish/bearish tag pill, score/100, support/resistance levels, pull target, vacuum targets, and generated narrative text ("Cipher Read"). This is visually and structurally distinct from every other panel's data display (all others are tables) — treat as its own component pattern.
- Mode tabs (Short term/Long term/LEAP) and scan-type buttons (Liq scan/Cluster scan/Flash family) are separate, independent control groups above the results.

### My Watchlists / Journal / Chart Saves — simplest panels
- My Watchlists: plain editable table, add via text input + button, remove via inline × per row. No loading states observed (instant, client-side).
- Journal: calendar grid, day cells appear clickable (empty state — no populated day was available to inspect further this session).
- Chart Saves: card grid gallery, populated from Night Vision's "Save chart" action — each card is a static image + metadata, with a delete (×) affordance per card.

### Settings — not a data panel
- Static informational sections (plan tier, preferences, API connection instructions). Preferences use pill/chip single-select groups (auto-refresh interval: 5s/15s/30s). No loading states.

## Responsive (mobile, 390px)

- Sidebar collapses entirely to a hamburger icon (top-left); no visible rail at all at this width (confirmed — not just narrower, fully hidden).
- Header restructures from single-row to stacked rows.
- All data tables (Strike Matrix, Trident, Spyglass, Watchlists) become horizontally-scrollable containers — columns get clipped at the viewport edge with a scrollbar, not reflowed/stacked.
- Breakpoint not yet pinpointed to an exact pixel — only tested at 1440 and 390. Recommend testing 768px (tablet) during the build phase before finalizing breakpoint values.

## Not yet captured (acceptable gaps — low priority)

- Hover states (color/shadow/scale transitions on buttons, table rows, cards) — not systematically swept; screenshots don't capture hover. Builders should approximate standard hover treatment (slight brightness/border change) consistent with the dark theme and verify against live site spot-checks during Phase 5 QA rather than blocking Phase 2/3 on this.
- Exact scroll-trigger behavior (if any) for the fixed header — panels didn't require scrolling to test (content fit or used internal scroll containers, not page-level scroll).
