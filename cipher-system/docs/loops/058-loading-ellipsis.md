# Loop 058 plan — Loading strings use ellipsis …

**Goal:** User-visible loading copy uses the typographic ellipsis `…`, not three ASCII dots.

**Why this loop exists:** PROGRAM 058. Most panels already use `…`. Spyglass still says `Scanning ${ticker}...`. Flow Tape’s aria-label omits the ellipsis that the skeleton label has.

**Architecture:** Replace those two strings. Do not restyle skeletons or change 250ms reveal.

**Files:**
- Modify: `Spyglass.tsx`, `FlowTape.tsx`
- Create: `web/test/loading-ellipsis.test.mjs`
- Create: `gates/loop-058-loading-ellipsis.md`

**Preserve:** Spyglass `overflow-x-auto rounded-[10px]`, LoadingStatus polite live, heatmap 26px, no order surface.

**Anti-goals:** No catalog change. No hosted E2E. No commit.

**Checks:** loading-ellipsis + loading-status-a11y; full web Node; lint; typecheck; sync.
