# Loop 004 plan — Morning Brief leftover cards

**Goal:** Flatten Morning Brief card chrome to DESIGN.md surfaces without re-expanding copy.

**Why this loop exists:** Three remaining `rounded-xl`/`rounded-lg` surfaces after the compact rewrite.

**Architecture:** Class-only preserve-mode. Keep compact four-card layout. Add gold `focus-visible` on jump buttons.

**Files:**
- Modify: `web/src/components/panels/MorningBrief.tsx`
- Create: `web/test/morning-brief-ui.test.mjs`
- Create: `gates/loop-004-morning-brief.md`

**Preserve:** Market now / Focus / Paper status / Setups to review; flow unknown-vs-zero copy; autopilot state strings; `no broker-order capability`; `Card` count ≤ 5.

**Anti-goals:** No copy expansion. No order surface. No commit.

**Checks:** focused morning + product-hardening tests; full web Node; lint; typecheck.

## Result 2026-08-23

Cards and jump buttons flattened; compact copy unchanged. Web Node later in this batch. Guest E2E at loop 005 cadence.
