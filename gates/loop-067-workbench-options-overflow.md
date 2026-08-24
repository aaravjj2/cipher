# Gates: loop 067 workbench Options overflow

Scope: guest Workbench Options chain stays an internal scrollport.

- [x] G1: Options Terminal source locks the chain scrollport.
  CHECK: node --test cipher-system/web/test/options-terminal-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: Hosted workbench Options overflow check passes.
  CHECK: bash -lc 'cd cipher-system/web && CIPHER_E2E_URL=https://cipher-main.tail39504f.ts.net:8443 npx playwright test e2e/workbench-options-overflow.spec.ts'
  EXPECT: /passed/

- [x] G3: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/
