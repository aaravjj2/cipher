# Loop 050 plan — paper estimated vs captured marks

**Goal:** Paper P&L copy distinguishes captured option fills/mids from estimated heuristic premiums. Do not invent marks. Earnings estimated labels stay estimated.

**Why this loop exists:** PROGRAM 050. Paper Portfolios headlines say “marked” / “realized” without saying those are captured mids/fills. Morning Brief says “Realized P&L” for the same book. Earnings Radar already labels estimated heuristic P&L.

**Architecture:**
- Paper Portfolios: label marked/realized as captured mid/fills; one sentence that estimated premiums are not used and unknown marks stay labelled.
- Morning Brief: “Captured paper P&L” plus the same distinction.
- Keep `mark_status` columns and `{data.caveat}`.

**Files:**
- Modify: PaperPortfolios.tsx, MorningBrief.tsx
- Create: `web/test/paper-marks-honesty.test.mjs`
- Create: `gates/loop-050-paper-marks.md`

**Preserve:** Opportunity-path copy (`not hypothetical option fills`), no broker orders, overflow tables, no live fills.

**Anti-goals:** Do not capture new quotes. No Skew Map. No commit. No hosted E2E (copy-only).

**Checks:** paper-marks-honesty + paper-portfolios-ui + morning-brief-ui; full web Node; lint; typecheck; sync.
