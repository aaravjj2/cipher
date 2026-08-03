# Cipher System

Local, read-only options research terminal (Strike Matrix, Night Vision, Spyglass).

## Run

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

Copy `.env.example` to `.env` and fill the local credentials. Cipher loads only
the repository `.env`; the file is ignored by Git and is never sent to the
browser. The supported credential groups are Alpaca market data, Tradier
read-only market data, London Strategic Edge, and the optional OpenCode Zen
research endpoint. Do not place credentials in source files, screenshots,
reports, logs, or generated data.

## Research verification

```bash
cd cipher-system
../.venv/bin/python -m pytest -q
```

The current price-only study is recorded in
`docs/current_era_price_only_research_closeout.md`. Volume-sensitive research is
explicitly deferred while adequate independent data is unavailable; the
unchanged reconciliation gate remains preserved rather than weakened.

The full source/runtime topology, module roles, data flows, architecture layers,
and current completion state are documented in
`docs/unified_workspace_architecture.md`.

The system is research-only: no order endpoint, live execution authority, or
automatic paper-trading promotion is enabled.

## What works

- **Strike Matrix** — OPRA chain + contract OI → GEX/VEX heatmap, ±% windows, live SSE refresh
- **Night Vision** — SIP/IEX candles + exposure level overlays (canvas)
- **Spyglass** — latest option prints with inferred bid/ask aggressor and premium tiers
- **Trident / Scanner / Watchlists / Journal / Saves** — research utilities (local-only)

Credentials stay in the local `.env` (never committed). The browser never sees them.

GEX is a public-OI heuristic, not verified dealer positioning.
