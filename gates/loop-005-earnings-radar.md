# Gates: loop 005 Earnings Radar chrome

Scope: Flatten Earnings Radar chrome; missing stays unknown.

- [ ] E1: Earnings Radar UI and product-hardening tests pass.
  CHECK: node --test cipher-system/web/test/earnings-radar-ui.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/

- [ ] E2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [ ] E3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [ ] E4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok

- [ ] E5: Hosted guest E2E still passes.
  CHECK: bash -lc 'cd cipher-system/web && CIPHER_E2E_URL=https://cipher-main.tail39504f.ts.net:8443 npx playwright test e2e/guest-complete-audit.spec.ts'
  EXPECT: /passed/
