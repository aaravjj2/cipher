# Gates: loop 034 Scanner accessible names

Scope: CSV download and saved-history controls named; scoring unchanged.

- [x] S1: Scanner a11y, chrome, and product-hardening tests pass.
  CHECK: node --test cipher-system/web/test/setup-scanner-a11y.test.mjs cipher-system/web/test/setup-scanner-ui.test.mjs cipher-system/web/test/product-hardening.test.mjs
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
