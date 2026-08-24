# Gates: loop 003 Setup Scanner chrome

Scope: Flatten Setup Scanner chrome; leave scan jobs, scoring, and comparison tray identity unchanged.

- [x] S1: Scanner UI and product-hardening tests pass.
  CHECK: node --test cipher-system/web/test/setup-scanner-ui.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/

- [x] S2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] S3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] S4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
