# Gates: loop 088 Options Terminal nav demo showcase

Scope: guest Options Terminal nav stays a labelled demo; live chain is not this nav item.

- [x] G1: Guest catalog demo contract still passes.
  CHECK: node --test cipher-system/web/test/guest-catalog.test.mjs
  EXPECT: /fail 0/

- [x] G2: Signed-in Options Terminal contract still passes.
  CHECK: node --test cipher-system/web/test/options-terminal-ui.test.mjs
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
