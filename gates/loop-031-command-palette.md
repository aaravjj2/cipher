# Gates: loop 031 Command palette keyboard and focus

Scope: Gold focus/selection on command palette; guest still cannot open it.

- [x] P1: Palette a11y tests pass.
  CHECK: node --test cipher-system/web/test/command-palette-a11y.test.mjs
  EXPECT: /fail 0/

- [x] P2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] P3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] P4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
