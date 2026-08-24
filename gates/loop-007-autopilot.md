# Gates: loop 007 Autopilot chrome

Scope: Flatten Autopilot chrome; no broker adapter edits.

- [ ] A1: Autopilot UI tests pass.
  CHECK: node --test cipher-system/web/test/autopilot-ui.test.mjs
  EXPECT: /fail 0/

- [ ] A2: Web Node suite still passes.
  CHECK: node --test cipher-system/web/test/*.test.mjs
  EXPECT: /fail 0/

- [ ] A3: Lint and types pass.
  CHECK: bash -lc 'cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && echo type-ok'
  EXPECT: type-ok
