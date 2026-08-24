# Loop 007 plan — Autopilot chrome

**Goal:** Flatten Autopilot chrome; keep paper-only and no-setup-is-valid copy.

**Why this loop exists:** Header, step cards, evidence, and boundary sections still use `rounded-xl`.

**Architecture:** Class-only. Keep `rounded-full` operating-state pill. Do not edit broker adapters.

**Files:**
- Modify: `web/src/components/panels/Autopilot.tsx`
- Create: `web/test/autopilot-ui.test.mjs`
- Create: `gates/loop-007-autopilot.md`

**Preserve:** `AUTONOMOUS · PAPER ONLY · AUDITABLE`; `Models cannot authorize an order`; `A healthy no-setup session is a valid outcome`; `Live execution` / `Impossible`; `Alpaca Paper` vs local simulation labels.

**Anti-goals:** No broker adapter edits. No commit.

**Checks:** focused tests; full web Node; lint; typecheck.

## Result 2026-08-23

Autopilot surfaces flattened; healthy no-setup copy unchanged. Web Node later in this batch. Guest E2E at loop 005 cadence.
