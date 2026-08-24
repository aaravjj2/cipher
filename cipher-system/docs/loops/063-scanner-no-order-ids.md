# Loop 063 plan — Scanner no order identifiers (source test)

**Goal:** Source test that Setup Scanner has no order/broker identifiers.

**Why this loop exists:** PROGRAM 063. Tests only if coverage is missing.

**Skip-if:** `setup-scanner-ui.test.mjs`, `setup-scanner-a11y.test.mjs`, `scanner-score-honesty.test.mjs`, and `scanner-empty-job.test.mjs` already `doesNotMatch` `submit_order|place_order|create_order|TradingClient|OrderClient`. Scanner source has none of those strings.

**Files:** none. Do not add a duplicate test.

**Preserve:** Scoring copy, empty-job copy, no order surface.

**Anti-goals:** No product change, no commit, no invented coverage.

**Checks:** existing scanner tests pass.

## Result 2026-08-23

Skipped — order-identifier assertions already live in four scanner tests (4 pass / 0 fail). No duplicate test file.
