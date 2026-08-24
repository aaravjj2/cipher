# Loop 090 plan — PanelHost guest filter

**Goal:** Guests only get hybrid/live panels from PanelHost; demo and locked panels stay on GuestShowcase. Sidebar still filters to GUEST_PANEL_LABELS.

**Why this loop exists:** PROGRAM 090.

**Skip-if:** PanelHost already returns GuestShowcase when `guestMode && guestModeType !== "hybrid" && guestModeType !== "live"`. guest-catalog and guest-locked-boundary already pin that, plus Sidebar `GUEST_PANEL_LABELS.has`.

**Files:** none.

**Preserve:** 29-panel catalog, hybrid Night Vision / Strike Matrix / Ticker Workbench, demo Options Terminal nav.

**Anti-goals:** Do not retune the filter “to be safer.” No catalog/PanelHost edit. No commit.

**Checks:** existing guest-catalog + guest-locked-boundary (already green in the 089 suite).

## Result 2026-08-23

Skipped — guest filter already asserted. No PanelHost or catalog change. Hosted catalog E2E not due (no UI/catalog/host change).
