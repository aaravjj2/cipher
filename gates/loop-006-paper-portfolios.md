# Gates: loop 006 Paper Portfolios chrome

Scope: Flatten Paper Portfolios chrome; do not backfill ledger.

- [ ] P1: Paper UI and product-hardening tests pass.
  CHECK: node --test cipher-system/web/test/paper-portfolios-ui.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/

- [ ] P2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [ ] P3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok
