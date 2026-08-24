# Gates: loop 053 guest tape vs live quote

Scope: guest ticker tape is demo; header/workbench quotes are labelled live.

- [x] G1: Guest tape / live quote test passes.
  CHECK: node --test cipher-system/web/test/guest-tape-live-quote.test.mjs
  EXPECT: /fail 0/

- [x] G2: Workbench and catalog contracts still pass.
  CHECK: node --test cipher-system/web/test/ticker-workbench-ui.test.mjs cipher-system/web/test/guest-catalog.test.mjs
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
