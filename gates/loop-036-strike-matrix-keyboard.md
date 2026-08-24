# Gates: loop 036 Strike Matrix toolbar keyboard

Scope: Arrow-key radiogroups on matrix toolbar; heatmap cells unchanged.

- [x] M1: Strike Matrix a11y and heatmap tests pass.
  CHECK: node --test cipher-system/web/test/strike-matrix-a11y.test.mjs cipher-system/web/test/heatmap-accessibility.test.mjs
  EXPECT: /fail 0/

- [x] M2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] M3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] M4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
