# Loop 062 plan — Night Vision after chrome (source test)

**Goal:** Source test that Night Vision chrome stayed flat after loop 002 without geometry or order changes.

**Why this loop exists:** PROGRAM 062. Tests only if coverage is missing.

**Skip-if:** `web/test/night-vision-ui.test.mjs` already asserts flat chrome, gold focus, GEX heuristic copy, missing gamma/OI unavailable, `fetchNightVisionReplay`, chart `role="img"`, and no order identifiers. Geometry export is still required in that file; `nightVisionGeometry.test.mjs` remains the mapping contract.

**Files:** none. Do not add a duplicate test.

**Preserve:** Geometry file, heatmap cell height, public-OI heuristic, unknown ≠ 0.

**Anti-goals:** No product change, no commit, no invented coverage.

**Checks:** existing night-vision-ui + geometry tests pass.

## Result 2026-08-23

Skipped — loop 002 already added `night-vision-ui.test.mjs`; geometry tests still pass (6 pass / 0 fail). No duplicate test file.
