# Gates: Autopilot, earnings recovery, and closed-market notifications

Scope: Fix reproduced failures without bypassing model validation or placing broker orders.

- [x] G1: Closed-market notification guards, dry-run safety, and local portfolio reporting are regression-tested.
  CHECK: /home/aarav/.venvs/cipher/bin/python -m pytest -q tests/test_digest_market_session.py tests/test_portfolio_daily_report.py
  EXPECT: passed
  EVIDENCE: .................                                                        [100%] | 17 passed in 0.87s

- [x] G2: Autopilot lifecycle, calendar gates, and internal simulation regression tests pass.
  CHECK: /home/aarav/.venvs/cipher/bin/python -m pytest -q tests/test_premarket_autopilot.py tests/test_autopilot_status.py tests/test_paper_executor_runtime.py tests/test_alpaca_paper_runtime.py tests/test_local_portfolio_backtest.py
  EXPECT: passed
  EVIDENCE: ...................................................                      [100%] | 51 passed in 5.80s

- [x] G3: Stored-report provider independence and earnings retry recovery pass checks; web builds.
  EVIDENCE: node --test app/test/*.test.mjs web/test/*.test.mjs: 155 passed, 0 failed; includes simulated provider outage and executed EarningsRadar hook recovery. Earnings endpoint/source/scanner/portfolio pytest: 32 passed. npm lint, typecheck, and production build exited 0.

- [x] G4: Changed services/frontend are published and read-only runtime verification is recorded, with live-session limitations explicit.
  EVIDENCE: 2026-09-08 00:45 UTC: core/web/executor active; snapshot phase closed, state MARKET_CLOSED, backend simulated, external_order_capability false. Earnings current, six cards, artifact 2026-09-07T12:16:50Z. Local static tree verified in sync. Vercel dpl_BTpJrXVf9ujUk7q5jzmZNAVhKf6g READY and production alias HTTP 200; same-origin auth/session responds authenticated false. No Discord test messages sent. Fresh-session provider-driven scan/fill execution and full option-P&L backtest remain unverified; replay checks cover underlying paths, not historical options fills. An unrelated pre-existing trailing blank line remains in tests/test_product_status.py; touched tracked files pass diff --check.
