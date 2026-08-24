# Loop 051 plan — Skew Map PROVISIONAL below 20 sessions

**Goal:** A name with fewer than 20 stored sessions stays `PROVISIONAL`. Other quality problems append reasons; they must not promote the name. Do not join week-over-week or earnings.

**Why this loop exists:** PROGRAM 051. Core already flags `sessions < 20`, then later overwrites `quality` to `limited`/`suspect`. The panel shows a lowercase badge and no session count.

**Architecture:**
- `build_skew_map`: if `sessions < 20`, `quality` stays `PROVISIONAL`. Coverage/price issues only add reasons.
- Panel: show `{sessions}/20` and a sentence that the map stays PROVISIONAL below 20.
- Guest Skew Map row names the gate.

**Files:**
- Modify: `core/skew_map.py`, `tests/test_skew_map.py`, SkewMap.tsx, GuestShowcase.tsx
- Create: `web/test/skew-provisional.test.mjs`
- Create: `gates/loop-051-skew-provisional.md`

**Preserve:** Formula, quadrants, missing return stays unknown, no order surface, no WoW/earnings join.

**Anti-goals:** Do not change Night Vision. No commit. No hosted E2E (copy + quality gate).

**Checks:** python test_skew_map; skew-provisional; full web Node; lint; typecheck; sync.
