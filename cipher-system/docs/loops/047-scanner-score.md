# Loop 047 plan — scanner score ≠ P(profit)

**Goal:** Scanner score reads as a 0–100 structural rank, not a win-rate or P(profit). Scoring formula, jobs, and `/100` scale stay the same.

**Why this loop exists:** PROGRAM 047. Intro already disclaims confidence ≠ win rate, but the big `{n}/100` marks and the “Score” column have no structural label. A bare /100 next to a ticker is the usual probability lie.

**Architecture:**
- Intro: one sentence that structural score ranks geometry, not P(profit).
- Shared `StructuralScore` for the three card `/100` marks: caption `structural /100` plus title.
- Comparison tray: prefix the number with `structural`.
- Ranked table header `Score` → `Structural`; cell `aria-label`.
- CSV header `Score` → `Structural score`.

**Files:**
- Modify: `web/src/components/panels/SetupScanner.tsx`
- Create: `web/test/scanner-score-honesty.test.mjs`
- Create: `gates/loop-047-scanner-score.md`

**Preserve:** Presets, gates-before-score, confidence copy, comparison tray fields, no order surface, cluster overflow unchanged.

**Anti-goals:** Do not change `weight_lab` / cluster formulas. No Edge* recovery. No commit. No hosted E2E (copy-only).

**Checks:** scanner-score-honesty + setup-scanner-ui + setup-scanner-a11y; full web Node; lint; typecheck; sync.
