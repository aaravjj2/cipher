# Paper Autopilot operator runbook

Cipher's autopilot is a staged, self-managed paper workflow:

1. At 08:45 and 09:15 ET it scans the liquid foundation universe plus delayed
   Finviz discovery. It writes a watch plan; it cannot open a position.
2. From 09:45 through 11:30 ET it rescans only planned names. A fresh OPRA-backed,
   sufficient-coverage, `triggered` Flash Agentic card in the same direction is
   required before submission to the local simulator.
3. The executor selects a 1–3 DTE option, simulates the entry at ask plus
   slippage, marks liquidation at bid, and exits on the underlying target or
   invalidation, +20% / -15% option P&L, 45 minutes, or 15:45 ET.
4. The post-close job exports point-in-time features and later outcomes. Training
   stays blocked until at least 100 replayable outcomes across 20 market dates
   provide a chronological, embargoed holdout.

FinBERT is advisory context only. FinGPT is not enabled. Neither a language
model nor a future custom ranker can authorize a live order.

The optional local sentiment environment is declared in
`requirements-sentiment.txt`. `cipher-finbert-context.timer` refreshes public
SEC/Yahoo documents before planning and runs the revision-pinned
`ProsusAI/finbert` model locally on CPU. If the model or sources are unavailable,
the plan records sentiment as stale/unavailable; price/structure rules do not
silently substitute a score.

## Safety controls

- Executor binds only to `127.0.0.1:8787`.
- Market data comes through Cipher core's Alpaca SIP/OPRA read-only endpoints.
- Indicative option fallback blocks an entry.
- Maximum 3 open positions, 1 per ticker, 5 new positions/day, 2 stopped
  positions/day, 1 contract/position, and $500 maximum option cost.
- Premarket entries and overnight positions are disabled.
- Creating `runtime/data/paper_runtime/STOP_PAPER_EXECUTOR` blocks new entries.
- The deployed executor backend is `simulated`: it never constructs the legacy
  Alpaca-paper broker adapter and every modeled fill is persisted locally as
  `SIMULATED_FILLED` with `external_order_capability: false` in health status.
- Historical Alpaca-paper rows remain labelled by provenance; they are not
  relabelled as Cipher-modeled fills.

## Repository units

- `cipher-paper-autopilot-executor.service`
- `cipher-paper-autopilot.service` / `.timer`
- `cipher-paper-autopilot-training.service` / `.timer`
- `cipher-finbert-context.service` / `.timer`

Install these only on the private Cipher host after the core and OPRA health
checks pass. The status is visible at `/api/autopilot-status` and on Morning
Brief. `executor offline` is a hard failure, not a signal to bypass controls.

## V2 paper comparison rollout — September 10, 2026

The executor service now starts four isolated internal-paper ledgers on loopback
ports 8787–8790: baseline, two-observation confirmation, cost filter (round-trip
friction at most one-third of stop budget), and exit policy (take profit at 50%
of remaining maximum spread profit). All receive the same scanner batches and
share persisted market observations. Flash is excluded. Each portfolio has its
own controls, five-entry daily cap and one-entry-per-ticker daily cap. Every
losing close counts toward the two-loss limit, regardless of exit reason.

Baseline history remains intact; candidate ledgers start empty. Versioned code
and configuration manifests are immutable. Increment the experiment version
before changing frozen execution/evaluation code; do not delete manifests to
force a restart. New versions do not inherit old performance evidence.

`/api/paper/cohorts` on port 8787 and the Autopilot dashboard expose current-version
metrics separately from historical totals. Process readiness is distinct from
data readiness. Off-hours chain access is not proof of executable fresh quotes.

Promotion requires 60 closed trades across 20 actual exchange sessions, positive
cost-adjusted expectancy, profit factor above 1.1, positive session-bootstrap
lower bound adjusted for three comparisons, registry eligibility, complete
monitoring evidence, matched baseline comparison and operational reconciliation.
The primary portfolio designation changes only at a pre-entry session boundary;
comparison portfolios continue running and existing positions retain their
original policy. No live or external broker execution is enabled.

Recorded-decision replay is available via `scripts/audit_autopilot_performance.py
--db PATH --json --replay`. Missing quotes remain explicit gaps. This is not an
alternative-strategy historical backtest or evidence of future profitability.

Deployment evidence is in `AUTOPILOT_ROLLOUT_GATES.md`. The pre-rollout SQLite and
service-unit backup is under runtime/data/paper_runtime/
autopilot-v2-20260909T191200Z-bbfbvgnv. Never restore an older ledger over newly
executed paper trades. Stop the service and preserve all current ledgers before
any code rollback; restore compatible code/configuration together.
