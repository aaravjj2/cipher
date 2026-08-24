# Gates: loop 084 strategy list scrollport

Scope: strategy catalog verdict register is a named overflow-x-auto rounded-[8px] region.

- [x] G1: Remaining chrome catalog contract still passes.
  CHECK: node --test cipher-system/web/test/remaining-chrome-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: Heatmap accessibility catalog contract still passes.
  CHECK: node --test cipher-system/web/test/heatmap-accessibility.test.mjs
  EXPECT: /fail 0/

- [x] G3: Purple leftover / token contract still passes.
  CHECK: node --test cipher-system/web/test/purple-leftover.test.mjs
  EXPECT: /fail 0/

- [x] G4: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] G5: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] G6: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
