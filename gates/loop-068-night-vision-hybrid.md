# Gates: loop 068 Night Vision hybrid

Scope: guest Night Vision is hybrid live-or-fallback; headed overflow stays 0.

- [x] G1: Hybrid wiring source test passes.
  CHECK: node --test cipher-system/web/test/night-vision-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: Geometry mapping tests still pass.
  CHECK: node --test cipher-system/web/test/nightVisionGeometry.test.mjs
  EXPECT: /fail 0/

- [x] G3: Hosted Night Vision hybrid check passes.
  CHECK: bash -lc 'cd cipher-system/web && CIPHER_E2E_URL=https://cipher-main.tail39504f.ts.net:8443 npx playwright test e2e/night-vision-hybrid.spec.ts'
  EXPECT: /passed/

- [x] G4: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/
