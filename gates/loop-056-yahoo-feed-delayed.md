# Gates: loop 056 Yahoo feed labelled delayed

Scope: yahoo/yfinance display as delayed, never live.

- [x] G1: Yahoo delayed labelling test passes.
  CHECK: node --test cipher-system/web/test/yahoo-feed-delayed.test.mjs
  EXPECT: /fail 0/

- [x] G2: Guest tape vs live quote still distinguishes demo tape.
  CHECK: node --test cipher-system/web/test/guest-tape-live-quote.test.mjs
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
