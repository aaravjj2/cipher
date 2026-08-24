# Loop 037 plan — Spyglass tab arrow keys

**Goal:** Bio / Contract Search tabs use a `tablist` with ArrowLeft/Right roving focus, matching Workbench and Strike Matrix. Gold `focus-visible`. Side inference copy stays inferred.

**Why this loop exists:** PROGRAM 037. Tabs are click-only.

**Architecture:** Change only `SpyglassHeaderTabs`. Keep `overflow-x-auto rounded-[10px]` heatmap contract. Do not change bid/ask side mapping.

**Files:**
- Modify: `web/src/components/panels/Spyglass.tsx`
- Create: `web/test/spyglass-a11y.test.mjs`
- Create: `gates/loop-037-spyglass-tabs.md`

**Preserve:** `p.side === "buy" ? "ASK"`, caveat on contract search, heatmap scrollport class.

**Anti-goals:** No order surface. No commit.

**Checks:** spyglass-a11y + heatmap-accessibility; full web Node; lint; typecheck; sync.
