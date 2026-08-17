# Cipher — Auditable AI Options Copilot

Cipher is a private, local-first stocks-and-options research workstation. It
connects discovery, chart and market-structure analysis, contract liquidity,
research agents, journaling, prospective validation, and autonomous **paper**
simulation through one evidence trail.

Cipher is not a live-trading bot. The active application has no broker-order
endpoint or live-order client.

## Daily workflow

```text
Morning Brief → Setup Scanner → Night Vision → Options Terminal
              → Research / Journal → Paper Portfolios
```

- **Evidence first** — provider, feed, event time, freshness, coverage, missing
  reasons, and stable replay identity accompany material outputs.
- **Options-aware** — OPRA quotes/trades, Greeks, IV, volume, contract OI/date,
  observed spreads, GEX/VEX, and defined-risk structure research.
- **Auditable autonomy** — the paper autopilot plans in premarket, requires a
  fresh closed-bar RTH confirmation, applies hard portfolio limits, and records
  every acceptance and rejection.
- **Honest evaluation** — realized, midpoint-marked, and liquidation equity are
  separate; skipped signals remain in a counterfactual ledger; tiny cohorts are
  not rankable.
- **Bounded AI** — scheduled research and FinBERT context are advisory and
  cannot authorize an order.

## Run

For a standalone checkout:

```bash
cp .env.example app/.env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm ci && npm run build && cd ..
bash scripts/sync_web_build.sh
./Start-Cipher-App.sh
```

On the VM, the unified product is managed by the installed `cipher-core`,
`cipher-web`, `cipher-gex`, and `cipher-tradier` systemd services. For a manual
local run from the canonical checkout:

```bash
cd cipher-system
./Start-Cipher-App.sh
```

Or use the unified development manager:

```bash
../.venv-research-py312/bin/python scripts/manage_unified_cipher.py start --without-ops
```

Open **http://127.0.0.1:8283/**

Default ports: core `8282`, web `8283` (override with `CIPHER_CORE_PORT` / `PORT`).

## Local configuration

Copy `.env.example` to `app/.env` and fill the local credentials. Cipher loads only
the repository `.env`; the file is ignored by Git and is never sent to the
browser. The active market-data provider is Alpaca: OPRA for options and SIP
with IEX fallback for stocks. Do not place credentials in source files,
screenshots, reports, logs, or generated data.

## Verification and release checks

```bash
cd cipher-system
python3 -m pytest -q
python3 -m compileall -q core tests
node --check app/server.mjs
node --check app/launcher.mjs
cd web && npm run lint && npm run typecheck && npm run build
```

The active product is intentionally conservative: missing gamma/OI/quotes remain
unknown, flow freshness is visible, and paper results separate realized,
midpoint-marked, liquidation, and counterfactual opportunity outcomes.

The implementation sequence, acceptance gates, risk register, hackathon package,
and current completion state are documented in the
[`canonical product and hackathon roadmap`](docs/cipher_product_and_hackathon_roadmap_2026-08-17.md).

Release audits live under [`docs/audits/`](docs/audits/). They record each
implemented phase, runtime smoke result, and any remaining data-quality caveat.

The system is research-only: no order endpoint, live execution authority, or
automatic paper-trading promotion is enabled.

## What works

- **Strike Matrix** — OPRA chain + contract OI → GEX/VEX heatmap, ±% windows, live SSE refresh
- **Night Vision** — SIP/IEX candles + exposure level overlays (canvas)
- **Evidence timeline** — observed/captured times, freshness, coverage, missing
  inputs, caveats, replay identity, and live-versus-cached state beside the chart
- **Spyglass** — latest option prints with inferred bid/ask aggressor and premium tiers
- **Flow freshness** — event age and current/stale/unknown status are shown instead
  of presenting a cold chain snapshot as a live tape
- **Bounded operator surfaces** — cold quote/flow refreshes return within a hard
  budget, deduplicate in the background, and report refreshing/unavailable rather
  than blocking or displaying zero
- **Frozen replay integrity** — Scanner → Night Vision artifacts verify the
  snapshot identity and, for new captures, the complete normalized-matrix SHA-256
  checksum; legacy artifacts are labelled when the full checksum is absent
- **Trident / Scanner / Watchlists / Journal / Saves** — research utilities (local-only)
- **Paper Autopilot** — premarket plan, RTH confirmation, simulated fills/exits,
  hard risk locks, decision traces, and leakage-safe learning manifests
- **Paper Portfolios** — six isolated studies, prospective cohorts, marked and
  liquidation equity, and opportunity-versus-execution attribution
- **Research Desk** — scheduled, evidence-bounded analysis for liquid and
  volatile names

Credentials stay in the local `.env` (never committed). The browser never sees them.

GEX is a public-OI heuristic, not verified dealer positioning. Any cached market
surface is labelled stale and remains non-actionable until refreshed.

The canonical implementation sequence, acceptance gates, risk register, and
post-hackathon roadmap are in [`docs/cipher_product_and_hackathon_roadmap_2026-08-17.md`](docs/cipher_product_and_hackathon_roadmap_2026-08-17.md).

## Release walkthrough media

The current local build has a captured trader workflow showing the dashboard,
Night Vision, the evidence timeline, paper portfolios, and Setup Scanner:

- [Dashboard](release-artifacts/01-home.png)
- [Night Vision](release-artifacts/02-night-vision.png)
- [Evidence timeline](release-artifacts/03-evidence-timeline.png)
- [Paper portfolios](release-artifacts/04-paper-portfolios.png)
- [Setup Scanner](release-artifacts/05-setup-scanner.png)
- [Walkthrough video](release-artifacts/cipher-release-walkthrough.webm)

These artifacts are generated from the local authenticated browser session and
must be regenerated after a material UI release. They contain no provider keys.

## QuantumHacks

The judge path, submission copy, architecture, demo script, and sanitized-release
checklist are in [`docs/quantumhacks/`](docs/quantumhacks/README.md).
