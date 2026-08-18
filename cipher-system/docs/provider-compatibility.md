# Provider compatibility

Cipher is a private, local-first research terminal. The active market-data
provider is Alpaca, and all browser-facing capabilities are read-only. This
page describes data compatibility; it does not authorize brokerage access,
account synchronization, or live orders.

## Capability status

The core service exposes `GET /api/provider-capabilities`. The response is
derived from local configuration only. It does not call a provider, verify an
account entitlement, return credentials, or convert a missing field into zero.

| Mode | Configuration | Meaning |
|---|---|---|
| `alpaca_opra_sip` | `ALPACA_DATA_FEED=opra`, `ALPACA_STOCK_FEED=sip` | Full intended research configuration: OPRA options plus SIP stock quotes/bars, subject to account entitlement and normal provider gaps. |
| `alpaca_indicative_iex` | `ALPACA_DATA_FEED=indicative`, `ALPACA_STOCK_FEED=iex` | Degraded research configuration. Options and stock data can be less complete or delayed; GEX, flow, and liquidity evidence must remain visibly caveated. |
| `alpaca_custom` | Other recognized feed combination | Configuration is readable, but it is not one of the documented compatibility modes. |
| `unconfigured` | Missing an Alpaca key or secret | No Alpaca data capability is claimed. The endpoint reports `null` capability states rather than false zero values. |

The endpoint reports feed selection and whether credentials are present. It does
not prove that OPRA, SIP, or any other feed is entitled for the configured
account. A provider response, freshness, coverage, and missing-input metadata
remain the authority for each individual observation.

## Alpaca configuration

Accepted server-side credential names are:

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

Credentials belong in `cipher-system/app/.env` or the process environment. The
browser must never receive them. `ALPACA_DATA_FEED` supports `opra` and
`indicative`; `ALPACA_STOCK_FEED` is normally `sip` or `iex`.

### Standard Alpaca account guidance

If the account cannot use the preferred feeds, configure the explicit degraded
mode rather than presenting the result as full-fidelity:

```env
ALPACA_DATA_FEED=indicative
ALPACA_STOCK_FEED=iex
```

This keeps the terminal usable for research while making the lower-quality
input mode inspectable. Missing gamma, open interest, quotes, trades, or bars
remain unknown. GEX remains a public-open-interest heuristic, not verified
dealer positioning.

## Anonymous yfinance fallback

When no Alpaca credentials/session are available, the core may use the already
installed `yfinance` package as an explicitly degraded fallback:

| Capability | Anonymous fallback behavior |
|---|---|
| Stock quotes | Delayed/unofficial Yahoo Finance quote with no fabricated bid/ask |
| Stock OHLCV bars | Delayed/unofficial Yahoo Finance history |
| Option chain | Limited Yahoo option-chain snapshot with nullable Greeks and OI date |
| Strike Matrix | May render strike rows and modeled exposure where mid/IV/OI inputs are sufficient; every response is labeled `feed=yahoo` |
| OPRA flow / latest option trades | Unavailable; the UI explicitly reports that OPRA event-time data is not available |
| Verified dealer positioning | Never available; GEX remains a public-OI heuristic |

The fallback never converts absent fields to zero and never presents a Yahoo
snapshot as an OPRA tape. If yfinance is unavailable or Yahoo returns no usable
rows, the API returns an explicit unavailable/error state rather than blocking
the rest of the terminal.

## Tradier

Tradier is not an interchangeable backend for the active Alpaca routes. Its
current safe role is optional read-only capture of historical option flow into
the local `tradier_stream.sqlite` path, consumed by `tradier_flow.py`.

Tradier credentials may enable that capture path, but they do **not** make
Tradier the provider for `/api/quote`, `/api/options-chain`, `/api/matrix`, or
`/api/bars`. The capability response labels this as
`capture_supplement_only` and lists the active Alpaca-backed routes that it does
not replace. No Tradier brokerage or order integration is supported.

## Webull

Webull has no adapter, credential configuration, or normalized data path in the
active application. It is reported as `unsupported`. Do not claim Webull support
or add brokerage integration under the read-only project boundary. A future
read-only adapter would require an explicit scope decision, normalized field
mapping, provenance rules, and tests before it could be described as supported.

## Hosted multi-user mode

Hosted deployment is intentionally split across three manually configured
services:

```text
Vercel Next.js export  ->  stable HTTPS Node API origin on the GCP VM
Supabase Auth/Postgres  ->  authentication and RLS-scoped user state
GCP Python core        ->  read-only Alpaca market-data normalization
```

The Vercel browser bundle receives only `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY`, and `NEXT_PUBLIC_CIPHER_API_ORIGIN`. The
Supabase service-role key, database password, internal proxy token, and all
Alpaca credentials stay off Vercel and out of the browser.

Supabase verifies the email/password only for the sign-in exchange. The browser
then receives an opaque `HttpOnly; Secure; SameSite=None` `cipher_session`
cookie. The cookie contains no email, password, Supabase token, or Alpaca key;
the temporary Supabase access token is held only in bounded Node process memory
and is cleared on logout, expiry, restart, or session eviction. Browser API
requests use `credentials: include`, not persisted bearer tokens.

Users manually enter an Alpaca key and secret in Settings for a session-only connection. The Node proxy sends
them over HTTPS to the authenticated core hop; the core stores them only in a
bounded in-memory session with inactivity and absolute expiry. They are cleared
on disconnect, logout, expiry, or process restart and are never written to
Supabase, browser storage, URLs, logs, disk caches, or API responses. Feed
selection is shown as OPRA/SIP full-intent or indicative/IEX degraded research;
it does not prove account entitlements.

The Vercel frontend must use a stable HTTPS API hostname. The existing Tailscale
Funnel is a private pilot/rollback option, not the production API-origin
contract. Hosted deployment is not ready for public traffic until Auth, RLS,
secret-boundary, and no-live-order checks pass.

## Safety boundary

Provider compatibility does not change the execution boundary:

- the research terminal has no live order endpoint or broker order client;
- paper and shadow simulation are not brokerage authorization;
- `LIVE_REVIEW_REQUIRED` is not permission to execute live orders;
- provider failures and missing fields are explicit unavailable/unknown states;
- credentials and local databases stay server-side and out of release media.
