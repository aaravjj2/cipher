# Loop 074 plan — Backtest results sticky scrollport axes

**Goal:** Sticky scrollports on Backtest results name both overflow axes.

**Why this loop exists:** PROGRAM 074, same invariant as 070.

**Skip-if:** `Backtest.tsx` and `OptionsBacktest.tsx` have no `sticky`. Filter results use `overflow-x-auto` without a sticky thead. Backtest density/overflow is loop 082.

**Files:** none.

**Preserve:** Chronological holdouts, no order surface.

**Anti-goals:** Do not add a fake sticky thead. No commit.

**Checks:** none; skip-if is true.

## Result 2026-08-23

Skipped — Backtest results have no sticky descendants. Table overflow is loop 082.
