# Access Obsidian Recreation Comparison

Compared against `https://www.accessobsidian.com/app#AAPL` in Chrome on 2026-07-20.

## Local Run

- Core API: `http://127.0.0.1:8282`
- Web app: `http://127.0.0.1:8283/#AAPL`
- Python environment: `C:\Aarav\cipher\.venv`

## Verified Local AAPL State

- Quote: `AAPL $333.62`
- Matrix coverage: `3,376 contracts`, `10/10 expirations`, `16 strikes shown`
- Matrix DOM rendered: `176` strike cells
- Core `/api/matrix` returned HTTP `200`
- `node --check` passed for `app/public/app.js` and `app/server.mjs`

## Surface Parity

Matched visible modules:

- Strike Matrix
- Night Vision
- Spyglass
- My Watchlists
- Journal
- Trident
- Chart Saves
- Setup Scanner
- Settings

Added during this pass:

- WO Admin
- Mispricing Admin
- Storm Admin
- Bio
- Contract Search
- Access-style 116px rail / 55px terminal header sizing
- Header chart shortcut, Welcome Mars, Logout affordance
- Scanner direction filters: All, Bullish, Bearish, Neutral
- Scanner card UX with setup label, pivot, stretch, invalidation, runway clarity, and target chips
- Ranking Lab partial reconstruction from saved cluster/scanner ranks
- GPT Analyst login-only handoff profile

Existing added/recreated features retained:

- Node Galaxy
- SPY/QQQ split
- Trade tape
- Volume Profile
- X-Ray
- Capture
- Save chart
- Scanner modes: Cipher, liquidity, cluster, Flash, Flash Index, Flash Agentic
- Weight Lab and cluster backtest controls

## Implementation Notes

- Fixed initial `#AAPL` load/render behavior by rendering Strike Matrix immediately after matrix data arrives.
- Night Vision, bars, flow, and split-cache requests now hydrate after matrix render via settled background fetches.
- Added local Bio and Contract Search views backed by the loaded matrix.
- Added local admin surfaces backed by matrix summary and strongest strike exposure data.
- Restyled the shell and scanner to match captured Access Obsidian matrix/scanner screenshots.
- Verified Flash Index cards render from live local scan output with enriched setup fields.
- Aligned the Matrix default state to the captured original: `Compact` expirations + `All` strike window.
- Disabled browser caching for local static assets so UI updates are always reflected after reload.
- Added transient `fetchJson` retry/backoff for local 502/503/504 responses.
- Made Trident render panes progressively and pause background tab streams to reduce local core pressure during multi-tab QA.
- Added `/api/ranking-lab`, a dependency-free rank surrogate that reads saved full scans, cluster reports, and cluster snapshots.
- Added Ranking Lab UI showing source coverage, rank signal weights, cluster tier ordering, and GPT prompt context.
- Hardened SSE writes so browser reloads/closed tabs quietly stop streams instead of surfacing broken connection tracebacks.

## Latest QA Pass

Run on 2026-07-20 against the live local app and the captured/live original Cipher surfaces.

- Local canonical route: `http://127.0.0.1:8283/#AAPL`
- Matrix default verified: `Compact`, `All`, `AAPL`, `3,376 contracts`, `5/5 expirations`, `49 strikes shown`
- Geometry verified in Chrome: `55px` topbar, `116px` rail
- API checks passed: health, matrix, night vision, bars, flow, Flash Index scan, Cipher scan, Cluster scan, Weight Lab, backtest score
- Local view workflow checked: Matrix, Night Vision, Spyglass, Watchlists, Journal, Trident, Chart Saves, Setup Scanner, WO Admin, Mispricing Admin, Storm Admin, Settings
- Syntax checks passed: `node --check app/public/app.js`, `node --check app/server.mjs`
- Python tests: `pytest` collected `0` tests in this checkout

QA fixes made from this pass:

- Removed stale static-asset caching.
- Changed Matrix defaults from `Full / +/-6%` to original-style `Compact / All`.
- Added request retry/backoff for transient proxy failures.
- Changed Trident from all-or-nothing loading to progressive per-pane loading.
- Added stream pause on hidden tabs to prevent background QA tabs from overloading the local core.
- Added GPT Analyst as a no-API-key ChatGPT handoff:
  - Stores local analyst profile in browser `localStorage`
  - Builds a prompt from current Matrix, Scanner, Flow, and Ranking Lab state
  - Copies/downloads the prompt and opens `https://chatgpt.com/` for the user's logged-in session
  - Does not automate ChatGPT.com in the background or require an API key
- Added Ranking Lab:
  - Current local result: `550` rank rows from `25` saved files
  - Strongest current signals: Scanner score, Cluster strength, Setup tag count
  - Cluster order surfaced from saved ranks: quad, triple, battle
  - Endpoint verified through core and web proxy: `/api/ranking-lab`

## Shell parity pass (2026-07-20 evening)

Aligned local `cipher-system/app` chrome to the live Access Obsidian shell captured from the signed-in session:

- Header: menu toggle, brand `/` quote layout, `+ Watchlist`, workspaces, chart shortcut, **Welcome Mars!**, **Logout** (local stub)
- Rail: **WORKSPACE** → **CIPHER X** (Trident / Chart Saves / Setup Scanner with badges) → WO / Mispricing Admin → **ACCOUNT** → Settings
- Local-only Ranking Lab / GPT Analyst / Storm Admin moved under **LOCAL LAB**
- Matrix/Trident: **Snap to spot** + **Snap to golden**
- Setup Scanner filters: **All / Upside / Downside** (matching live labels)
- Brand titles: `CIPHER MY WATCHLISTS`, `CIPHER TRADING JOURNAL`, `CIPHER SETTINGS`

Local: `http://127.0.0.1:8283/#NBIS` (or `#AAOI`, `#GS`)

Still not 1:1 at model layer: proprietary Cipher/Flash weights, multi-expiration cluster execution, and remote auth/billing.

