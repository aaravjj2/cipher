# Gates: loop 086 Night Vision fallback vs live

Scope: guest Night Vision attributes live vs demo honestly; geometry untouched.

- [x] G1: Night Vision hybrid source tests pass.
  CHECK: node --test cipher-system/web/test/night-vision-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: Geometry and skeleton contracts still pass.
  CHECK: node --test cipher-system/web/test/heatmap-accessibility.test.mjs cipher-system/web/test/night-vision-missing-levels.test.mjs
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
