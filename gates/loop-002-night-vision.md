# Gates: loop 002 Night Vision chrome

Scope: Flatten Night Vision chrome; leave geometry and replay identity unchanged.

- [x] N1: Night Vision UI and geometry tests pass.
  CHECK: node --test cipher-system/web/test/night-vision-ui.test.mjs cipher-system/web/test/nightVisionGeometry.test.mjs cipher-system/web/test/heatmap-accessibility.test.mjs cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: # todo 0 | # duration_ms 793.900698

- [x] N2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: # todo 0 | # duration_ms 1941.221244

- [x] N3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok
  EVIDENCE: type-ok
