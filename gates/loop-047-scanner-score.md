# Gates: loop 047 scanner score honesty

Scope: structural score copy; scoring formula unchanged.

- [x] G1: Scanner honesty and existing scanner tests pass.
  CHECK: node --test cipher-system/web/test/scanner-score-honesty.test.mjs cipher-system/web/test/setup-scanner-ui.test.mjs cipher-system/web/test/setup-scanner-a11y.test.mjs
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
