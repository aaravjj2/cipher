# Gates: loop 060 locked guest one sentence

Scope: locked guest panels share one Guest boundary sentence.

- [x] G1: Locked-boundary test passes.
  CHECK: node --test cipher-system/web/test/guest-locked-boundary.test.mjs
  EXPECT: /fail 0/

- [x] G2: Guest catalog still complete.
  CHECK: node --test cipher-system/web/test/guest-catalog.test.mjs
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
