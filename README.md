# Cipher

Local, read-only options research terminal for personal use. The active app lives in
`cipher-system/` and provides Strike Matrix, Night Vision, Spyglass, scanners,
watchlists, journal, chart saves, and local ranking/weight labs.

This repository is not a commercial product and is not integrated with APEX,
Hermes, Fincept, AccessObsidian internals, or any proprietary Cipher clone.

## Active Architecture

```
              browser UI
       cipher-system/app/public
                    |
                    v
        Node same-origin proxy
       cipher-system/app/server.mjs
                    |
                    v
        local read-only core API
        cipher-system/core/app.py
                    |
        +-----------+-------------+
        |                         |
        v                         v
 Alpaca market data        local research files
 OPRA options              scanner/backtest/weights
 SIP/IEX stocks            cipher-system/data/
```

## Research Governance Platform

Cipher now includes a separate governance and strategy-graduation plane under
`cipher-system/core/research_platform/`. It preserves the existing terminal and
collectors while adding immutable manifests, frozen dataset snapshots,
point-in-time feature policies, standardized experiments, LEAN audit gates,
prospective validation, deterministic risk review, and evidence reconciliation.

Initialize and inspect it with:

```bash
/home/aarav/.venvs/cipher/bin/python cipher-system/scripts/run_research_platform.py init
/home/aarav/.venvs/cipher/bin/python cipher-system/scripts/run_research_platform.py import-current-evidence
/home/aarav/.venvs/cipher/bin/python cipher-system/scripts/run_research_platform.py status
```

The core API exposes governance status read-only at `/api/governance`.

The maximum promotion state is `LIVE_REVIEW_REQUIRED`. The platform contains no
broker adapter or live-order path. See
`cipher-system/core/research_platform/README.md` and
`CIPHER_CURRENT_STATE_AND_HYBRID_ARCHITECTURE_THESIS.md` for the complete design.

## Current Data Model

Cipher currently uses Alpaca credentials server-side only. The browser never sees
keys.

| Surface | Data Used |
|---|---|
| Strike Matrix | Alpaca OPRA option snapshots joined to option-contract open interest |
| Night Vision | Strike Matrix exposure levels plus stock OHLCV bars |
| Spyglass | Latest option trades from chain snapshots, classified against bid/ask |
| Scanner | Matrix-derived GEX/VEX, OI, volume, support/resistance, and local scoring |
| Backtest/ranking lab | Saved local scan snapshots plus historical stock bars |

GEX is a public open-interest heuristic, not verified dealer positioning.

Formula:

```python
call_gex =  call_gamma * call_oi * 100 * spot**2 * 0.01
put_gex  = -put_gamma  * put_oi  * 100 * spot**2 * 0.01
net_gex  = call_gex + put_gex
```

Missing gamma or open interest remains unknown. Do not silently treat missing
inputs as zero.

## Historical Data

Alpaca provides historical options bars and trades through API endpoints, with
historical options coverage documented from February 2024 onward. These are
downloadable by paging API responses and writing them to local CSV, JSONL,
Parquet, or SQLite.

Important limitation: the current app computes GEX from current option snapshots
and current contract open interest metadata. Alpaca's historical option bars and
trades are enough to replay price/volume flow, but they are not a complete
historical GEX replay by themselves unless historical greeks and historical open
interest are also captured or sourced.

Practical download targets:

| Dataset | Use |
|---|---|
| Historical option bars | option OHLCV by contract for flow/price replay |
| Historical option trades | trade tape by contract for Spyglass-style replay |
| Current option chain snapshots | live/latest quote, trade, greeks, IV |
| Current option contracts | open interest and OI date |
| Historical stock bars | Night Vision candles and forward/backtest scoring |

For true historical GEX, add a local capture job that snapshots the live chain
and contract OI during market sessions, then persists those raw payloads before
deriving matrix rows.

This repo includes that capture path:

```powershell
cd C:\path\to\cipher\cipher-system
py -3 -m pip install -r ..\requirements.txt

# Smoke test one ticker.
.\scripts\Capture-GexSnapshot.ps1 -Ticker SPY -Feed opra -Expirations 1

# Capture the default optionable universe, excluding small and unknown caps.
.\scripts\Capture-GexSnapshot.ps1 -All -Feed opra -Expirations 1

# Keep capturing every 15 minutes until stopped.
.\scripts\Start-GexCaptureLoop.ps1 -Feed opra -Expirations 1 -IntervalMinutes 15
```

The default universe uses `cipher-system/data/optionable_universe_by_cap.json`
tiers `mega,large,medium`, excluding `small` and `unknown`. Captures are written
to `cipher-system/data/gex_history.sqlite` plus raw JSON under
`cipher-system/data/gex_snapshots/`; both are local generated data and ignored by
git.

### Historical Flow-Cluster Backtests

True historical GEX still needs historical per-contract open interest. When that
is unavailable, Cipher can separately test **flow clusters** from historical
gamma-weighted option prints. This is a different signal family:

```python
flow_call =  call_gamma * call_volume * 100 * spot**2 * 0.01
flow_put  = -put_gamma  * put_volume  * 100 * spot**2 * 0.01
```

Use London Strategic Edge as the primary historical-flow source by setting
`LSE_API_KEY` server-side/local only, then download rows and backtest:

```powershell
cd C:\path\to\cipher\cipher-system

# Download a small sample.
.\scripts\Download-FlowClusterData.ps1 -Ticker SPY,QQQ -Start 2026-07-20 -End 2026-07-20 -MinPremium 25000

# Build historical flow clusters and score 1/3/5-day forward behavior.
.\scripts\Run-FlowClusterBacktest.ps1 -Ticker SPY,QQQ -Start 2026-07-20 -End 2026-07-20

# Prefer the local "Stock data\data\5m" CSVs for forward stock-bar scoring.
.\scripts\Run-FlowClusterBacktest.ps1 -Ticker SPY,QQQ -Start 2026-07-14 -End 2026-07-14 -BarProvider local -LocalTimeframe 5m

# Optional: price representative option contracts with future option candles.
.\scripts\Run-FlowClusterBacktest.ps1 -Ticker SPY -Start 2026-07-20 -End 2026-07-20 -PriceOptions
```

Use `-All -Limit 25` first before attempting the whole cap-filtered universe.
The generated cache is `cipher-system/data/flow_cluster_history.sqlite`; reports
land under `cipher-system/data/flow_clusters/`. Local stock bars are discovered
from `Stock data/data/{1m,5m,15m}/{TICKER}.csv` and resampled to daily ranges for
touch/forward scoring. They do not replace the historical option-flow source.

Kronos (`Stock data/external/Kronos`) is useful as an optional candlestick
forecast/regime filter, not as an options-flow or GEX source. It requires a
separate model stack (`torch`, `huggingface_hub`, `safetensors`) and should be
used to test whether forecast direction agrees with flow/Cipher direction before
entering a setup.

```powershell
# Check whether Kronos repo/dependencies are ready.
py -3 .\cipher-system\core\kronos_research.py status

# Export local stock bars into Kronos-compatible OHLCV shape.
py -3 .\cipher-system\core\kronos_research.py export --ticker AMZN --timeframe 5m --limit 512
```

## Repository Layout

```
cipher/
  cipher-system/
    app/              # Node launcher/proxy and browser UI
    core/             # local Python market-data + research API
    data/             # local universes, weights, scan/backtest artifacts
    mcp-server/       # local research workflow tooling
    previous-work/    # archived/reference work; not active runtime
  tests/              # smoke and safety tests for the active app
  scripts/            # parity capture/comparison helpers
  docs/               # devspace/tooling notes
  api/                # stale scaffold; not the active app
```

The root `relay/`, `shared/`, `storage/`, `exposure_engine/`, `night_vision/`,
`spyglass/`, and `execution/` directories are not the active implementation in
this checkout.

## Quick Start

```bash
cd /home/aarav/Aarav/cipher/cipher-system
cp ../.env.example app/.env
# fill the Alpaca values in app/.env
./Start-Cipher-App.sh
```

Open:

```text
http://127.0.0.1:8283/
```

Default ports:

| Service | Port |
|---|---:|
| Core API | 8282 |
| Browser app | 8283 |

## Environment

Use Alpaca market-data credentials in `cipher-system/app/.env`.

Recommended values:

```env
ALPACA_ALGO_PLUS_KEY=
ALPACA_ALGO_PLUS_SECRET=
ALPACA_DATA_FEED=opra
ALPACA_STOCK_FEED=sip
```

`ALPACA_DATA_FEED=indicative` can be used for UI testing when OPRA is not
available, but it is delayed/modified and should not be used for real intraday
research decisions.

## API Routes

The local core exposes:

| Path | Purpose |
|---|---|
| `/health` | local credential/feed status, no secrets |
| `/api/quote` | underlying quote and prior-close context |
| `/api/matrix` | strike x expiration GEX/VEX surface |
| `/api/heatmap` | heatmap-ready matrix payload |
| `/api/night-vision` | matrix plus ranked exposure levels |
| `/api/bars` | stock OHLCV candles |
| `/api/flow` | Spyglass option flow tape |
| `/api/stream` | SSE quote/matrix/flow refresh |
| `/api/scan` | local setup scanner |
| `/api/backtest` | scan capture and forward scoring |

## Guardrails

- Read-only research app.
- No order submission endpoints.
- No auto-execution.
- No browser-exposed secrets.
- No claims that GEX is verified dealer positioning.
- Keep data gaps visible instead of manufacturing precision.

## Verification

```bash
cd /home/aarav/Aarav/cipher
python3 -m compileall -q cipher-system/core tests
node --check cipher-system/app/server.mjs
node --check cipher-system/app/launcher.mjs
node --check cipher-system/app/public/app.js
pytest -q
```
