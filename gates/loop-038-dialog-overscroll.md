# Gates: loop 038 dialog/drawer overscroll

Scope: overscroll-behavior contain on dialogs and mobile nav drawer; heatmap scrollports unchanged.

- [x] G1: Overlay overscroll, palette, sidebar, and heatmap tests pass.
  CHECK: node --test cipher-system/web/test/overlay-overscroll.test.mjs cipher-system/web/test/command-palette-a11y.test.mjs cipher-system/web/test/sidebar-a11y.test.mjs cipher-system/web/test/heatmap-accessibility.test.mjs
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
