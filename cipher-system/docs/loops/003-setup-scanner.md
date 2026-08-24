# Loop 003 plan — Setup Scanner chrome

**Goal:** Flatten Setup Scanner chrome to DESIGN.md surfaces without changing scan jobs, scoring, presets, comparison tray, or evidence navigation.

**Why this loop exists:** `SetupScanner.tsx` has the densest remaining card radius (`rounded-xl`, `rounded-[12px]`, `rounded-lg` on presets, jobs, results, and comparison). Handoff lists it as the next high-value dense surface after Night Vision.

**Architecture:** Class-only preserve-mode. Status pills, live dots, and progress tracks may stay `rounded-full`. Add gold `focus-visible` rings on mode pills and primary actions. Do not edit `core/scanner.py` or any score formula.

**Files:**
- Modify: `web/src/components/panels/SetupScanner.tsx`
- Create: `web/test/setup-scanner-ui.test.mjs`
- Create: `gates/loop-003-setup-scanner.md`

**Preserve:** SCAN_PRESETS labels, `confidence describes evidence coverage, not a predicted win rate`, `Rejection funnel:`, `ResultComparison`, `<details key={raw.ticker}`, three-candidate cap, Expected move / Catalyst `Not observed`, snapshot id slice, `cipher:night-vision-replay`, guest demo catalog routing.

**Anti-goals:** No scoring, universe, or poll-interval change. No order surface. No commit.

**Checks:** focused scanner + product-hardening tests; full web Node; lint; typecheck; build+sync. Guest E2E still deferred until loop 005.

## Result 2026-08-23

- Presets, discovery bar, advanced details, funnel tiles, comparison tray, history popover, and warning banners use border surfaces. Status pills, live dots, and progress tracks stay `rounded-full`.
- Gold `focus-visible` rings on presets, mode pills, scan CTAs, discovery, CSV/history, and evidence jumps.
- `OutlineButton` default tone renamed `accent` (still `--accent` gold). Scoring, presets, poll intervals, and comparison tray logic unchanged.
- Web Node 69 passed / 0 failed. Lint + typecheck OK. Published tree in sync. Guest E2E deferred until loop 005.
