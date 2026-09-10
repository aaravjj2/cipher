# Gates: Autopilot v2 rollout

Scope: Internal-paper execution and deployment. Statistical promotion is not claimed.

- [x] G1: Execution, accounting, recovery and evaluation regressions pass.
  CHECK: /home/aarav/.venvs/cipher/bin/python -m pytest -q tests/test_paper_executor*.py tests/test_autopilot*.py tests/test_alpaca_paper_runtime.py tests/test_exchange_calendar.py tests/test_paper_portfolio_api.py tests/test_premarket_autopilot.py tests/test_digest_market_session.py tests/test_alpaca_core_market_data.py tests/test_operator_status.py tests/test_product_status.py
  EXPECT: 207 passed
  EVIDENCE: ...............................................................          [100%] | 207 passed in 11.36s

- [x] G2: Frontend contracts pass and published build matches output.
  CHECK: node --test web/test/autopilot*.test.mjs && bash scripts/sync_web_build.sh --check
  EXPECT: In sync: app/public matches web/out.
  EVIDENCE: # duration_ms 80.074615 | In sync: app/public matches web/out.

- [x] G3: Four isolated simulated portfolios start and reconcile.
  EVIDENCE: 2026-09-10 03:34 UTC GET /health on ports 8787–8790: ready=true, reconciliation_passed=true, mode=paper, backend=simulated, external_order_capability=false. Distinct cohort/config hashes. /api/paper/cohorts returns four rows without errors, all no_prospective_trades.

- [x] G4: Historical records match the integrity-checked backup.
  EVIDENCE: Backup /home/aarav/Aarav/cipher/runtime/data/paper_runtime/autopilot-v2-20260909T191200Z-bbfbvgnv. Post-restart IDs, prices, quantities, timestamps, statuses and payloads match all 25 historical positions exactly; integrity_check=ok.

- [x] G5: Chain access and scheduled monitoring are available.
  EVIDENCE: 2026-09-10 03:35 UTC deployed market-data-probe SPY returned ok=true, 6 expirations, 960 contracts. This does not prove fresh executable off-hours quotes. Scanner, training, failure-alert and daily Discord timers scheduled. Failure-alert unit permits candidate notification-state writes under cohorts.
