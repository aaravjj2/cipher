# Loop 054 plan — Autopilot zero-trade is healthy

**Goal:** A session with zero paper trades reads as healthy when the executor is `HEALTHY_NO_SETUP`, not as a stalled or failed agent.

**Why this loop exists:** PROGRAM 054. Empty candidates already say no-setup is valid. The paper-execution step still shows `0 submissions`, the header uses the waiting accent, and the guest Autopilot demo only narrates a filled trade.

**Architecture:** When `operating_state === HEALTHY_NO_SETUP`, paint the header positive, label `0` paper trades as healthy no-trade, and hide a stale entry-block chip. Guest Autopilot demo adds one zero-trade-is-healthy line. Do not change executor state logic or broker adapters.

**Files:**
- Modify: `web/src/components/panels/Autopilot.tsx`, `web/src/components/panels/GuestShowcase.tsx`
- Create: `web/test/autopilot-zero-trade.test.mjs`
- Create: `gates/loop-054-autopilot-zero-trade.md`

**Preserve:** Paper-only copy, `Models cannot authorize an order`, `Live execution` / `Impossible`, `A healthy no-setup session is a valid outcome`, HEALTHY_NO_SETUP token in API consumers, `rounded-full` pill.

**Anti-goals:** No Alpaca/broker edits. No catalog/PanelHost change. No commit. No hosted E2E.

**Checks:** autopilot-zero-trade + autopilot-ui + guest-catalog; full web Node; lint; typecheck; sync.
