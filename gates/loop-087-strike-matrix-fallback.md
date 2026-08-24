# Gates: loop 087 Strike Matrix fallback vs live

Scope: guest Strike Matrix attributes live vs demo honestly; 26px cells and overflow-auto kept.

- [x] G1: Strike Matrix a11y/hybrid tests pass.
  CHECK: node --test cipher-system/web/test/strike-matrix-a11y.test.mjs
  EXPECT: /fail 0/

- [x] G2: Heatmap cell and unknown contracts still pass.
  CHECK: node --test cipher-system/web/test/heatmap-accessibility.test.mjs cipher-system/web/test/oi-date-public-oi.test.mjs
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
