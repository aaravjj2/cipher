# Gates: Options Terminal visual migration

Scope: Restyle the Options Terminal panel to DESIGN.md / Skew Map surfaces without changing chain, structure, or order-boundary behavior.

- [x] T1: Options Terminal source has no large card radius and uses gold focus rings.
  CHECK: node --test cipher-system/web/test/options-terminal-ui.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: # todo 0 | # duration_ms 87.364926

- [x] T2: Web Node tests still pass after the panel change.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: # todo 0 | # duration_ms 1932.874425

- [x] T3: Guest catalog still treats Options Terminal as demo with no order surface.
  CHECK: node --test cipher-system/web/test/guest-catalog.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: # todo 0 | # duration_ms 96.529405

- [x] T4: Lint, types, production build, and published tree stay in sync.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && cd ../.. && ./cipher-system/scripts/sync_web_build.sh --check'
  EXPECT: In sync: app/public matches web/out.
  EVIDENCE: In sync: app/public matches web/out.

- [x] T5: Hosted guest audit still renders Options Terminal on desktop and mobile.
  CHECK: bash -lc 'cd cipher-system/web && CIPHER_E2E_URL=https://cipher-main.tail39504f.ts.net:8443 npx playwright test e2e/guest-complete-audit.spec.ts'
  EXPECT: /2 passed/
  EVIDENCE: (node:291551) Warning: The 'NO_COLOR' env is ignored due to the 'FORCE_COLOR' env being set. | (Use `node --trace-warnings ...` to show where the warning was created)

- [x] T6: Headed Playwright shows Options Terminal with overflow 0 and no console errors.
  EVIDENCE: xvfb headed session `ot` at 1440x900 opened Ticker Workbench Options (live Options Terminal heading, expiry chips, chain table). OVERFLOW=0. Console Total messages: 0 (Errors: 0, Warnings: 0). Artifact: artifacts/options-terminal-guest-workbench.png. Session closed.
