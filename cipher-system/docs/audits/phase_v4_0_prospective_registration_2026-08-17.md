# Phase V4.0 audit — TSLA and August 17 prospective registration

Date: 2026-08-17 UTC
Verdict: accepted for pre-session monitoring; no strategy is validated by this phase

## Registered before the session

- `tsla_stable_wall_rejection_v1`, minimum prospective sample 20.
- `spartan_weekly_radar_2026_08_17`, covering the supplied ten-name radar and
  August 21 option strikes where eligible observed contracts exist.
- Start time for both: 2026-08-17 09:30 America/New_York.
- Existing historical TSLA results remain descriptive and are not included in
  the prospective denominator.

The monitor evaluates only the latest fully closed five-minute bar inside a
three-minute availability window. This is intentionally a no-backfill design.
Zero signals before the open is the expected truthful state.

## Correctness and safety

- Separate SQLite ledger records programs, signals, option legs, events, and runs.
- Option entry is modeled at observed ask plus 0.5% and $0.01; exit is observed
  bid less 0.5% and $0.01, plus $1.30 round-trip commission per contract.
- TSLA uses conservative stop-first handling if stop and target share a five-minute bar.
- Missing contracts, quotes, gamma, or OI block the observation rather than becoming zero.
- GEX is labelled a public-OI heuristic, not verified dealer positioning.
- API and UI report `paper_only`, `read_only`, and `execution_capability: false`.
- Program responses and signal payloads carry a SHA-256 fingerprint of the frozen configuration.
- Session filtering uses America/New_York and is regression-tested across DST.
- Safety grep found prohibited order terms only inside the defensive blocklist.

## Product and operations integration

- Paper Portfolios now displays prospective program state and cohort progress.
- `/api/prospective-fronttests` exposes a bounded read-only audit model.
- The authenticated same-origin proxy allowlists the new endpoint.
- `cipher-prospective-fronttests.timer` is enabled and runs every minute on weekdays.
- The daily Discord portfolio digest now includes prospective cohort deltas.
- Operator status monitors the new ledger and the local backup now includes it.
- A fresh five-store backup was restore-verified after deployment.

## Verification

- Focused Python tests: 16 passed.
- Active Python suite: 926 passed, 2 skipped.
- Node app/web suites: 70 passed.
- TypeScript, ESLint, compileall, Node syntax, Next.js production build: passed.
- npm production dependency audit: zero vulnerabilities.
- Atomic publication: `web/out` and `app/public` match.
- Browser E2E discovery: 12 tests skipped because the deployed password file is
  intentionally unreadable to the interactive user; the authenticated web gate
  itself passed live and returned 401 without a session.
- Unified runtime audit: `COMPLETE`; all service, timer, registry, backup,
  authentication, and no-live-execution checks passed.

An unscoped root `pytest` invocation was also attempted. It correctly does not
represent the active product suite: root discovery entered archived worktrees,
quarantined vendor repositories, duplicate modules, and optional Torch/Pydantic
projects. The authoritative scope is `cipher-system/tests`, per `AGENTS.md`.

## Remaining Phase 0 work

- Observe the first live session and confirm run/coverage records during RTH.
- Add explicit per-run missing-input/eligible-non-signal coverage.
- Confirm option marks and session/weekly resolution on actual signals.
- Confirm the scheduled Discord delivery after the first market day.
- Do not tune either cohort while it is accumulating evidence.
