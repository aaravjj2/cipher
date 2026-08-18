# Cipher Hosted Multi-User Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current single-user/local Cipher terminal into a Vercel-hosted multi-user application with Supabase Auth/RLS and session-only per-user Alpaca credentials while preserving the existing Python core and read-only boundary.

**Architecture:** Vercel serves the Next.js frontend in hosted mode. Supabase provides Auth and RLS-protected Postgres state. The existing GCP VM exposes the authenticated Node proxy over stable HTTPS and continues to run `core/app.py`; user Alpaca credentials are held only in bounded backend memory for the authenticated session.

**Tech Stack:** Next.js 16, React 19, TypeScript, Node.js 20+ standard library HTTP server, Python 3.11+, Supabase Auth/Postgres/RLS, Alpaca market-data APIs, existing SQLite/reference data, Playwright, pytest, Node test runner.

**Spec:** `cipher-system/docs/superpowers/specs/2026-08-18-hosted-multitenant-deployment-design.md`

## Global Constraints

- The browser must never receive raw Alpaca credentials or service-role credentials.
- Raw end-user Alpaca credentials are session-only: 30-minute inactivity timeout, 12-hour absolute lifetime, maximum 32 in-memory sessions per backend process.
- No raw Alpaca credentials may be written to logs, traces, SQLite, Supabase, disk caches, crash dumps, response bodies, URLs, browser storage, or generated artifacts.
- User-owned records require `user_id` tenancy and Supabase RLS before hosted exposure.
- Shared market/reference data may remain read-only on the VM; local SQLite files must not be publicly exposed.
- Vercel production traffic requires a stable HTTPS API origin; the current Tailscale Funnel is pilot/rollback-only.
- Existing missing-data semantics, public-OI GEX caveat, paper-only semantics, and `LIVE_REVIEW_REQUIRED` governance boundary remain unchanged.
- No `/v2/orders`, `submit_order`, `place_order`, `create_order`, `TradingClient`, `OrderClient`, or equivalent live-order capability may be added.
- The existing local password-gated deployment remains available until hosted Auth passes end-to-end verification.
- Do not request, print, commit, or store Vercel/Supabase account secrets in the repository.

---

## File map

### New files

- `cipher-system/supabase/migrations/0001_user_state.sql` — user-owned tables, indexes, RLS, and policies.
- `cipher-system/supabase/migrations/0002_provider_session_metadata.sql` — non-secret connection metadata only.
- `cipher-system/web/src/lib/supabase.ts` — browser Supabase client using public variables.
- `cipher-system/web/src/lib/auth.ts` — typed auth/session helpers.
- `cipher-system/web/src/components/auth/AuthPanel.tsx` — sign-in/sign-up/sign-out UI.
- `cipher-system/web/src/components/auth/ProviderConnectionPanel.tsx` — session-only Alpaca connection UI.
- `cipher-system/app/supabase_auth.mjs` — server-side access-token validation through Supabase Auth.
- `cipher-system/app/provider_session_client.mjs` — Node-to-core internal session bridge.
- `cipher-system/core/request_context.py` — request-scoped authenticated user/provider context.
- `cipher-system/core/provider_session.py` — bounded in-memory session credential store.
- `cipher-system/core/supabase_rest.py` — RLS-preserving Supabase REST adapter using the user JWT.
- `cipher-system/core/user_state.py` — shared repository contracts and user-ID validation.
- `cipher-system/app/test/supabase-auth.test.mjs` — Auth validation and fail-closed tests.
- `cipher-system/app/test/provider-session-route.test.mjs` — provider-session route boundary tests.
- `cipher-system/tests/test_provider_session.py` — session lifetime, clearing, and isolation tests.
- `cipher-system/tests/test_request_context.py` — context propagation and cleanup tests.
- `cipher-system/tests/test_secret_boundary.py` — no-logging/no-persistence credential regression tests.
- `cipher-system/tests/test_supabase_rest.py` — RLS adapter and no-secret-logging tests.
- `cipher-system/tests/test_user_state_tenancy.py` — user-ID and repository isolation tests.
- `cipher-system/web/test/auth-contract.test.mjs` — browser auth/provider-source contract tests.
- `cipher-system/web/e2e/hosted-auth.spec.ts` — two-user auth and isolation journey.
- `cipher-system/web/vercel.json` — hosted deployment headers and API routing configuration if required by the selected Vercel project mode.
- `cipher-system/web/.env.hosted.example` — names and descriptions of public hosted variables only.
- `cipher-system/supabase/README.md` — manual project setup, migration, RLS verification, and rollback instructions.

### Existing files to modify

- `cipher-system/app/server.mjs` — Supabase auth boundary, user context, provider-session routes, CORS/CSRF, and internal proxy headers.
- `cipher-system/app/package.json` — only if a server dependency is proven necessary; prefer Node built-ins and Supabase Auth HTTP validation.
- `cipher-system/core/app.py` — request-context setup/cleanup, internal session routes, and scoped provider/cache access.
- `cipher-system/core/disk_cache.py` — prevent user-session responses from entering persistent shared disk cache.
- `cipher-system/core/watchlists.py` — repository boundary with authenticated user scope.
- `cipher-system/core/trader_journal.py` — repository boundary with authenticated user scope.
- `cipher-system/core/workspace_layouts.py` — repository boundary with authenticated user scope.
- `cipher-system/core/holdings.py` — repository boundary with authenticated user scope.
- `cipher-system/core/alerts.py` — repository boundary with authenticated user scope.
- `cipher-system/core/portfolio_risk.py` — repository boundary with authenticated user scope.
- `cipher-system/core/paper_portfolio_api.py` — user-owned hosted state boundary.
- `cipher-system/web/next.config.ts` — preserve static local export while enabling hosted Vercel mode.
- `cipher-system/web/src/app/page.tsx` — auth gate/session lifecycle and sign-out handling.
- `cipher-system/web/src/lib/api.ts` — auth token attachment, hosted API base, 401 handling, and provider-session methods.
- `cipher-system/web/src/components/panels/Settings.tsx` — Auth and provider connection controls.
- `cipher-system/web/package.json` and `package-lock.json` — add the verified `@supabase/supabase-js` client dependency.
- `cipher-system/.gitignore` — ensure hosted/local env and migration artifacts do not admit secrets.
- `cipher-system/README.md` and `cipher-system/docs/provider-compatibility.md` — manual hosted setup and session-only credential disclosure.

---

## Task 1: Establish the Supabase schema and tenancy contract

**Files:**
- Create: `cipher-system/supabase/migrations/0001_user_state.sql`
- Create: `cipher-system/supabase/migrations/0002_provider_session_metadata.sql`
- Create: `cipher-system/supabase/README.md`
- Create: `cipher-system/tests/test_user_state_tenancy.py`
- Modify: `cipher-system/.gitignore`

**Interfaces:**
- Produces SQL tables with `user_id uuid not null references auth.users(id)`, indexes beginning with `user_id`, and RLS policies using `auth.uid()`.
- Produces an operator-run verification query that proves cross-user `select`, `insert`, `update`, and `delete` isolation.
- Produces no table containing raw Alpaca key or secret columns.

- [ ] **Step 1: Write the failing schema contract tests.**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "supabase/migrations/0001_user_state.sql").read_text(encoding="utf-8")


def test_user_tables_have_user_id_and_rls():
    for table in ("user_profiles", "watchlists", "saved_screens", "journal_entries", "chart_templates", "workspace_layouts", "holdings", "alerts", "portfolio_risk_positions"):
        assert f"create table if not exists public.{table}" in SQL
        assert f"alter table public.{table} enable row level security" in SQL
        assert f"user_id uuid not null references auth.users(id)" in SQL


def test_schema_has_no_raw_provider_secret_columns():
    lowered = SQL.lower()
    assert "alpaca_api_key" not in lowered
    assert "alpaca_secret" not in lowered
    assert "private_key" not in lowered


def test_policies_bind_to_auth_uid():
    assert SQL.count("auth.uid()") >= 12
    assert "using (user_id = auth.uid())" in SQL
    assert "with check (user_id = auth.uid())" in SQL
```

- [ ] **Step 2: Run the contract test and verify it fails because the migrations do not exist.**

Run: `pytest -q tests/test_user_state_tenancy.py`

Expected: FAIL with a missing migration-file error.

- [ ] **Step 3: Add the migrations.** Create tables for profiles/preferences, watchlists, saved screens, journal entries, chart templates, workspace layouts, holdings, alerts, portfolio-risk positions/cash, paper-user annotations, and non-secret provider-session metadata. Add `select`, `insert`, `update`, and `delete` policies for each user-owned table. Add unique constraints scoped by `user_id`, not global names.

- [ ] **Step 4: Add the manual Supabase setup document.** Document project creation, Auth redirect URLs, applying migrations, creating a test user pair, the SQL isolation probe, and deleting the test project. State that `SUPABASE_SERVICE_ROLE_KEY` is never placed in browser or Vercel public variables.

- [ ] **Step 5: Run the schema contract and repository guard tests.**

Run: `pytest -q tests/test_user_state_tenancy.py`

Expected: PASS; no production Supabase credentials are needed.

- [ ] **Step 6: Commit the schema boundary.**

```bash
git add cipher-system/supabase cipher-system/tests/test_user_state_tenancy.py cipher-system/.gitignore
git commit -m "feat: define user-scoped hosted state schema"
```

---

## Task 2: Add the browser Supabase Auth foundation

**Files:**
- Modify: `cipher-system/web/package.json`
- Modify: `cipher-system/web/package-lock.json`
- Create: `cipher-system/web/src/lib/supabase.ts`
- Create: `cipher-system/web/src/lib/auth.ts`
- Create: `cipher-system/web/src/components/auth/AuthPanel.tsx`
- Create: `cipher-system/web/test/auth-contract.test.mjs`
- Modify: `cipher-system/web/src/app/page.tsx`
- Modify: `cipher-system/web/src/components/panels/Settings.tsx`
- Create: `cipher-system/web/.env.hosted.example`

**Interfaces:**
- `createBrowserSupabaseClient(): SupabaseClient` reads only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
- `getAccessToken(): Promise<string | null>` returns the current Supabase access token.
- `subscribeToAuth(callback: (session: Session | null) => void): () => void` returns an unsubscribe function.
- `AuthPanel` renders sign-in, sign-up, sign-out, loading, and error states without rendering provider credentials.

- [ ] **Step 1: Add a source contract test for public/private environment boundaries.** Assert that browser source references `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`, never `SUPABASE_SERVICE_ROLE_KEY`, `ALPACA_API_SECRET`, or `ALPACA_SECRET`.

- [ ] **Step 2: Run the contract test and verify it fails because the Auth modules do not exist.**

Run: `node --test test/auth-contract.test.mjs`

Expected: FAIL with missing-file or missing-symbol assertions.

- [ ] **Step 3: Add `@supabase/supabase-js` using the project’s existing npm workflow and implement the browser client.** Keep the client module browser-only and fail with a visible configuration error when public variables are absent.

- [ ] **Step 4: Implement `AuthPanel` and mount it at the application boundary.** The app must not fetch market data or user state until a valid session exists. Preserve a local-development bypass only when the existing local server explicitly serves the current local auth mode; hosted mode must fail closed.

- [ ] **Step 5: Add typed API helpers for auth state and sign-out.** Do not put Alpaca credentials in the Supabase session metadata or browser storage.

- [ ] **Step 6: Run browser contract, lint, and typecheck.**

Run: `node --test test/auth-contract.test.mjs && npm run lint && npm run typecheck`

Expected: PASS.

- [ ] **Step 7: Commit the browser Auth foundation.**

```bash
git add cipher-system/web
git commit -m "feat: add Supabase browser authentication foundation"
```

---

## Task 3: Add fail-closed Supabase authentication to the Node proxy

**Files:**
- Create: `cipher-system/app/supabase_auth.mjs`
- Create: `cipher-system/app/test/supabase-auth.test.mjs`
- Modify: `cipher-system/app/server.mjs`
- Modify: `cipher-system/app/test/provider-capabilities-route.test.mjs`

**Interfaces:**
- `createSupabaseAuth({ supabaseUrl, anonKey, cacheTtlMs })` returns `{ validateRequest(req), invalidate(token) }`.
- `validateRequest(req)` returns `{ userId, accessToken }` or `null`; it never returns raw credential values.
- `server.mjs` uses `requireHostedUser(req)` for protected routes and preserves unauthenticated `GET /api/health` as liveness-only.

- [ ] **Step 1: Write tests for missing, malformed, expired, and valid Supabase tokens.** Stub the Auth `/auth/v1/user` HTTP response; assert that only a successful response with a valid user ID is accepted, and that malformed/failed responses return 401 without leaking the token.

- [ ] **Step 2: Run the focused Node tests and verify failure.**

Run: `node --test test/supabase-auth.test.mjs`

Expected: FAIL because `supabase_auth.mjs` and hosted auth integration do not exist.

- [ ] **Step 3: Implement Auth validation using the Supabase Auth HTTP endpoint and Node built-ins.** Use the backend-only Supabase URL and anon key, hash tokens for short-lived validation-cache keys, and never log the token or Authorization header. Do not add a JWT library unless a verified Supabase asymmetric-key requirement makes the HTTP validation insufficient.

- [ ] **Step 4: Add hosted-mode route enforcement to `server.mjs`.** Require Supabase Auth for every market-data, state, provider-session, scan, paper, and research route in hosted mode. Keep the current local password gate for private rollback mode. Add an internal proxy token header for Node-to-core requests and reject requests that reach the public listener with forged internal context.

- [ ] **Step 5: Add Origin/CSRF behavior.** Allow only the configured Vercel production/preview origins for credential-mutating requests, require JSON content type, bound request bodies, and do not accept user IDs from query/body as authority.

- [ ] **Step 6: Run focused app tests and syntax checks.**

Run: `node --test test/*.test.mjs && node --check server.mjs && node --check supabase_auth.mjs`

Expected: PASS.

- [ ] **Step 7: Commit the Node Auth boundary.**

```bash
git add cipher-system/app
 git commit -m "feat: enforce Supabase authentication at the API boundary"
```

---

## Task 4: Add request context and session-only Alpaca credentials

**Files:**
- Create: `cipher-system/core/request_context.py`
- Create: `cipher-system/core/provider_session.py`
- Create: `cipher-system/tests/test_request_context.py`
- Create: `cipher-system/tests/test_provider_session.py`
- Create: `cipher-system/app/provider_session_client.mjs`
- Create: `cipher-system/app/test/provider-session-route.test.mjs`
- Modify: `cipher-system/core/app.py`
- Modify: `cipher-system/core/disk_cache.py`
- Modify: `cipher-system/app/server.mjs`

**Interfaces:**
- `ProviderCredentials(key: str, secret: str, options_feed: str, stock_feed: str)` is never serializable in API responses.
- `SessionStore.connect(user_id: str, credentials: ProviderCredentials) -> str` returns an opaque session ID.
- `SessionStore.get(user_id: str, session_id: str) -> ProviderCredentials | None` enforces ownership and expiry.
- `SessionStore.disconnect(user_id: str, session_id: str) -> bool` removes credentials.
- `request_context.set_context(user_id, provider_session_id, credentials)` and `request_context.clear_context()` are required around every core request.
- `local_settings()` preserves local `.env` fallback only when no hosted provider session is active.

- [ ] **Step 1: Write failing tests for session-only behavior.** Cover ownership, 30-minute inactivity expiry, 12-hour absolute expiry, 32-session bound, logout/disconnect clearing, process-lifetime-only storage, and the assertion that `repr()`/JSON serialization does not expose secret values.

- [ ] **Step 2: Run the focused Python tests and verify failure.**

Run: `pytest -q tests/test_request_context.py tests/test_provider_session.py`

Expected: FAIL because the context/store modules do not exist.

- [ ] **Step 3: Implement the bounded in-memory store.** Use monotonic time for expiry, constant-time identity comparisons where applicable, lock all mutations, and store no file/database fallback. Keep only opaque IDs and non-secret timestamps in status responses.

- [ ] **Step 4: Add the internal Node-to-core connect/disconnect bridge.** Public Node routes authenticate the user, validate bounded payloads, and forward credentials only over the loopback internal channel. The core stores them and returns only the opaque session ID/status. Public status must never echo keys, secrets, or reversible fingerprints.

- [ ] **Step 5: Thread request context through `core/app.py`.** Set and clear context in a `try/finally` around dispatch. Update provider calls to resolve credentials from context and update in-memory/disk cache keys so user-session data cannot cross accounts. Disable persistent disk cache for session-scoped responses.

- [ ] **Step 6: Add route and secret-boundary tests.** Assert that credentials do not appear in response bodies, exceptions, logs captured by the test logger, disk-cache files, or source-generated browser artifacts.

- [ ] **Step 7: Run focused Python/Node tests and compile checks.**

Run: `pytest -q tests/test_request_context.py tests/test_provider_session.py tests/test_secret_boundary.py && node --test app/test/provider-session-route.test.mjs && python3 -m compileall -q core tests`

Expected: PASS.

- [ ] **Step 8: Commit the session credential boundary.**

```bash
git add cipher-system/core cipher-system/app
 git commit -m "feat: add session-only provider credential isolation"
```

---

## Task 5: Add the RLS-preserving Supabase repository layer

**Files:**
- Create: `cipher-system/core/supabase_rest.py`
- Create: `cipher-system/core/user_state.py`
- Create: `cipher-system/tests/test_supabase_rest.py`
- Modify: `cipher-system/core/app.py`

**Interfaces:**
- `SupabaseRestClient(base_url: str, user_jwt: str, timeout_seconds: float = 5.0)` performs requests with the user JWT and never a service-role key.
- `UserStateRepository(client, user_id)` rejects a missing/malformed user ID and never accepts a different payload `user_id`.
- Repository errors become explicit unavailable responses; they must not return empty arrays as if no records existed.

- [ ] **Step 1: Write fake-HTTP tests.** Verify the adapter sends `Authorization: Bearer <user JWT>` and `apikey: <anon key>` without logging either value, rejects payload user-ID mismatches, handles 401/403/5xx explicitly, and preserves `None`/unknown values.

- [ ] **Step 2: Run the focused tests and verify failure.**

Run: `pytest -q tests/test_supabase_rest.py`

Expected: FAIL because the adapter/repository modules do not exist.

- [ ] **Step 3: Implement the standard-library REST adapter.** Use `urllib.request`, bounded response bodies, JSON content types, timeout handling, and redacted error messages. Pass the user JWT through to Supabase RLS; never use a service-role key in request handling.

- [ ] **Step 4: Implement repository primitives.** Provide `list_rows`, `get_row`, `insert_row`, `update_row`, and `delete_row` methods that add the authenticated `user_id` server-side and reject client-supplied ownership fields.

- [ ] **Step 5: Add migration/rollback documentation.** Document how the operator applies SQL manually in Supabase, checks RLS, and returns the VM to local-only state if the project is unavailable.

- [ ] **Step 6: Run the focused tests and commit.**

```bash
pytest -q tests/test_supabase_rest.py
git add cipher-system/core/supabase_rest.py cipher-system/core/user_state.py cipher-system/tests/test_supabase_rest.py
git commit -m "feat: add RLS-preserving user-state repository"
```

---

## Task 6: Migrate user-owned modules one bounded group at a time

**Files:**
- Modify: `cipher-system/core/watchlists.py`
- Modify: `cipher-system/core/trader_journal.py`
- Modify: `cipher-system/core/workspace_layouts.py`
- Modify: `cipher-system/core/holdings.py`
- Modify: `cipher-system/core/alerts.py`
- Modify: `cipher-system/core/portfolio_risk.py`
- Modify: `cipher-system/core/paper_portfolio_api.py`
- Modify: `cipher-system/core/app.py`
- Modify: `cipher-system/web/src/lib/api.ts`
- Create: `cipher-system/tests/test_user_state_routes.py`

**Interfaces:**
- Each migrated route receives authenticated context from `request_context.current_user_id()`.
- Existing JSON shapes remain stable unless a field is explicitly marked unavailable.
- Mutations call the Supabase repository with the authenticated user ID and never a request-provided owner ID.

- [ ] **Step 1: Add route-level failing tests for user A/user B isolation.** For each module group, create two fake users, create same-named records, assert each user sees only their own rows, and attempt read/update/delete using the other user’s ID.

- [ ] **Step 2: Run the isolation suite before migration.**

Run: `pytest -q tests/test_user_state_routes.py`

Expected: FAIL because routes still use global local stores.

- [ ] **Step 3: Migrate workspace layouts, watchlists, and saved screens.** Preserve local mode behind the local auth flag; hosted mode uses the RLS repository. Add indexes/unique constraints from the SQL migration.

- [ ] **Step 4: Run only the workspace/watchlist isolation tests.**

Run: `pytest -q tests/test_user_state_routes.py -k 'workspace or watchlist or screen'`

Expected: PASS.

- [ ] **Step 5: Migrate journal entries and chart templates.** Preserve ticker filtering and explicit unavailable/error responses.

- [ ] **Step 6: Run journal/template isolation tests.**

Run: `pytest -q tests/test_user_state_routes.py -k 'journal or template'`

Expected: PASS.

- [ ] **Step 7: Migrate holdings, portfolio risk, alerts, and paper-user annotations.** Keep shared paper/research evidence read-only and move only user-created positions/settings/annotations into user-owned tables.

- [ ] **Step 8: Run the complete route isolation suite.**

Run: `pytest -q tests/test_user_state_routes.py`

Expected: PASS with no test requiring production Supabase credentials.

- [ ] **Step 9: Commit the module migration.**

```bash
git add cipher-system/core cipher-system/tests/test_user_state_routes.py cipher-system/web/src/lib/api.ts
git commit -m "feat: isolate hosted user state with Supabase RLS"
```

---

## Task 7: Add the hosted provider connection UI and API client behavior

**Files:**
- Create: `cipher-system/web/src/components/auth/ProviderConnectionPanel.tsx`
- Modify: `cipher-system/web/src/lib/api.ts`
- Modify: `cipher-system/web/src/components/panels/Settings.tsx`
- Modify: `cipher-system/web/src/app/page.tsx`
- Modify: `cipher-system/web/test/auth-contract.test.mjs`
- Create: `cipher-system/web/e2e/hosted-auth.spec.ts`

**Interfaces:**
- `connectProviderSession(input: { alpacaKey: string; alpacaSecret: string; optionsFeed: "opra" | "indicative"; stockFeed: "sip" | "iex" }): Promise<ProviderSessionStatus>`.
- `fetchProviderSessionStatus(): Promise<ProviderSessionStatus>` returns status/feed/caveat only.
- `disconnectProviderSession(): Promise<void>` clears backend session state.

- [ ] **Step 1: Write source-contract tests.** Assert provider form fields are cleared after submit, credentials are not saved through Supabase metadata, `localStorage`, `sessionStorage`, URL construction, or API error text, and the UI labels the connection session-only.

- [ ] **Step 2: Run the contract test and verify failure.**

Run: `node --test test/auth-contract.test.mjs`

Expected: FAIL because the provider panel/helpers do not exist.

- [ ] **Step 3: Implement the typed API helpers.** Attach the Supabase access token to every hosted request, send provider credentials only to the connect endpoint, and map 401/403/429/503 to explicit UI states.

- [ ] **Step 4: Implement the Settings provider panel.** Show connected/disconnected/expired/unavailable state, feed mode, caveats, and disconnect action. Never show key suffixes or account fingerprints.

- [ ] **Step 5: Add the authenticated browser journey.** The test uses local mock Supabase/API fixtures, creates two isolated users, connects one session, verifies the other cannot use it, signs out, and verifies the session is cleared.

- [ ] **Step 6: Run web tests, lint, typecheck, and the focused Playwright journey.**

Run: `node --test test/*.test.mjs && npm run lint && npm run typecheck && npx playwright test e2e/hosted-auth.spec.ts`

Expected: PASS; the browser test skips only when the explicitly configured local fixture server is unavailable.

- [ ] **Step 7: Commit the hosted connection UI.**

```bash
git add cipher-system/web
 git commit -m "feat: add session-only Alpaca connection flow"
```

---

## Task 8: Add hosted Vercel mode and manual deployment configuration

**Files:**
- Modify: `cipher-system/web/next.config.ts`
- Create: `cipher-system/web/vercel.json`
- Create: `cipher-system/web/.env.hosted.example`
- Modify: `cipher-system/web/deploy.sh`
- Modify: `cipher-system/README.md`
- Modify: `cipher-system/docs/provider-compatibility.md`
- Create: `cipher-system/tests/test_hosted_config.py`

**Interfaces:**
- Local build continues to produce the existing static `out/` used by `sync_web_build.sh`.
- Hosted build is selected by `CIPHER_HOSTED=1` and uses the manually configured API origin.
- Public Vercel variables are `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, and `NEXT_PUBLIC_CIPHER_API_ORIGIN`; no Alpaca or Supabase service-role variables are public.

- [ ] **Step 1: Write configuration contract tests.** Assert hosted variable names, absence of secret variable names in browser-facing configuration, stable HTTPS API-origin validation, and preservation of local static-export behavior.

- [ ] **Step 2: Run the configuration tests and verify failure.**

Run: `pytest -q tests/test_hosted_config.py`

Expected: FAIL because hosted configuration files and mode do not exist.

- [ ] **Step 3: Update Next configuration.** Keep `output: "export"` for local/private builds. When `CIPHER_HOSTED=1`, build the Vercel-compatible hosted mode and configure the API origin/rewrite behavior without embedding secrets.

- [ ] **Step 4: Add `vercel.json`/deployment documentation.** Document the operator workflow: create Vercel project, set the three public variables, configure the production domain, set the backend CORS origin, and deploy a preview. Do not include account tokens in source.

- [ ] **Step 5: Add production API-origin checks.** Verify HTTPS, expected Auth behavior, no direct core-port exposure, liveness response, and Vercel-to-VM connectivity before enabling production traffic.

- [ ] **Step 6: Run local build and configuration tests.**

Run: `pytest -q tests/test_hosted_config.py && npm run lint && npm run typecheck && npm run build`

Expected: PASS; existing local static publishing remains functional.

- [ ] **Step 7: Commit deployment configuration.**

```bash
git add cipher-system/web cipher-system/README.md cipher-system/docs/provider-compatibility.md cipher-system/tests/test_hosted_config.py
git commit -m "feat: add manual Vercel hosted deployment mode"
```

---

## Task 9: End-to-end hosted verification and release gate

**Files:**
- Modify: `cipher-system/web/e2e/hosted-auth.spec.ts`
- Create: `cipher-system/tests/test_hosted_security_gate.py`
- Modify: `cipher-system/docs/provider-compatibility.md`
- Modify: `cipher-system/docs/quantumhacks/release-checklist.md`

- [ ] **Step 1: Add the hosted security gate.** Scan source, generated frontend output, test artifacts, and configuration for raw Alpaca secret names/values, service-role variables, order-client strings, and direct core exposure. Assert that all unauthenticated state routes return 401.

- [ ] **Step 2: Run the security gate before deployment.**

Run: `pytest -q tests/test_hosted_security_gate.py tests/test_research_only_guard.py`

Expected: PASS with no secret or live-order findings.

- [ ] **Step 3: Deploy manually to Vercel preview and a non-production Supabase project.** The operator enters account credentials in provider dashboards; the agent does not receive them. Configure the VM API origin and allow only the preview origin.

- [ ] **Step 4: Run the two-user browser journey.** Verify sign-up/sign-in/sign-out, user-state isolation, session-only provider connection, expiry/disconnect behavior, explicit unavailable data, and no live-order controls.

Run: `npx playwright test e2e/hosted-auth.spec.ts`

Expected: PASS.

- [ ] **Step 5: Run the full active-app verification suite.**

Run:

```bash
python3 -m compileall -q core tests
node --check app/server.mjs
node --check app/launcher.mjs
node --test app/test/*.test.mjs web/test/*.test.mjs
pytest -q
cd web && npm run lint && npm run typecheck && npm run build
```

Expected: PASS, with network-dependent tests skipping cleanly when no provider credentials are configured.

- [ ] **Step 6: Verify rollback.** Switch the VM back to the current local password gate, confirm the existing private Tailscale deployment works, and confirm no user-state migration is destructive.

- [ ] **Step 7: Update hosted readiness documentation.** Record the production API hostname, Vercel deployment URL, Supabase project reference without secrets, test date, migration version, and known caveats. Never record credentials or session tokens.

- [ ] **Step 8: Commit the release gate documentation.**

```bash
git add cipher-system/tests/test_hosted_security_gate.py cipher-system/web/e2e/hosted-auth.spec.ts cipher-system/docs
 git commit -m "test: verify hosted multi-user security boundary"
```

---

## Execution checkpoints

- Do not deploy Vercel before Tasks 1–4 pass locally.
- Do not expose user-owned panels before the corresponding Task 6 isolation tests pass.
- Do not accept real Alpaca credentials in a hosted environment until Task 4
  secret-boundary tests pass.
- Do not call a Vercel preview public-ready until Tasks 8–9 pass against a
  non-production Supabase project.
- Do not claim a general public deployment without a stable HTTPS API hostname.
- Keep Devpost media/release artifacts separate from this migration; hosted
  deployment work must not alter the sanitized release package without a new
  audit.
