# Gates: loop 052 Night Vision missing levels ≠ 0

Scope: missing GEX/VEX is unknown, not $0. Geometry file untouched.

- [x] G1: Missing-level honesty test passes.
  CHECK: node --test cipher-system/web/test/night-vision-missing-levels.test.mjs
  EXPECT: /fail 0/

- [x] G2: Night Vision chrome/geometry contracts still pass.
  CHECK: node --test cipher-system/web/test/night-vision-ui.test.mjs cipher-system/web/test/nightVisionGeometry.test.mjs
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
