# Gates: loop 004 Morning Brief chrome

Scope: Flatten Morning Brief leftover cards; keep compact copy.

- [ ] B1: Morning Brief UI and product-hardening tests pass.
  CHECK: node --test cipher-system/web/test/morning-brief-ui.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/

- [ ] B2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [ ] B3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok
