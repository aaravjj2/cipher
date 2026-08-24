# Gates: loop 082 backtest table scrollport

Scope: backtest partition table scrolls in a named overflow-x/y region.

- [x] G1: Remaining chrome (backtest needles) still passes.
  CHECK: node --test cipher-system/web/test/remaining-chrome-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] G3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] G4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
