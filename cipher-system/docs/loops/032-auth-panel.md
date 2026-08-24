# Loop 032 plan — Auth panel labels, autocomplete, and focus

**Goal:** Email/password fields have explicit `htmlFor`/`id`/`name`, keep autocomplete tokens, and show gold `focus-visible` on fields and actions.

**Why this loop exists:** PROGRAM 032. Labels wrap inputs but have no ids; controls have no focus ring.

**Architecture:** Class + attribute only on `AuthPanel.tsx`. Do not change cookie session exchange, guest path, or persistSession:false.

**Files:**
- Modify: `web/src/components/auth/AuthPanel.tsx`
- Create: `web/test/auth-panel-a11y.test.mjs`
- Create: `gates/loop-032-auth-panel.md`

**Preserve:** `Continue as guest`, `no broker-order authority`, `autoComplete="email"`, current/new-password, no localStorage, no service-role key.

**Anti-goals:** No credential persistence. No live broker. No commit.

**Checks:** auth-panel-a11y + auth-contract; full web Node; lint; typecheck; sync.

## Result 2026-08-23

Email and password have matching `htmlFor`/`id`/`name`. Autocomplete remains `email` / `current-password` / `new-password`. Gold `focus-visible` on fields, submit, guest, and mode links. Guest path and no-storage contract unchanged.

