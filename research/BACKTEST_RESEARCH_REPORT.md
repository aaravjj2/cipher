# Cipher Underlying-Price Proxy Research — Corrected Status

> **Not an options backtest.** The v4/v5/v6 experiments used daily underlying
> OHLCV and target/stop proxies. They did not use historical option contracts,
> observed point-in-time bid/ask, premium cash flows, Greeks, assignment,
> exercise, OCC adjustments, or broker margin.

## Valid interpretation

These experiments are exploratory **underlying-signal tests**. Names such as
`put_write`, `covered_call`, and `vol_regime_selling` are legacy labels only;
their returns must not be interpreted as cash-secured-put, covered-call, or
option-premium returns.

Some proxy configurations produced positive simulated portfolio curves, but
trade-level statistics were weak, autocorrelated, and underpowered. Apparent
portfolio profitability alongside negative average trade returns is an
accounting warning that requires reconciliation, not evidence of nonlinear
option payoff capture.

## Options-research status

No credible options backtest has succeeded. Legacy Tradier and Alpaca-snapshot
results were invalidated and purged. The authoritative status is:

- `data/OPTIONS_RESEARCH_STATUS.json`
- `scripts/audit_option_dataset.py`
- `scripts/run_point_in_time_options_backtest.py`

A new options result is permitted only after observed historical bid/ask quotes
pass the strict point-in-time provenance gate.
