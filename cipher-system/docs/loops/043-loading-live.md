# Loop 043 plan — loading status aria-live

**Goal:** Panel-level async loading copy that is not a skeleton still announces via `role="status"` `aria-live="polite"`. Skeleton regions already do this.

**Why this loop exists:** PROGRAM 043. Only `SkeletonRegion` has `aria-live`. Autopilot, Paper Portfolios, Earnings Radar, Holdings, Company Context, Chart Workbench, Options Terminal, Spyglass tape read, and Scanner history still show mute text.

**Architecture:** Add `LoadingStatus` in `skeleton.tsx` (no pulse, no `cipher-skeleton-region`, so Holdings stays skeleton-free). Wrap those loading returns. Do not put `aria-live` on error states. Do not add skeletons to Holdings.

**Files:**
- Modify: `web/src/components/ui/skeleton.tsx`, Autopilot, PaperPortfolios, EarningsRadar, Holdings, CompanyContext, ChartWorkbench, OptionsTerminal, Spyglass, SetupScanner
- Create: `web/test/loading-status-a11y.test.mjs`
- Create: `gates/loop-043-loading-live.md`

**Preserve:** Loading copy strings, paper-only autopilot, unknown-not-zero, heatmap overflow, Holdings no Skeleton.

**Anti-goals:** No heatmap redo. No commit.

**Checks:** loading-status-a11y + heatmap Holdings assertion; full web Node; lint; typecheck; sync.
