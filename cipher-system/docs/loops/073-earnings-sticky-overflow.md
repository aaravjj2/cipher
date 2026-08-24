# Loop 073 plan — Earnings table sticky scrollport axes

**Goal:** Sticky scrollports on Earnings Radar name both overflow axes.

**Why this loop exists:** PROGRAM 073, same invariant as 070.

**Skip-if:** `EarningsRadar.tsx` has no `sticky`. The table wrapper already uses `overflow-x-auto overflow-y-auto`, asserted in `earnings-radar-ui.test.mjs`. Earnings-table density is loop 080.

**Files:** none.

**Preserve:** UNVALIDATED_FOR_LIVE_OPTIONS_PNL, missing stays unknown.

**Anti-goals:** Do not add a fake sticky thead. No commit.

**Checks:** existing earnings-radar-ui test still passes.

## Result 2026-08-23

Skipped — no sticky on Earnings Radar; both overflow axes already asserted (1 pass / 0 fail). Table density is loop 080.
