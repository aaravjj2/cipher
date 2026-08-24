# Loop 094 plan — no Operator/Settings in guest nav

**Goal:** Guests must not see Operator Status or Settings in the sidebar.

**Why this loop exists:** PROGRAM 094.

**Skip-if:** Guest catalog has no SYSTEM section. `GUEST_NAV_SECTIONS` drops empty sections after filtering to GUEST_PANEL_LABELS. guest-catalog already asserts no SYSTEM. Hosted guest E2E already expects zero Operator Status / Settings buttons.

**Files:** none.

**Preserve:** Signed-in SYSTEM nav, 29-panel guest catalog, GUEST_PANEL_LABELS filter.

**Anti-goals:** Do not delete SYSTEM for signed-in users. No catalog rewrite. No commit.

**Checks:** existing guest-catalog + product-hardening + hosted E2E from 085.

## Result 2026-08-23

Skipped — guest nav already omits SYSTEM; catalog and E2E already pin Operator Status / Settings at zero.
