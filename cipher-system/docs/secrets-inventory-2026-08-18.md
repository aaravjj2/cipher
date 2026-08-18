# Cipher secrets inventory (2026-08-18)

Names and consumers only — **no values ever appear in this document**. This is
the map of which secret exists, where it is allowed to live, and which code
reads it. If a secret is not listed here, it is not consumed by active code.

Legend for locations:

- `session-only` — entered by the user in the browser and held in backend
  memory for the session; never persisted to disk, cookies, logs, or Supabase.
- `server-env` — `/etc/cipher/cipher.env` on the VM (root-readable only),
  loaded by systemd via `EnvironmentFile=-`.
- `app-env` — `cipher-system/app/.env` (server-side, used by the Node proxy
  and some scripts).
- `gcp-secret` — Google Secret Manager, materialized to `/etc/cipher/` by
  `cipher-secrets.service`.

## Credentials (secret values)

| Name | Location | Consumers |
|---|---|---|
| `ALPACA_API_KEY` / `ALPACA_ALGO_PLUS_KEY` / `ALPACA_ALGO_KEY` | session-only (browser entry); fallback read from server env | `core/app.py` provider session, `core/provider_session.py` |
| `ALPACA_API_SECRET` / `ALPACA_ALGO_PLUS_SECRET` / `ALPACA_ALGO_SECRET` / `ALPACA_SECRET_KEY` | session-only; fallback read from server env | `core/app.py`, `core/provider_session.py` |
| `ALPACA_DATA_FEED`, `ALPACA_STOCK_FEED` | server-env / app-env | `core/app.py` feed selection (opra/sip defaults) |
| `TRADIER_ACCESS_TOKEN` / `TRADIER_TOKEN` / `TRADIER_PRODUCTION_TOKEN` / `TRADIER_SANDBOX_TOKEN` | server-env / app-env | Tradier read-only stream capture (`core/tradier_stream_capture.py`) — market-data only, no brokerage |
| `CIPHER_TRADIER_TOKEN_FILE` | server-env | path to a token file alternative |
| `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY` | app-env / server-env | `core/ask_cipher.py` read-only chat completions |
| `MASSIVE_API_KEY` | app-env / server-env | market research agent text generation |
| `NEWSAPI_KEY` | app-env / server-env | `core/news.py` news context |
| `POLYGON_API_KEY` | app-env / server-env | optional historical bars supplement |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | server-env / app-env | hosted-mode auth (anon key only; RLS enforced server-side) |
| `CIPHER_INTERNAL_PROXY_TOKEN` | server-env (`/etc/cipher/cipher.env`) | `core/app.py` internal auth for `/api/*` from local services (`X-Cipher-Internal-Token`) |
| `DISCORD_WEBHOOK_URL` / `DISCORD_PROGRESS_WEBHOOK` | app-env / server-env | `scripts/send_portfolio_daily_discord.py`, `earnings_model/discord_bot.py`, `scripts/alert_health_monitor.py` |
| `HERMES_BIN` | server-env | Telegram alert delivery binary path |
| `CIPHER_HERMES_TARGET` | server-env | Telegram target config for `scripts/hermes_scan_alerts.py` |

## Configuration (non-secret, listed for completeness)

| Name | Purpose |
|---|---|
| `CIPHER_HOSTED` | hosted-mode flag (auth + Supabase) |
| `CIPHER_CORE_PORT`, `CIPHER_CORE_PYTHON`, `CIPHER_CORE_URL`, `CIPHER_RESEARCH_PYTHON`, `NODE` | process wiring |
| `CIPHER_ROOT`, `CIPHER_PAPER_RUNTIME`, `CIPHER_RAW_LAKE_BUCKET`, `CIPHER_NATIVE_LEAN_ROOT` | path/bucket config |
| `CIPHER_EARNINGS_RADAR_PATH` | path where the earnings digest writes the radar artifact (default `runtime/data/earnings_radar.json`) |
| `CIPHER_ENABLE_CLOUD_WRITES`, `CIPHER_GOVERNANCE_HOOKS` | feature flags for research-platform cloud writes / governance hooks |
| `CIPHER_SCAN_ALERT_*` | cluster-alert pass tuning (limit, tickers, timeout) |
| `TRADIER_ENV`, `TRADIER_STREAM_*`, `TRADIER_OPTION_*`, `TRADIER_MAX_*`, `TRADIER_HEALTH_MAX_AGE_MIN` | Tradier capture configuration |
| `GEX_HEALTH_MAX_AGE_MIN`, `OPTION_CHAINS_HEALTH_MAX_AGE_MIN` | freshness thresholds |
| `GCP_PROJECT`, `GOOGLE_CLOUD_PROJECT`, `SEC_USER_AGENT` | GCS/backup + SEC client identity |
| `HF_HOME` | HuggingFace model cache root |
| `USER` | systemd/script user context |

## Hard rules

1. No raw secret is ever written to the browser, cookies, localStorage,
   logs, or committed files. Supabase receives only the anon key + cookie
   session, never Alpaca/Tradier credentials.
2. Alpaca keys are **session-only** when entered via the UI; the env-file
   fallback exists for local/single-user operation.
3. `cipher-secrets.service` materializes GCP secrets to root-readable files
   under `/etc/cipher/`; app processes load them via systemd
   `EnvironmentFile`/`LoadCredential`, never by echoing them.
4. If a new secret name is added, this table must be updated in the same
   commit — a secret that is not mapped is a secret that can leak silently.

## Verification

```bash
# No secret values in tracked files (names may appear):
grep -rIlE '(sk-[A-Za-z0-9]|AKIA[0-9A-Z]|xox[baprs]-|discord[_-]?app[_-]?key|webhooks?/)[A-Za-z0-9/_-]{10,}' cipher-github --exclude-dir=node_modules --exclude-dir=.git 2>/dev/null || echo clean
```
