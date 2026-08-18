# Cipher hosted multi-user deployment design

Date: 2026-08-18
Status: Draft for user review

## Decision summary

Cipher will be hosted as a multi-user application with:

- **Vercel** serving the existing Next.js frontend.
- **Supabase Auth** providing user authentication.
- **Supabase Postgres** storing user-owned application state with Row Level
  Security (RLS).
- The existing **GCP VM** continuing to run the Python market-data API and Node
  browser-facing proxy.
- **Manual deployment setup** by the operator. The agent will prepare code,
  migrations, configuration templates, and verification steps, but will not
  access or receive Vercel/Supabase account secrets.
- **Manual, session-only Alpaca credential entry** by each user. Raw Alpaca
  credentials are never persisted to Supabase, browser storage, URLs, logs, or
  generated artifacts.

This is a multi-user tenancy and persistence migration, not only a frontend
hosting change.

## Goals

1. Make the current research terminal reachable through a stable HTTPS frontend.
2. Preserve the existing read-only market-data and paper-simulation boundary.
3. Authenticate users through Supabase Auth.
4. Prevent user-owned records from crossing tenant boundaries.
5. Allow each user to connect their own Alpaca market-data credentials for a
   session without exposing them to browser code or persisting them.
6. Keep existing provider caveats, missing-data semantics, GEX caveat, and
   `LIVE_REVIEW_REQUIRED` execution boundary unchanged.
7. Allow the operator to configure provider and hosting accounts manually.

## Non-goals

- No broker-order endpoint, order client, live execution, or trading authority.
- No browser-side Alpaca calls with secret credentials.
- No storage of raw Alpaca credentials in Supabase or Vercel environment
  variables on behalf of end users.
- No claim that session-only credentials are inaccessible to the operator while
  present in backend process memory; they are not persisted, but the backend
  must necessarily use them to call Alpaca.
- No immediate migration of every historical research archive or GEX capture
  database into Postgres.
- No replacement of the Python core with Supabase Edge Functions.
- No public exposure of the existing local SQLite files or VM filesystem.

## Target architecture

```text
Browser
  |
  | HTTPS, Supabase Auth session, no Alpaca secret after submission
  v
Vercel
  | Next.js frontend and same-origin /api proxy/rewrites
  v
Public HTTPS API origin on the existing GCP VM
  |
  +-- Node app/server.mjs: auth boundary, request routing, rate limits
  |
  +-- Python core/app.py: read-only market data and paper/research APIs
  |
  +-- in-memory session credential store: process lifetime only
  |
  +-- local read-only historical/reference data where appropriate
  |
  v
Alpaca market-data APIs

Supabase
  +-- Auth users and sessions
  +-- RLS-protected user-owned Postgres tables
  +-- migrations and operator-managed project configuration
```

The public API origin must use HTTPS and must not expose core port 8282
 directly. The existing Tailscale Funnel may remain as a private/operator
 surface, but the Vercel production deployment needs a stable, externally
 reachable HTTPS origin configured manually by the operator.

## Authentication and request flow

1. A user signs up or signs in through Supabase Auth in the Vercel frontend.
2. The frontend obtains a Supabase access token. The public Supabase URL and
   anon key are safe to ship to the browser; service-role credentials are not.
3. Frontend API calls go through the Vercel `/api/*` rewrite/proxy and include
   `Authorization: Bearer <access-token>`.
4. The Node proxy validates the token issuer, signature, audience, expiry, and
   subject against the configured Supabase project. Invalid or expired tokens
   fail closed with HTTP 401.
5. The authenticated subject is passed to Python as trusted request context only
   after proxy validation, using an internal authenticated hop that cannot be
   forged by the public client.
6. User-state reads and writes always use the authenticated subject. No request
   may accept an arbitrary `user_id` from a browser payload as authority.
7. Logout invalidates the frontend session and removes the backend's session-only
   provider credential state for that user/session.

Public exceptions are limited to liveness responses that do not disclose
provider configuration, account information, or user data. Research and state
routes remain authenticated.

## Session-only Alpaca credentials

The frontend provides an explicit `Connect Alpaca` form. It must:

- submit credentials only over HTTPS to the authenticated backend;
- never place credentials in localStorage, sessionStorage, cookies, query
  strings, route parameters, analytics, error messages, or React diagnostics;
- clear the form fields immediately after a successful submission or failure;
- display the connected state without displaying or echoing key material;
- offer an explicit disconnect action.

The backend must:

- validate the credential payload size and shape;
- store credentials only in a bounded in-memory structure keyed by an opaque
  session identifier and authenticated user ID;
- apply an inactivity/absolute expiry and bounded maximum session count;
- never write the raw values to logs, traces, exceptions, SQLite, Supabase,
  disk caches, crash dumps, or response bodies;
- resolve provider calls from the request's session context rather than global
  `.env` values;
- clear the values on disconnect, expiry, logout, and process restart;
- avoid background jobs that outlive the user credential session.

The server may retain only non-secret connection metadata such as provider name,
feed selection, connection state, and last successful request time. It must not
retain a reversible credential fingerprint that could be mistaken for a secret
or used as an account identifier in a public response.

Because the existing core reads Alpaca credentials from process-global settings,
provider access must be refactored to a request/session-scoped credential
provider. Cache keys must include a non-secret session/account scope whenever a
response depends on user-specific Alpaca entitlements. A response fetched with
one user's credentials must never be served from a cache to another user.

## Supabase data model and tenancy

Every user-owned table receives a non-null `user_id uuid references auth.users`
column and RLS policies enforcing `user_id = auth.uid()`:

- user profiles and preferences;
- watchlists and saved screens;
- journal entries and chart templates;
- workspace layouts;
- manual holdings;
- portfolio-risk positions and cash settings;
- alert rules;
- user-created paper/research records and annotations;
- provider-session metadata that contains no secret material.

The migration must identify the exact existing modules and routes for each store
before changing behavior. Shared market/reference data may remain on the VM as
read-only data, but must not contain user-owned records or secrets.

The browser uses only the Supabase anon key. Server-side service-role access,
if required for migrations or operator administration, is never used as a
substitute for request authorization and is never bundled into Vercel output.
User requests should use a user-scoped Supabase client or an equivalent explicit
RLS-enforcing path.

## API boundary

The Node proxy remains the explicit route allowlist and gains:

- Supabase JWT validation;
- authenticated user context propagation;
- CORS/CSRF behavior appropriate to the Vercel origin;
- session credential connect/status/disconnect routes;
- bounded request body and rate limits for credential operations;
- no route for orders, broker clients, or execution authority.

Python core routes that read shared market data can continue to use the existing
normalization and caveat envelopes. User-state routes must migrate from global
SQLite/JSON access to repositories that accept the authenticated user context.
A route is not considered hosted-ready until it has an RLS-backed repository or
is explicitly classified as shared read-only data.

## Deployment and manual operator workflow

The operator manually creates/configures:

- a Vercel project connected to the public repository;
- a Supabase project and Auth settings;
- the Supabase schema migrations and RLS policies;
- a stable HTTPS API hostname pointing to the GCP VM;
- backend environment values on the VM only;
- Vercel public environment values such as the Supabase URL, anon key, and API
  origin.

Raw user Alpaca keys are not entered into Vercel or Supabase project settings.
The operator must verify that production logs, preview deployments, crash
reports, and database logs do not include request bodies or authorization
headers.

A deployment must be reversible: publish the Vercel frontend only after the API
origin is healthy, keep the current VM service configuration available for
rollback, and run migrations in an additive/backward-compatible order.

## Migration phases

### Phase 0 — inventory and boundary tests

- Inventory every user-owned SQLite/JSON store and API route.
- Add tests that reject unauthenticated state access and arbitrary user IDs.
- Add secret-log/body/header regression tests.
- Add no-live-order and read-only scans to the hosted path.

### Phase 1 — Supabase foundation

- Add Supabase browser client using public variables only.
- Add Auth screens and session handling.
- Create initial schema migrations, indexes, and RLS policies.
- Add local test project/test fixtures without requiring production credentials.

### Phase 2 — backend auth bridge

- Add JWT validation to the Node proxy.
- Propagate trusted user context to Python.
- Keep the current local password gate available for private rollback operation
  until the hosted auth path passes end-to-end tests.
- Add Vercel API rewrite configuration and HTTPS origin health checks.

### Phase 3 — session provider connections

- Add connect/status/disconnect APIs.
- Refactor Alpaca access to request-scoped session credentials.
- Add expiry, memory bounds, no-persistence tests, and cache isolation.
- Keep missing provider data explicit and never convert it to zero.

### Phase 4 — user-state repositories

- Migrate watchlists, journal, layouts, holdings, alerts, and portfolio-risk
  state one module at a time.
- Backfill only intentionally selected sanitized/user-authorized data.
- Keep global research/reference archives read-only and separate.

### Phase 5 — hosted feature release

- Enable the migrated panels behind Auth/RLS.
- Keep unmigrated panels local-only or visibly unavailable rather than exposing
  shared mutable state.
- Verify user A cannot read, update, delete, or infer user B's records.

### Phase 6 — production verification

- Deploy manually to a Vercel preview and a non-production Supabase project.
- Run browser, API, RLS, secret, rate-limit, and no-live-order tests.
- Verify Alpaca keys are absent from browser bundles, logs, database rows,
  screenshots, and response payloads.
- Promote to production only after rollback and outage behavior are tested.

## Acceptance criteria

The migration is complete only when:

- a new user can sign up, sign in, and sign out from the Vercel site;
- unauthenticated API requests cannot read or mutate user state;
- two users can create same-named records without collision or visibility;
- user A cannot access user B's records by changing IDs, query parameters, or
  request bodies;
- session Alpaca credentials are never persisted and disappear on expiry,
  logout, and backend restart;
- browser bundles, local storage, cookies, URLs, logs, traces, database rows,
  and error responses contain no raw Alpaca credentials;
- user-scoped provider caches cannot cross-contaminate accounts;
- stale, missing, or unavailable market data remains explicitly represented;
- GEX retains its public-OI heuristic caveat;
- no live-order endpoint, broker order client, or order authority is introduced;
- Vercel production can reach the VM API over HTTPS;
- the VM can be rolled back to the current private deployment without data loss;
- Python, Node, frontend, RLS, browser, and research-only safety checks pass.

## Resolved implementation choices

These choices constrain the implementation plan:

1. **API hostname:** use the current Tailscale Funnel only for an authenticated
   pilot and rollback path. Production Vercel traffic must use a stable HTTPS
   hostname on the VM, preferably a custom operator-owned domain/reverse proxy;
   no public launch depends on a private tailnet address.
2. **Supabase access:** user-state repositories use the authenticated user's JWT
   against Supabase's RLS-enforcing API. A service-role key, if required for
   migrations, is operator-only and never used as a substitute for request
   authorization.
3. **Provider sessions:** use a 30-minute inactivity timeout, a 12-hour absolute
   lifetime, and a bounded maximum of 32 in-memory sessions per backend process.
   These are initial defaults and must be configurable without logging secrets.
4. **Migration order:** migrate workspace layouts, watchlists/screens, journal
   and templates, holdings/portfolio risk, alerts, then paper/research records.
   Each module gets its own schema migration, repository adapter, RLS tests, and
   browser journey before the next module is enabled.
5. **Feature ownership:** market quotes, chains, charts, GEX, flow, and shared
   historical/research evidence are shared read-only computations scoped by the
   provider session. Watchlists, journals, layouts, holdings, alerts, paper
   portfolios, and annotations are user-owned and require RLS before hosted
   exposure.
6. **Background work:** scheduled VM jobs continue only for operator-owned/shared
   datasets. User-specific Alpaca sessions are request-driven and never copied
   into a long-lived worker or scheduled job.

If the operator cannot provide a stable public HTTPS API hostname, the release
stays in pilot mode on Vercel preview plus the authenticated Funnel and is not
represented as a general public multi-user deployment.

## Self-review result

- No raw credentials, service-role values, or provider keys are included.
- The current read-only and no-live-order boundary is preserved in every layer.
- User-owned storage, shared research data, session lifetime, and deployment
  rollback behavior are explicit.
- The design does not claim that Supabase hosts the Python process.
- The design distinguishes a private pilot from a generally hostable production
  URL.
