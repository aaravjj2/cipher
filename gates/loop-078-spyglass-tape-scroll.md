# Gates: loop 078 Spyglass tape scrollport

Scope: Spyglass tapes are named overflow-x-auto regions; 560px min-width kept.

- [x] G1: Spyglass a11y test passes.
  CHECK: node --test cipher-system/web/test/spyglass-a11y.test.mjs
  EXPECT: /fail 0/

- [x] G2: Heatmap sticky overflow contract still passes.
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
