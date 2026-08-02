# Cipher — Agent Constraints

These instructions apply to `/home/aarav/Aarav/cipher`.

## Project Identity

- Personal-use, private, local-only options research terminal.
- Active product is `cipher-system/`.
- Not a commercial product and not for redistribution.
- Clean-room reconstruction only. Do not copy proprietary AccessObsidian,
  APEX, Hermes, Fincept, or commercial Cipher internals.
- Read-only intelligence in v1. No auto-execution and no order endpoints.

## Active Runtime

The active app is:

| Path | Role |
|---|---|
| `cipher-system/core/app.py` | Python read-only market-data/research API |
| `cipher-system/app/server.mjs` | Node same-origin proxy and static server |
| `cipher-system/app/public/` | Browser UI |
| `cipher-system/core/scanner.py` | Setup Scanner and Flash/Cluster/Liq scoring |
| `cipher-system/core/cluster_backtest.py` | Local scan capture and forward scoring |
| `cipher-system/core/ranking_lab.py` | Local rank-surrogate diagnostics |
| `cipher-system/core/weight_lab.py` | Local weight fitting/dumps |
| `cipher-system/core/gex_capture.py` | Local GEX history capture job |
| `cipher-system/core/research_platform/` | Governance, provenance, experiment, promotion, and prospective-validation plane |
| `cipher-system/core/paper_executor/` | Separate shadow/paper simulation runtime; no broker orders |
| `tests/` | Smoke and safety tests for the active app |

The root `api/`, `relay/`, `shared/`, `storage/`, `exposure_engine/`,
`night_vision/`, `spyglass/`, and `execution/` scaffold is stale unless a user
explicitly asks to revive it.

## Market Data Source

Alpaca is the active market-data source for this checkout.

- Options: Alpaca OPRA where available, `indicative` only for UI/debug fallback.
- Stocks: Alpaca SIP preferred, IEX fallback.
- Credentials stay server-side in `cipher-system/app/.env` or environment
  variables. The browser must never receive raw keys or secrets.
- Do not add Tradier relay code unless the user explicitly asks to migrate away
  from the current Alpaca app.

Accepted credential names:

```env
ALPACA_ALGO_PLUS_KEY=
ALPACA_ALGO_PLUS_SECRET=
ALPACA_ALGO_KEY=
ALPACA_ALGO_SECRET=
ALPACA_API_KEY=
ALPACA_API_SECRET=
ALPACA_DATA_FEED=opra
ALPACA_STOCK_FEED=sip
```

## Required Options Data

The app depends on:

- Option chain snapshots with latest quote, latest trade, greeks, IV, daily
  volume, and pagination.
- Option contract metadata with open interest and open-interest date.
- Underlying stock latest quote/trade for spot context.
- Underlying stock OHLCV bars for Night Vision and backtests.
- Latest option trades plus bid/ask for Spyglass premium tier and side inference.

## GEX History Capture

Use `cipher-system/core/gex_capture.py` for local GEX history accumulation. It
captures the same Alpaca-backed matrix payload the UI uses, writes raw JSON under
`cipher-system/data/gex_snapshots/`, and normalizes snapshots/cells into
`cipher-system/data/gex_history.sqlite`.

PowerShell entrypoints:

```powershell
.\cipher-system\scripts\Capture-GexSnapshot.ps1 -Ticker SPY
.\cipher-system\scripts\Capture-GexSnapshot.ps1 -All
.\cipher-system\scripts\Start-GexCaptureLoop.ps1 -IntervalMinutes 15
```

`-All` defaults to the cap-filtered universe tiers `mega,large,medium`, excluding
`small` and `unknown`.

## GEX Formula

Do not silently change this convention:

```python
call_gex =  call_gamma * call_oi * 100 * spot**2 * 0.01
put_gex  = -put_gamma  * put_oi  * 100 * spot**2 * 0.01
net_gex  = call_gex + put_gex
```

GEX is a public-OI heuristic, not verified dealer positioning. UI copy,
comments, docs, and API responses must preserve that caveat.

Missing gamma or open interest is unknown. Do not convert missing inputs to zero
except as an explicitly labelled visual placeholder.

## Execution Boundary

- No live order submission code.
- The browser/core research terminal remains read-only.
- `core/paper_executor/` and forward-test modules may simulate entries, exits,
  fills, positions, and portfolio constraints, but must not contain a broker
  client or submit an order.
- Governance promotion stops at `LIVE_REVIEW_REQUIRED`; that state does not
  authorize live execution.
- No scheduled live-order runner.
- No `/v2/orders`, `submit_order`, `place_order`, `create_order`,
  `TradingClient`, or `OrderClient` usage in active files.
- Any future broker adapter requires a separately authorized package, explicit
  human review, and an updated project boundary. It must not be inferred from
  paper eligibility or governance state.

## Coding Standards

- Keep changes focused on `cipher-system/` unless updating root docs/tests.
- Prefer the existing standard-library HTTP server style in `core/app.py` unless
  the user requests a framework migration.
- Preserve local caches and rate-conscious polling behavior.
- Handle network/data failures explicitly; market-data gaps are normal.
- Keep secret handling server-side and masked in UI.
- Document formula, threshold, and reconstruction choices inline when changing
  scanner/GEX/flow behavior.

## Testing

Run the lightweight checks after active-app edits:

```bash
python3 -m compileall -q cipher-system/core tests
node --check cipher-system/app/server.mjs
node --check cipher-system/app/launcher.mjs
node --check cipher-system/app/public/app.js
pytest -q
```

The tests assume a local core may or may not be running. Network/live-data tests
should skip cleanly when the app is offline.

## Defaults

| Setting | Default |
|---|---|
| Core port | 8282 |
| Web port | 8283 |
| Options feed | `opra` |
| Stock feed | `sip`, fallback `iex` |
| Matrix expirations | 12 |
| Full matrix expirations cap | 36 |
| Spyglass tiers | Small $20-50k, Medium $50-150k, Large $150-500k, Whale $500k+ |
| Scanner concurrency | 1 worker to avoid Alpaca 429s |

## Historical/Archive Areas

- `cipher-system/previous-work/` is archived/reference material.
- `cipher-system/previous-work/external/` is vendor/reference bulk and should
  not be searched or edited unless explicitly needed.
- `.env` files, local databases, cached data, and browser artifacts are not to
  be committed or printed.
