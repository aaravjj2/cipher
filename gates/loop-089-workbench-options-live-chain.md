# Gates: loop 089 Workbench Options live chain

Scope: guest Workbench Options is the live chain, not the demo Options Terminal nav.

- [x] G1: Workbench UI contract still passes.
  CHECK: node --test cipher-system/web/test/ticker-workbench-ui.test.mjs
  EXPECT: /fail 0/

- [x] G2: No-order-ticket contract still passes.
  CHECK: node --test cipher-system/web/test/workbench-no-order-ticket.test.mjs
  EXPECT: /fail 0/

- [x] G3: Guest catalog / demo-nav contract still passes.
  CHECK: node --test cipher-system/web/test/guest-catalog.test.mjs
  EXPECT: /fail 0/

- [x] G4: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] G5: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] G6: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
