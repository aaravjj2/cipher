# Gates: loop 050 paper captured vs estimated marks

Scope: captured fill/mid labels on paper surfaces; earnings remains estimated.

- [x] G1: Paper marks and related chrome tests pass.
  CHECK: node --test cipher-system/web/test/paper-marks-honesty.test.mjs cipher-system/web/test/paper-portfolios-ui.test.mjs cipher-system/web/test/morning-brief-ui.test.mjs
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
