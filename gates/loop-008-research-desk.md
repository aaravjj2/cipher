# Gates: loop 008 Research Desk chrome

Scope: Flatten Research Desk chrome; remain read-only.

- [ ] R1: Research Desk UI tests pass.
  CHECK: node --test cipher-system/web/test/research-desk-ui.test.mjs
  EXPECT: /fail 0/

- [ ] R2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [ ] R3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok
