# Cipher Research App

A local, read-only options research application. The browser is served from a Node app server; all market-data requests are proxied to the local core service, so API credentials remain server-side.

## Start

Double-click `Start-Cipher-App.cmd`, then open [http://127.0.0.1:8183/](http://127.0.0.1:8183/).

Or, from `app/`, run `npm run app`.

## Current modules

- Strike Matrix — OPRA snapshots joined to option-contract open interest, GEX heuristic, 1/5/6 expiration surfaces and density controls.
- Night Vision — underlying candles, price-context overlays, 1D/5m/15m modes, and shared refresh.
- Same-origin API routes — `/api/health`, `/api/quote`, `/api/matrix`, `/api/night-vision`, `/api/bars`.
- Browser scanner ingest — `POST /api/scanner-ingest` accepts Flash, Flash Agentic, Cluster, Liq, or Cipher Model JSON payloads and stores versioned daily JSONL/CSV plus a persistent signal-episode ledger under `cipher-system/data/browser_ingest/`.

## Browser scanner ingest

The endpoint accepts either an array of setup cards or an envelope containing `setups`, `cards`, `results`, or `data`. Schema v2 preserves `source`, `scan_type`, request/card identity, source and receive timestamps, lifecycle state, normalized setup family, separate `score` and `strength`, card-scoped raw JSON, and validation results. A deterministic signal signature plus a persistent ten-minute episode ledger marks repeated polls as updates instead of new alerts. Empty scans are recorded in JSONL rather than discarded.

Each mode is routed into separate files:

- `<mode>-scans-v2-YYYY-MM-DD.jsonl` — every raw scan and zero-result heartbeat.
- `<mode>-observations-v2-YYYY-MM-DD.csv` — every normalized visible card observation.
- `<mode>-signals-v2-YYYY-MM-DD.csv` — only newly opened signal episodes.
- `scanner-signal-ledger-v2.json` — stable signal IDs and lifecycle counts.

This keeps Flash, Flash Agentic, and Cluster isolated while preserving complete scanner uptime. Use `cipher-system/scripts/accessobsidian_browser_logger.js` instead of the older independent console snippets; the maintained logger sends every heartbeat and leaves episode deduplication to the server.

Directional cards are checked before they become actionable. Bullish targets must be above spot with invalidation below spot; bearish cards require the inverse. Levels more than 12% from spot are flagged as likely cross-card leakage. Invalid cards remain in the authoritative raw log for debugging but have `actionable=false`. Cluster values above 100 are retained as `strength`, not misrepresented as a 0–100 confidence score.

By default, cross-origin browser requests are accepted only from `https://www.accessobsidian.com` and `https://accessobsidian.com`. Override that allowlist with `CIPHER_INGEST_ALLOWED_ORIGINS`. When an ingest token is configured, allowlisted AccessObsidian browser requests are authorized by the tailnet plus Origin allowlist; non-browser callers must still provide a bearer or `X-Cipher-Ingest-Token`. The GCP deployment exposes this route through the tailnet-only Cipher listener on port 8443.

Example payload:

```json
{
  "source": "accessobsidian",
  "scan_type": "flash",
  "captured_at": "2026-07-27T13:00:00-04:00",
  "setups": [
    {
      "ticker": "AAPL",
      "direction": "BULLISH",
      "state": "ARMING",
      "setup_type": "BREAKOUT CONTINUATION",
      "score": 97,
      "strength": 412.5,
      "spot": 220.5,
      "pivot": 221,
      "target": 224,
      "invalidation": 218
    }
  ]
}
```

## Boundaries

The app is research-only: it does not submit orders, manage brokerage accounts, or expose credentials to the browser. Its exposure calculations are transparent OI-based estimates, not a claim to reproduce Cipher's private dealer-positioning model.