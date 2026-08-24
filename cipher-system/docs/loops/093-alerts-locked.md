# Loop 093 plan — Alerts locked

**Goal:** Guests cannot create or deliver alerts. The nav item stays locked with the shared boundary sentence.

**Why this loop exists:** PROGRAM 093.

**Skip-if:** Catalog mode is `locked`. PanelHost guest filter sends it to GuestShowcase. guest-locked-boundary already lists Alerts.

**Files:** none.

**Preserve:** GUEST_LOCKED_BOUNDARY, alerts semantics tests for signed-in (crossing/staleness, no tab-open-only claim), 29-panel catalog.

**Anti-goals:** Do not hide the locked nav item. Do not change signed-in Alerts copy. No catalog/PanelHost rewrite. No commit.

**Checks:** existing guest-locked-boundary (already green).

## Result 2026-08-23

Skipped — Alerts is already locked for guests with the shared boundary sentence.
