# Gates: loop 032 Auth panel labels, autocomplete, and focus

Scope: Explicit labels/ids, autocomplete tokens, gold focus; guest path unchanged.

- [x] A1: Auth a11y and contract tests pass.
  CHECK: node --test cipher-system/web/test/auth-panel-a11y.test.mjs cipher-system/web/test/auth-contract.test.mjs
  EXPECT: /fail 0/

- [x] A2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] A3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] A4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
