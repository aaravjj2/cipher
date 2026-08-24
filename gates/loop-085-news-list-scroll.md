# Gates: loop 085 news list scrollport

Scope: news headline list scrolls in a named overflow-x/y region.

- [x] G1: Remaining chrome (news needles) still passes.
  CHECK: node --test cipher-system/web/test/remaining-chrome-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: News skeleton contract still passes.
  CHECK: node --test cipher-system/web/test/heatmap-accessibility.test.mjs
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
