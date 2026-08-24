# Gates: loop 058 loading ellipsis

Scope: loading copy uses … not ASCII ...

- [x] G1: Loading ellipsis test passes.
  CHECK: node --test cipher-system/web/test/loading-ellipsis.test.mjs
  EXPECT: /fail 0/

- [x] G2: LoadingStatus a11y still passes.
  CHECK: node --test cipher-system/web/test/loading-status-a11y.test.mjs
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
