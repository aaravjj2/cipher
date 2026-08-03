# Cipher System

Local, read-only options research terminal (Strike Matrix, Night Vision, Spyglass).

## Run

```bash
cd cipher-system
./Start-Cipher-App.sh
```

Or:

```bash
cd app && npm run app
```

Open **http://127.0.0.1:8283/**

Default ports: core `8282`, web `8283` (override with `CIPHER_CORE_PORT` / `PORT`).

## What works

- **Strike Matrix** — OPRA chain + contract OI → GEX/VEX heatmap, ±% windows, live SSE refresh
- **Night Vision** — SIP/IEX candles + exposure level overlays (canvas)
- **Spyglass** — latest option prints with inferred bid/ask aggressor and premium tiers
- **Trident / Scanner / Watchlists / Journal / Saves** — research utilities (local-only)

Credentials stay in the local `.env` (never committed). The browser never sees them.

GEX is a public-OI heuristic, not verified dealer positioning.
