# Loop 071 plan — Scanner results sticky scrollport axes

**Goal:** Sticky scrollports on scanner results name both overflow axes.

**Why this loop exists:** PROGRAM 071, same invariant as 070.

**Skip-if:** `SetupScanner.tsx` has no `sticky`. History listbox is `overflow-y-auto` without sticky headers. Scanner-results density/overflow is loop 076, not this test ID.

**Files:** none.

**Preserve:** Scoring copy, empty-job copy, no order identifiers.

**Anti-goals:** Do not add a fake sticky header. No commit.

**Checks:** none; skip-if is true.

## Result 2026-08-23

Skipped — Setup Scanner has no sticky descendants. Both-axes sticky invariant remains Matrix/Trident only.
