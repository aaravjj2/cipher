# Seven-Layer Guarded Research Stack

Cipher implements the attached autonomous quant architecture as a guarded research and backtesting stack. It does not authorize live order submission.

## Boundary

- Vendor/API access is confined to ingestion and existing read-only collectors.
- Forecasting, FinBERT-style sentiment, GDELT/event parsing, Kronos/TimesFM features, factor discovery, anomaly attribution, and autoresearch feedback are offline or scheduled batch layers.
- Strategy graduation remains capped at `LIVE_REVIEW_REQUIRED`.
- Layer 6 produces simulation-only portfolio proposals and audit records. It does not create broker orders.
- Layer 7 feedback routes back into the Layer 5 validation gate, not into live runtime execution.

## Implemented Tables

The canonical BigQuery DDL now includes the additional topology needed by the seven-layer plan:

- `feature_vectors`
- `factor_candidates`
- `anomaly_log`
- `backtest_gate_results`
- `portfolio_proposals`
- `execution_audit`
- `autoresearch_feedback`

Existing tables such as `market_bars`, `option_quotes`, `news_events`, `model_forecasts`, `experiment_metrics`, and `audit_events` remain part of the same canonical topology.

## New Code

- `core/research_platform/seven_layer_stack.py`
  Defines the seven guarded layers, validates boundary violations, creates offline orchestration plans, detects forecast interval anomalies, and builds autoresearch feedback packets.

- `scripts/describe_seven_layer_stack.py`
  Prints the stack plan and boundary status without touching GCP or starting a live process.

## Local-Only Status

This checkout is an independent local clone. It is not connected to the prior
VM, and the default configuration has `cloud_writes_enabled=false`, no GCS
bucket, and the placeholder BigQuery project `cipher-project-not-configured`.

- Phase 0 is deliberately **not passed** locally: provisioning and validating
  BigQuery, GCS, and Cloud Run/Functions requires a separately configured cloud
  project and credentials.
- Phases 1-3 are implemented as guarded local research infrastructure, but a
  dataset, forecast, factor, or strategy is not promoted until its required
  data-quality and out-of-sample evidence has been recorded.
- Phase 4 live execution is not implemented in Cipher. The promotion ceiling is
  `LIVE_REVIEW_REQUIRED`, which is a human-review state, not execution authority.

## Operator Commands

```bash
cd /home/aarav/Aarav/cipher
.venv/bin/python cipher-system/scripts/describe_seven_layer_stack.py
.venv/bin/python cipher-system/scripts/describe_seven_layer_stack.py --json
.venv/bin/python cipher-system/scripts/describe_seven_layer_stack.py --tables
.venv/bin/python cipher-system/scripts/describe_external_integrations.py
.venv/bin/python cipher-system/scripts/describe_local_capabilities.py
.venv/bin/python cipher-system/scripts/run_local_research_scheduler.py
.venv/bin/python cipher-system/scripts/run_timesfm_base_context.py SPY --lookback 128 --horizon 12
```

`run_local_research_scheduler.py` is a local, file-backed scheduler entrypoint.
It records whether each guarded job is ready or blocked in
`data/governance/local_research_scheduler.json`; it does not invoke vendors,
subprocesses, brokers, or orders. A system scheduler may call this command only
after the relevant runtime and evidence prerequisites have been met.

## Validation

The implementation is covered by `test_research_platform_advanced_layers.py` and `test_research_platform_data_plane.py`.

Expected checks:

```bash
cd /home/aarav/Aarav/cipher
.venv/bin/python -m compileall -q cipher-system/core cipher-system/scripts cipher-system/tests
/home/aarav/.nvm/versions/node/v22.23.1/bin/node --check cipher-system/app/server.mjs
/home/aarav/.nvm/versions/node/v22.23.1/bin/node --check cipher-system/app/launcher.mjs
/home/aarav/.nvm/versions/node/v22.23.1/bin/node --check cipher-system/app/public/app.js
cd cipher-system
PATH=/home/aarav/.nvm/versions/node/v22.23.1/bin:$PATH ../.venv/bin/python -m pytest -q -s
```
### Local model context comparison

The `run_model_context.py` helper runs the public TimesFM base model and the
local Kronos checkpoint against the same local OHLCV cutoff, then reports their
directional context. It is deliberately non-actionable: a directional agreement
is not a prediction claim, promotion record, or execution instruction.
The report also labels the bar cutoff as fresh, stale, or unknown; a stale local
dataset is never concealed behind a recent model inference timestamp.
Kronos sampling is seeded (`42` by default) and the seed is recorded so the
research context can be reproduced.

```bash
cd /home/aarav/Aarav/cipher
.venv/bin/python cipher-system/scripts/run_model_context.py SPY --lookback 128 --horizon 12
```
