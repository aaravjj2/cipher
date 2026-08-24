# Gates: loop 045 touch targets

Scope: 32px chrome icon controls; heatmap cells remain 26px.

- [x] G1: Touch-target, header, sidebar, and heatmap tests pass.
  CHECK: node --test cipher-system/web/test/touch-targets.test.mjs cipher-system/web/test/header-a11y.test.mjs cipher-system/web/test/sidebar-a11y.test.mjs cipher-system/web/test/heatmap-accessibility.test.mjs
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

- [x] G5: Hosted guest catalog still renders.
  CHECK: bash -lc 'cd cipher-system/web && CIPHER_E2E_URL=https://cipher-main.tail39504f.ts.net:8443 npx playwright test e2e/guest-complete-audit.spec.ts'
  EXPECT: /2 passed/
