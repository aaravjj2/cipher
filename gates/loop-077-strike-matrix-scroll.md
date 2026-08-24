# Gates: loop 077 Strike Matrix labelled scrollport

Scope: grid-scroll is a named region; overflow-auto and 26px cells stay.

- [x] G1: Heatmap accessibility test passes.
  CHECK: node --test cipher-system/web/test/heatmap-accessibility.test.mjs
  EXPECT: /fail 0/

- [x] G2: Cell height and public-OI date still pass.
  CHECK: node --test cipher-system/web/test/oi-date-public-oi.test.mjs cipher-system/web/test/touch-targets.test.mjs
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
