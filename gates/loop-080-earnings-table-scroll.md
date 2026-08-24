# Gates: loop 080 earnings table scrollport

Scope: earnings cards table scrolls in a named overflow-x/y region.

- [x] G1: Earnings chrome test passes.
  CHECK: node --test cipher-system/web/test/earnings-radar-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: Unvalidated gate copy still passes.
  CHECK: node --test cipher-system/web/test/earnings-unvalidated.test.mjs
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
