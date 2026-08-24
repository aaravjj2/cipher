# Loop 006 plan — Paper Portfolios chrome

**Goal:** Flatten Paper Portfolios chrome without changing ledger language or backfilling.

**Why this loop exists:** Dense rounded stats, executor banner, and portfolio details.

**Architecture:** Class-only. Status language HEALTHY_NO_SETUP etc. stays in API consumers as already tested.

**Files:**
- Modify: `web/src/components/panels/PaperPortfolios.tsx`
- Create: `web/test/paper-portfolios-ui.test.mjs`
- Create: `gates/loop-006-paper-portfolios.md`

**Preserve:** `No simulated fill was created`; `PAPER ONLY · READ ONLY · EXECUTION CAPABILITY: FALSE`; estimated vs path copy already in product-hardening.

**Anti-goals:** No ledger backfill. No broker connect. No commit.

**Checks:** focused + product-hardening; full web Node; lint; typecheck.

## Result 2026-08-23

Paper stats/details flattened; PAPER ONLY copy unchanged. Web Node later in this batch. Guest E2E at loop 005 cadence.
