# Gates: loop 076 scanner results scrollport

Scope: ranked scan results are an internal labelled scrollport.

- [x] G1: Scanner UI test passes.
  CHECK: node --test cipher-system/web/test/setup-scanner-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: Empty-job and score honesty still pass.
  CHECK: node --test cipher-system/web/test/scanner-empty-job.test.mjs cipher-system/web/test/scanner-score-honesty.test.mjs
  EXPECT: /fail 0/

- [x] G3: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] G4: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] G5: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
