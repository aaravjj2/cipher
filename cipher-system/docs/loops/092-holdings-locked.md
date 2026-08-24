# Loop 092 plan — Holdings locked

**Goal:** Guests cannot open live Holdings. The nav item stays locked with the shared boundary sentence. No holdings skeleton.

**Why this loop exists:** PROGRAM 092.

**Skip-if:** Catalog mode is `locked`. PanelHost guest filter sends it to GuestShowcase. guest-locked-boundary already lists Holdings. loading-status-a11y already forbids a holdings skeleton.

**Files:** none.

**Preserve:** GUEST_LOCKED_BOUNDARY, no brokerage copy, no holdings skeleton, 29-panel catalog.

**Anti-goals:** Do not add a skeleton. Do not hide the locked nav item. No catalog/PanelHost rewrite. No commit.

**Checks:** existing guest-locked-boundary + loading-status-a11y (already green).

## Result 2026-08-23

Skipped — Holdings is already locked for guests with the shared boundary sentence and still has no skeleton.
