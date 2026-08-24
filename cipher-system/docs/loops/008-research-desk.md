# Loop 008 plan — Research Desk chrome

**Goal:** Flatten Research Desk chrome; keep read-only jumps.

**Why this loop exists:** Candidate cards and summary tiles still use xl/lg/md radius.

**Architecture:** Class-only. Add gold `focus-visible` on Options/Chart/Journal jumps.

**Files:**
- Modify: `web/src/components/panels/ResearchDesk.tsx`
- Create: `web/test/research-desk-ui.test.mjs`
- Create: `gates/loop-008-research-desk.md`

**Preserve:** Intraday/weekly tabs; `eligible_for_deeper_review`; no order identifiers.

**Anti-goals:** No scoring change. No commit.

**Checks:** focused tests; full web Node; lint; typecheck; build+sync.

## Result 2026-08-23

Research Desk tiles/cards flattened; read-only jumps kept. Web Node later in this batch. Guest E2E at loop 005 cadence.
