# Loop 064 plan — no live Alpaca host in web/src

**Goal:** Source test that `web/src` never contains `api.alpaca.markets`.

**Why this loop exists:** PROGRAM 064. Autopilot and Options Terminal already forbid the host in those files; there is no walk of all `web/src`. Browser must not hardcode the live broker host.

**Architecture:** Reuse the `purple-leftover` directory walk. Fail if any `.ts`/`.tsx`/`.css` file contains `api.alpaca.markets` or `paper-api.alpaca.markets`. Alpaca as a provider name in Settings stays allowed.

**Files:**
- Create: `web/test/no-alpaca-host.test.mjs`
- Create: `gates/loop-064-no-alpaca-host.md`

**Preserve:** Session-only provider fields, paper-only Autopilot copy, no `cipher-alpaca-agent` edits.

**Anti-goals:** No product change, no commit, no hosted E2E.

**Checks:** no-alpaca-host + full web Node.

## Result 2026-08-23

- `web/test/no-alpaca-host.test.mjs` walks `web/src` `.ts`/`.tsx`/`.css` for `api.alpaca.markets` and `paper-api.alpaca.markets`.
- Provider name "Alpaca" in Settings remains allowed. No product files changed.
- Web Node **108 pass / 0 fail**. Gates ALL MET. No sync (test-only).
