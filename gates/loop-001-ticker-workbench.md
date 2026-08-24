# Gates: loop 001 Ticker Workbench

Scope: Flatten Ticker Workbench chrome and keep guest Overview inside Chart/Options.

- [x] W1: Workbench source invariants pass.
  CHECK: node --test cipher-system/web/test/ticker-workbench-ui.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: # todo 0 | # duration_ms 108.499143

- [x] W2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: # todo 0 | # duration_ms 2021.583714

- [x] W3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok
  EVIDENCE: type-ok
