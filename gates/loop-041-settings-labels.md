# Gates: loop 041 Settings labels

Scope: Settings refresh radiogroup labelled; provider fields htmlFor/id; session-only credentials.

- [x] G1: Settings a11y, auth-contract, and product-hardening tests pass.
  CHECK: node --test cipher-system/web/test/settings-a11y.test.mjs cipher-system/web/test/auth-contract.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/

- [x] G2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] G3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] G4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
