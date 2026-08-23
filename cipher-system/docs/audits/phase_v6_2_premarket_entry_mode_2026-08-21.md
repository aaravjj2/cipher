# Phase 6.2 — Autopilot premarket-entry mode (2026-08-21)

## What changed

The paper autopilot previously could only enter positions after a regular-session
confirmation scan (09:35–11:30 ET). This adds an **opt-in premarket-entry mode**
so the ranked premarket setup can drive entries on its own.

| Layer | Change |
|---|---|
| `core/paper_executor/autopilot_planner.py` | New `premarket_payload()` builds executor cards from the ranked plan candidates using the fresh premarket evidence; `build_premarket_plan(..., premarket_entry_allowed=...)` reflects the mode in `entry_policy` |
| `core/paper_executor/autopilot_scheduler.py` | `run_cycle()` submits the premarket payload to the paper executor when the mode is on; gated by `CIPHER_AUTOPILOT_PREMARKET_ENTRY` env or `--premarket-entry` flag |
| `core/paper_executor/config.py` + `config/paper_autopilot_shadow.yaml` | `strategy.allow_premarket_entries` (default `false`, shipped config `true`) |
| `core/paper_executor/policy.py` | `entry_window_allowed()` admits cipher-scanner cards before the window opens **only** in premarket hours and **only** when the flag is on; the window close still binds and other scanner types are unaffected |

## Safety bounds (unchanged / reinforced)

- **Paper-only.** The executor is shadow mode; no live order code, broker client,
  or order endpoint is involved. Payloads assert `paper_only: true` and
  `live_execution_capability: false`.
- **Same evidence contract.** `premarket_payload()` re-verifies every candidate
  against the plan gates: OPRA feed, current freshness, premarket session phase,
  sufficient coverage, actionable geometry, matching direction, valid snapshot id.
  Names that failed `build_premarket_plan` can never enter.
- **Executor still decides.** Setup allowlist, ticker allowlist, portfolio limits,
  contract selection, and synthetic fills all still run inside the executor.
- **Window close binds.** A cipher card at/after 11:30 ET is rejected even with
  the flag on; only times strictly before the open window are admitted.
- **Fail-closed default.** Without `CIPHER_AUTOPILOT_PREMARKET_ENTRY` (or the CLI
  flag), behavior is identical to before: the plan is watch-only and the executor
  is never called during premarket.

## Verification

- New regression tests: planner payload acceptance/rejection, scheduler
  submission (on) and no-submission (off), policy window gating (cipher on /
  flash off / post-window blocked / default off), shipped-config admission.
- Full suite: **1071 passed, 2 skipped**; compile + Node checks clean.

## Deployment

- `CIPHER_AUTOPILOT_PREMARKET_ENTRY=1` added to `/etc/cipher/cipher.env`
  (read by the `cipher-paper-autopilot.timer` → `cipher-paper-autopilot.service`
  scheduler and the executor service).
- `cipher-paper-autopilot-executor.service` restarted with the new admission
  config; active.
- Next live exercise: the 08:45 ET premarket run (or the 09:15 retry) will build
  today's plan and, if the mode is on, submit the ranked candidates to the paper
  book. `runtime/data/paper_runtime/autopilot/status.json` and the per-day cycle
  traces record `premarket_entries` / `premarket_batch_id` / rejection counts.

To disable: remove the env line (or set it to `0`) and set
`allow_premarket_entries: false` in `config/paper_autopilot_shadow.yaml`.
