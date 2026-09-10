# Autopilot entry-quality improvement gates

- [x] G1 Baseline realized performance is measured from the deployed ledger.
  CHECK: `/home/aarav/.venvs/cipher/bin/python scripts/audit_autopilot_performance.py --db /home/aarav/Aarav/cipher/runtime/data/paper_runtime/data/paper_trades/autopilot_shadow.sqlite --json`
  EXPECT: `"trades": 25`
  EVIDENCE: 25 closed, 9 wins (36.0%), -$319.10; setup, exit, hour, spread, symbol, and repeat-entry slices emitted by the audit script.

- [x] G2 Root causes are tied to the actual entry and execution paths.
  EVIDENCE: contract_selector.py admitted arbitrarily distant cheap contracts after ATM max_cost rejection; observed same-day repeats were 1/6 winners and -$276.22.

- [x] G3 Entry-quality controls prevent identified low-quality/repeat trades without weakening risk limits.
  EVIDENCE: selector regression rejects far-OTM fallback; debit-spread regression uses ATM legs and caps net debit; transactional database test blocks session re-entry.

- [x] G4 Focused and regression tests pass.
  CHECK: `/home/aarav/.venvs/cipher/bin/python -m pytest -q tests/test_paper_executor*.py tests/test_premarket_autopilot.py tests/test_local_portfolio_backtest.py tests/test_autopilot_status.py tests/test_autopilot_notifications.py`
  EXPECT: `102 passed`
  EVIDENCE: 102 passed in 8.37s.

- [x] G5 Deployed simulated-paper worker is healthy and uses the improved configuration.
  CHECK: `curl -fsS http://127.0.0.1:8787/health | jq -r '[.ready,.mode,.execution.backend,.execution.external_order_capability] | map(tostring) | join(" ")'`
  EXPECT: `true paper simulated false`
  EVIDENCE: active service returned ready=true, mode=paper, backend=simulated, external_order_capability=false after restart.

- [x] G6 Promotion remains evidence-gated; no historical-fit result is represented as prospective performance.
  EVIDENCE: audit artifact sets out_of_sample_performance=false and live_trading_readiness=false; no setup was removed based only on the 25-trade sample.
