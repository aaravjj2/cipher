# Gates: loop 033 Shared ui/button Cipher gold focus

Scope: Replace shadcn ring-ring focus with Cipher gold on the shared Button primitive.

- [x] B1: Shared button a11y test passes.
  CHECK: node --test cipher-system/web/test/shared-button-a11y.test.mjs
  EXPECT: /fail 0/

- [x] B2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [x] B3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok

- [x] B4: Published web tree stays in sync.
  CHECK: bash -lc './cipher-system/scripts/sync_web_build.sh && ./cipher-system/scripts/sync_web_build.sh --check && echo sync-ok'
  EXPECT: sync-ok
