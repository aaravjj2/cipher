# Loop 059 plan — Empty scanner job state

**Goal:** A finished scan with zero qualifying names is an empty job, not a failed scan and not “no clusters match filters.”

**Why this loop exists:** PROGRAM 059. Completing with `top: []` still sets `hasResults`. Cipher view then shows an empty comparison tray. Cluster view says filters matched nothing. Idle copy is hidden.

**Architecture:** When `hasResults && rawResults.length === 0`, show “Scan finished with no qualifying setups.” Keep the filter-empty sentence only when clusters exist but the filter hides them. Do not change scoring.

**Files:**
- Modify: `web/src/components/panels/SetupScanner.tsx`
- Create: `web/test/scanner-empty-job.test.mjs`
- Create: `gates/loop-059-scanner-empty-job.md`

**Preserve:** Structural score honesty, confidence coverage sentence, CSV “Structural score”, `--accent-foreground` CTA, no order surface.

**Anti-goals:** No catalog change. No hosted E2E. No commit.

**Checks:** scanner-empty-job + scanner-score-honesty; full web Node; lint; typecheck; sync.
