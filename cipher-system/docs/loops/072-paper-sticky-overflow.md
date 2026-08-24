# Loop 072 plan — Paper ledger sticky scrollport axes

**Goal:** Sticky scrollports on the paper ledger name both overflow axes.

**Why this loop exists:** PROGRAM 072, same invariant as 070.

**Skip-if:** `PaperPortfolios.tsx` has no `sticky`. Ledger tables use `overflow-x-auto` without sticky headers. Paper-table density/overflow is loop 079.

**Files:** none.

**Preserve:** HEALTHY_NO_SETUP, captured vs estimated marks, no order surface.

**Anti-goals:** Do not add a fake sticky thead. No commit.

**Checks:** none; skip-if is true.

## Result 2026-08-23

Skipped — Paper ledger has no sticky descendants. Table overflow is loop 079.
