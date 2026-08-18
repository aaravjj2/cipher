# AccessObsidian Clean-Room Parity Fix Report

Date: 2026-07-20

## Scope

Implemented safe local parity fixes for the active Cipher build in `cipher-system` using the saved AccessObsidian reference captures under `/home/aarav/accessobsidian-reference/`.

This is a clean-room UI/behavior parity pass. It does not copy private source, private prompts, private weights, credentials, cookies, tokens, or proprietary AccessObsidian internals.

## Reference captures used

Captured through the Cowork Browser Agent extension-connected signed-in Chrome profile only:

- `strike-matrix.png/json`
- `night-vision.png/json`
- `spyglass.png/json`
- `watchlists.png/json`
- `journal.png/json`
- `trident.png/json`
- `chart-saves.png/json`
- `setup-scanner.png/json`
- `settings.png/json`
- `summary.json`

## Files changed

- `cipher-system/app/public/app.js`
- `cipher-system/app/public/styles.css`
- `tests/test_cipher_local_smoke.py`
- `PARITY_FIX_REPORT.md`

## Implemented fixes

### My Watchlists

Before: local card grid with ticker, price/feed, and dominant level.

After: AccessObsidian-style table:

- TICKER
- % CHANGE
- $ CHANGE
- PRICE
- COMPACT
- delete `×`

Functional verification after refresh:

- SPY, QQQ, IWM, NVDA, AAPL populated with price/change/compact values.
- Compact remains local dominant exposure level from public-OI reconstruction.

### Journal

Before: form + simple table.

After: calendar/month layout:

- MONTH P&L header
- Save image action
- Date / Result / Amount / Notes form
- SUN-SAT calendar grid
- Day-level P&L rendering when entries exist

### Setup Scanner

Before: local scanner controls with nearest expiration only.

After: closer captured layout:

- Short term / Long term / LEAP
- Cipher Model Scan
- Liq scan / Cluster scan
- Flash BETA / Flash Index BETA / Flash Agentic BETA
- Direction filters
- Captured cluster-expiration pill row:
  - Nearest (1 Exp)
  - Fri Jul 24
  - Fri Jul 31
  - Fri Aug 7
  - Fri Aug 14
  - Fri Aug 21
  - Fri Aug 28
  - Fri Sep 4
  - Fri Sep 11

Safety note: execution remains locked to nearest expiration in the local scanner. Non-nearest pills are parity UI only until corresponding local API support is implemented.

### Settings

Before: local data status/settings cards.

After: closer captured layout while preserving local secret safety:

- `Your plan` card with `CIPHER X`
- Plan & connection card
- Live refresh card
- Alpaca data card with:
  - API KEY ID: CONNECTED
  - API SECRET KEY: masked
  - Connect / Disconnect controls shown disabled
  - explanation that secrets remain server-side and redacted
- Weight lab, backtest, and disclosure cards retained

### Chart Saves

Before: thin local cards with ticker/view/note only.

After: AccessObsidian-style rich saved chart cards:

- screenshot/thumbnail preview from the Night Vision canvas when available
- DATE ADDED
- TICKER
- PRICE
- VIEW
- TOP LEVELS with normalized scores
- top-right delete `×`

Browser verification saved an AAPL Night Vision chart and confirmed the Chart Saves view rendered `DATE ADDED`, `$AAPL`, price, `1 Exp`, and top levels such as `330 100`, `335 88`, `340 66`, `337.5 36`.

### Tests

Added `tests/test_cipher_local_smoke.py` and `tests/test_cipher_safety_guardrails.py`.

Coverage:

- core health is OK/read-only
- matrix endpoint returns rows/formula/public-OI caveat
- scan universe is populated
- ranking lab reports clean-room reconstruction caveat
- frontend parity tokens exist in app source/styles
- active app/core files do not expose order-execution surfaces
- settings keep credentials masked/server-side

Also added `cipher-system/pytest.ini` so running pytest from inside `cipher-system` uses the root tests and avoids collecting `previous-work/external` vendor/reference trees.

## Verification run

Command:

```bash
cd /home/aarav/Aarav/cipher
python3 -m compileall -q api cipher-system/core cipher-system/mcp-server tests
node --check cipher-system/app/server.mjs
node --check cipher-system/app/launcher.mjs
node --check cipher-system/app/public/app.js
pytest -q
curl -s http://127.0.0.1:8282/health
curl -s http://127.0.0.1:8283/api/health
```

Result:

- Syntax checks passed.
- Root `pytest -q`: `7 passed in 2.96s`.
- `cipher-system` `pytest -q`: `7 passed in 0.03s`.
- Core health OK.
- Web proxy health OK.
- `read_only=true`.
- `market_data_configured=true`.

## Browser verification

Verified local UI at:

- `http://127.0.0.1:8283/#AAPL`

Views checked:

- My Watchlists: table with populated values after refresh.
- Journal: calendar/month P&L UI renders.
- Setup Scanner: controls and cluster-expiration pills render.
- Settings: CIPHER X plan, API key status/masked secret, and disclosure render.

## Remaining known parity gaps

These are not safe one-pass fixes without more backend/API work:

1. Non-nearest cluster expiration execution
   - UI now shows captured expiration choices.
   - Backend scanner still executes nearest expiration only.

2. Pixel-perfect visual parity
   - Major layout/content gaps are improved.
   - Exact fonts/spacing/animations still differ.

3. Proprietary model parity
   - Local ranking remains an explicit clean-room reconstruction.
   - Do not claim or copy proprietary AccessObsidian internals.

4. Account/auth behavior
   - Local app does not and should not implement Discord/account billing/auth flows unless a separate safe local auth design is requested.

5. More automated browser tests
   - Smoke tests now exist.
   - Full Playwright-style view assertions can be added next if desired.
