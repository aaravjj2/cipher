# Autopilot implementation contract

Implement the approved five-part plan using existing internal paper infrastructure.
Preserve all pre-existing edits and historical trades. No external orders.

## Ownership and interfaces
- execution agent: contract_selector.py, fill_simulator.py, runtime.py, quote_manager.py, position_manager.py, config.py, exchange_calendar.py and NEW tests/test_autopilot_execution_v2.py. Runtime must accept optional clock callable for deterministic current-time validation. Add ExperimentConfig to ExecutorConfig: cohort_id='legacy', version='v1', confirmation_observations=1, maximum_round_trip_stop_fraction=None, take_profit_remaining_fraction=None. execution agent owns config hash + structured immutable entry payload. Runtime health exposes experiment metadata.
- ledger agent: database.py and NEW tests/test_autopilot_ledger_v2.py. create_position_transactional accepts optional starting_cash and entry_order dict; close_position accepts optional exit_order dict; both atomically save corresponding orders when passed. Use BEGIN IMMEDIATE, NY session boundaries, all losses, cash guard. Add session_counts(now,ticker) returning new_positions, stopped_trades, ticker_entries. Preserve APIs with optional parameters.
- research agent: NEW core/paper_executor/cohort_evaluation.py, scripts/audit_autopilot_performance.py and NEW tests/test_autopilot_cohort_evaluation.py. Expose evaluate_cohort(db_path, baseline_path=None) returning JSON-safe observed metrics, evidence coverage, promotion blockers; support legacy and structured entry evidence and spreads. Build recorded quote replay with explicit gaps, no synthetic paths. Freeze 60 trades/20 sessions, PF>1.1, positive Bonferroni-adjusted session-bootstrap lower bound (three comparisons) and existing strategy registry requirements. No live promotion claims.
- root: core/paper_executor/cohorts.py orchestration, service.py, dashboard/status/Discord additions, deployment configuration, documentation, integration tests and rollout. Cohorts use same incoming batches and timestamp-keyed market-data cache; independent SQLite ledgers, control tokens and endpoints. Baseline remains existing ledger with version tags; candidates fresh ledgers.

## Gates
- [x] Execution invariants and failure recovery tested.
- [x] Atomic accounting, cash/session limits and restart tested.
- [x] Evaluation/replay honestly distinguish legacy, missing data and prospective versions.
- [x] Four isolated cohorts consume common observations; strategies implement approved deltas.
- [x] API/dashboard/Discord expose cohort results and blockers.
- [x] Backups, migration, regression checks and deployed health verified.

Evidence: AUTOPILOT_ROLLOUT_GATES.md, 207 passing regression tests, production
four-cohort health and authenticated core API checks on September 10, 2026.
Discord formatting/deduplication is tested; actual candidate failure delivery
awaits a real incident. No artificial incident was sent. In-session forward
performance and strategy promotion remain unproven.

No historical performance or elapsed prospective sessions will be fabricated to satisfy promotion.
