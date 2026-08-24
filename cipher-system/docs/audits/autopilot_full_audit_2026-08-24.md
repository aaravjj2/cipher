# Full Autopilot Audit — 2026-08-24

## Outcome

The continuous paper autopilot was fully audited end to end: executor runtime,
entry/exit paths, quote management, scheduler, planner, notifications, replay,
training export, promotion gate, status surfaces, systemd units, and the
project-wide execution-boundary constraints. Every defect found was fixed in
this pass except where explicitly noted below. The suite is green:
1116 Python tests passed, 2 skipped, 0 failed; app Node tests 33/33; web Node
tests 110/110. The live executor was restarted onto the fixed code at
15:25 UTC with reconciliation, OPRA readiness, broker readiness, and no entry
blocker.

## Fixed defects

Entry path (evidence: six unclassified `WORKER_ERROR "stale quote"` events
during Monday's premarket):

1. `runtime.process_entry_once` and `_process_spread_entry` now catch fill-
   simulator `ValueError`s and persist a classified `ENTRY_BLOCKED` decision
   (`SKIPPED_MARKET_DATA_UNAVAILABLE`, `SKIPPED_WIDE_SPREAD`,
   `SKIPPED_NO_CONTRACT`) instead of crashing the entry worker. This restores
   the release criterion that every confirmation ends in a candidate, a
   classified rejection, or a classified data failure.
2. A failed Alpaca-paper exit submission now backs off 30 s before resubmitting;
   previously the 0.5 s monitor loop retried immediately every pass.
3. Mark payloads no longer contain negative `quote_age_seconds`; spread marks
   record explicit long/short quote ages instead of raw quote objects.
4. SIGTERM/SIGINT now shut the HTTP server and worker threads down cleanly so
   systemd restarts record a SHUTDOWN event instead of dying mid-write.
5. The forward queue's retry schedule was dead code (`due_forward_items` was
   only consulted at process start); the forward loop now requeues due items
   every 60 s, so one transient network error no longer strands a forward until
   restart. Backlog accounting counts only retryable states.
6. The ENTRY_CONFIRMATION scheduler cycle guarded its scan requests but not the
   provider-session setup itself; a missing token or failed connect crashed the
   cycle with a traceback. It now records a retryable blocked cycle, symmetric
   with premarket discovery.
7. Replay reports used hardcoded `autopilot_replay_2026-08-19.*` filenames, so
   every run overwrote the same files; names are now derived from the replay.
8. Training export labelled unknown-P&L samples as unprofitable; unknown stays
   `None`. The prospective export included the chronological holdout and embargo
   dates, leaking the evaluation set into anything trained on it; they are now
   excluded and `prospective_samples` reflects it. The exporter also releases
   its read handle on the live ledger (`contextlib.closing`).
9. The legacy registry branch in `promotion_gate` selected events without
   ordering, making "latest state" nondeterministic; ordered by `rowid`.
10. The standalone local scan scheduler's entry window ran to 15:00 ET against
    the plan contract's 11:30 ET; aligned.
11. The operator status probe timed out at 0.75 s (flapping to DATA_FAILURE
    under scan load) and its offline fallback dropped keys the success path
    always provides; timeout raised to 2.5 s and the offline schema mirrors the
    success schema.
12. `scripts/gex_daily_capture.sh` called the live brokerage hostname
    `api.alpaca.markets` for a read-only clock check; switched to the paper API
    host, restoring compliance with the no-live-hostname constraint.
13. A stale uncommitted edit had changed skew-map quality vocabulary to an
    uppercase `"PROVISIONAL"`, breaking its own test contract; the intended
    precedence logic (provisional dominates while under 20 sessions) was kept
    with the canonical lowercase value.

## Verified clean

- `/v2/orders` appears only inside `core/paper_executor/alpaca_paper_broker.py`
  plus guard deny-lists and negative tests.
- No alpaca-py `TradingClient`/`OrderClient` imports anywhere.
- No hardcoded secrets; provider credentials never reach API responses or the
  browser (`server.mjs` responds `{status, read_only}` only).
- Governance promotion stops at `LIVE_REVIEW_REQUIRED`; transitions out are
  terminal and `live_execution_enabled` raises `PromotionBlockedError`.
- Kill switch is fail-closed sentinel-file based; default start mode is shadow;
  broker host is hard-locked to the paper API.

## Noted, not changed

- After ~11:55 ET no scheduler cycle refreshes `status.json` while the executor
  keeps managing positions; operator freshness handling may want a sparse
  afternoon cadence.
- `local_scan_scheduler --workers` is a deliberate hard rate-budget clamp of 1;
  documented rather than removed.
- The MSFT exit mark carried `feed_degraded: true` because degradation is a
  global flag over all subscribed symbols; it is informational only and exits
  still required fresh per-symbol quotes.

## Follow-up fix (same day): training timer units restored to source

The training units were installed and enabled on the VM but missing from the
`cipher-github/infra/gcp-cipher-vm/systemd/` source tree, and neither
`configure-vm.sh` copy enabled any of the paper-autopilot or context timers —
a fresh provision would have lost the whole stack. Fixed by porting all ten
missing unit files (training, event-context, finbert-context,
operational-metrics, option-history pairs) into the cipher-github tree, adding
all eight autopilot/context enable entries to both `configure-vm.sh` copies,
and validating every unit with `systemd-analyze verify`. The training job was
also run manually against the fixed exporter; it correctly reports
`INSUFFICIENT_PROSPECTIVE_DATA` (2 of 100 required closed samples), which is
the honest blocked state rather than a failure. The daily 16:20 ET timer will
keep it current as the paper ledger grows.

## Verification

- Python: 1116 passed, 2 skipped, 0 failed.
- App Node tests: 33 passed. Web Node tests: 110 passed.
- `compileall` clean; all `app/*.mjs` parse.
- Executor restarted 15:25:14 UTC: mode paper, reconciliation passed, market
  data ready, broker ready, entry blocker none.
