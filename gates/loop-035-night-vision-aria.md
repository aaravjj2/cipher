# Gates: loop 035 Night Vision accessible names

Scope: Name refresh/timeframe/strike controls; do not change geometry.

- [x] V1: Night Vision a11y, chrome, and geometry tests pass.
  CHECK: node --test cipher-system/web/test/night-vision-a11y.test.mjs cipher-system/web/test/night-vision-ui.test.mjs cipher-system/web/test/nightVisionGeometry.test.mjs
  EXPECT: /fail 0/

- [x] V2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] V3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] V4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
