# Loop 070 plan — Night Vision sticky scrollport axes

**Goal:** Sticky scrollports name both overflow axes so a `top: 0` header does not stick to the wrong container.

**Why this loop exists:** PROGRAM 070, existing invariant.

**Skip-if:** Night Vision has no `sticky` descendants. The invariant already lives in `heatmap-accessibility.test.mjs` for Strike Matrix (`overflow-auto`) and Trident (`overflow-y-auto overflow-x-hidden`). NV X-Ray/SP docks use `overflow-y-auto` without sticky; do not retune them here. Geometry file stays untouched.

**Files:** none.

**Preserve:** Heatmap 26px cells, Matrix/Trident overflow contracts, nightVisionGeometry.

**Anti-goals:** No invented overflow-x on NV docks, no commit.

**Checks:** existing sticky-scrollport test passes.

## Result 2026-08-23

Skipped — Night Vision has no sticky descendants. Both-axes invariant already asserted for Matrix/Trident (`heatmap-accessibility`, 12 pass / 0 fail). Geometry untouched.
