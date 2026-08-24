# Gates: loops 004–028 panel chrome batch

Scope: Flatten remaining DESIGN.md panel chrome. Alerts (018) and Strategy Catalog xl/lg (024) skipped.

- [x] C1: Chrome and product-hardening tests pass.
  CHECK: node --test cipher-system/web/test/morning-brief-ui.test.mjs cipher-system/web/test/earnings-radar-ui.test.mjs cipher-system/web/test/paper-portfolios-ui.test.mjs cipher-system/web/test/autopilot-ui.test.mjs cipher-system/web/test/research-desk-ui.test.mjs cipher-system/web/test/remaining-chrome-ui.test.mjs cipher-system/web/test/heatmap-accessibility.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/

- [x] C2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] C3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] C4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok

- [x] C5: Hosted guest E2E still passes.
  CHECK: bash -lc 'cd cipher-system/web && CIPHER_E2E_URL=https://cipher-main.tail39504f.ts.net:8443 npx playwright test e2e/guest-complete-audit.spec.ts'
  EXPECT: /passed/
