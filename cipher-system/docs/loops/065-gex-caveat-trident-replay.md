# Loop 065 plan — GEX caveat in Trident / Replay (source test)

**Goal:** Source test that Trident and GEX Replay state the public-OI GEX heuristic.

**Why this loop exists:** PROGRAM 065. Tests only if coverage is missing.

**Skip-if:** Coverage exists.
- `GexReplay.tsx` empty state includes `GEX is a public-OI heuristic, not verified dealer positioning`; `gex-heuristic-copy.test.mjs` asserts it.
- Trident uses `ExposureLegend`, whose copy includes that sentence plus OI-as-of; `oi-date-public-oi.test.mjs` asserts both.

**Files:** none. Do not add a duplicate test. Do not change heatmap cell height (`26px`).

**Preserve:** Public-OI heuristic, unknown ≠ 0, Replay overflow contract.

**Anti-goals:** No product change, no commit, no invented coverage.

**Checks:** existing gex-heuristic-copy + oi-date-public-oi tests pass.

## Result 2026-08-23

Skipped — Replay empty copy and Trident’s `ExposureLegend` already carry the public-OI sentence, and both are asserted (2 pass / 0 fail). No duplicate test.
