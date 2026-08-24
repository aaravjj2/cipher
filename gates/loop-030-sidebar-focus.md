# Gates: loop 030 Sidebar focus-visible

Scope: Gold focus rings on Sidebar buttons; guest nav filter unchanged.

- [x] N1: Sidebar a11y and product-hardening tests pass.
  CHECK: node --test cipher-system/web/test/sidebar-a11y.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/

- [x] N2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] N3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] N4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
