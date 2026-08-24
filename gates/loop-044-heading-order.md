# Gates: loop 044 heading order

Scope: sr-only h1 on panels that lacked headings; nested guest showcase uses h2.

- [x] G1: Heading-order, heatmap, and guest catalog tests pass.
  CHECK: node --test cipher-system/web/test/heading-order.test.mjs cipher-system/web/test/heatmap-accessibility.test.mjs cipher-system/web/test/guest-catalog.test.mjs
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
