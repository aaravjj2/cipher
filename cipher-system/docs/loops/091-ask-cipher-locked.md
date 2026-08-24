# Loop 091 plan — Ask Cipher locked

**Goal:** Guests cannot run Ask Cipher. The nav item stays locked with the shared boundary sentence. Workbench Agent stays hidden.

**Why this loop exists:** PROGRAM 091.

**Skip-if:** Catalog mode is `locked`. PanelHost guest filter sends it to GuestShowcase. guest-locked-boundary already lists Ask Cipher and pins the one boundary sentence. Workbench already uses `tab === "Agent" && !guestMode`.

**Files:** none.

**Preserve:** Shared GUEST_LOCKED_BOUNDARY, 29-panel catalog, no LLM for guests.

**Anti-goals:** Do not hide the nav item (locked panels stay visible). No catalog/PanelHost rewrite. No commit.

**Checks:** existing guest-locked-boundary + ticker-workbench-ui (already green).

## Result 2026-08-23

Skipped — Ask Cipher is already locked for guests with the shared boundary sentence; Agent tab stays signed-in only.
