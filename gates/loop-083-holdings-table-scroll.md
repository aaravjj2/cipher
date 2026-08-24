# Gates: loop 083 holdings table scrollport

Scope: holdings position grids scroll in named overflow-x/y regions.

- [x] G1: Remaining chrome (holdings needles) still passes.
  CHECK: node --test cipher-system/web/test/remaining-chrome-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: Holdings still has no skeleton.
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
