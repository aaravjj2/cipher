# Gates: loop 057 OI date where public OI is used

Scope: heatmap legend, matrix, Trident, options chain show OI as of date.

- [x] G1: OI date test passes.
  CHECK: node --test cipher-system/web/test/oi-date-public-oi.test.mjs
  EXPECT: /fail 0/

- [x] G2: Public-OI heuristic and heatmap 26px still pass.
  CHECK: node --test cipher-system/web/test/gex-heuristic-copy.test.mjs
  EXPECT: /fail 0/

- [x] G3: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] G4: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] G5: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
