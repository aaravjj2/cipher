# Why 15 tests skip

The suite is at zero failures. What remains is 15 skips, and they are not 15
separate problems — almost all trace to **one unresolved data-acquisition
blocker**. This records which, so nobody re-derives it.

Reproduce:

```bash
../.venv-research-py312/bin/python -m pytest tests/ -q -rs   # 313 passed, 15 skipped
python3 -m pytest tests/ -q -rs                              # 311 passed, 17 skipped
```

The core interpreter skips two more because `vectorbt` and the qlib/rdagent
runtimes are research-engine extras. They are installed in
`.venv-research-py312`, exactly where `requirements-research-engines.txt` says
they belong — the core app imports only numpy and scipy. Running the suite there
is the fix; nothing is missing.

## The one blocker

Every strategy-research artifact is produced by
`scripts/run_strategy_research_loop.py`, and its first step is:

```
resolve_dataset_id() ->
RuntimeError: registered canonical Holdout C price-only dataset is unavailable
```

The research registry holds 8 datasets — `tradier_stream`, `gex_history`,
`historical_bars`, `flow_forward_test`, `paper_trades_legacy`, three
`cluster_kronos_forward` — and **none is a Holdout C price-only panel**. This is
the project's own recorded blocker: 11 of 12 required strict independent origins,
tracked in `scripts/report_holdout_c_acquisition_blocker.py`,
`record_holdout_c_data_block.py` and the `holdout_c_*` governance artifacts. It
needs a twelfth independent data origin, which is an acquisition problem, not a
code problem.

Blocked downstream of it:

| Artifact | Consumer |
|---|---|
| `cipher_signal_only/latest_signal_research.json` | signal-only boundary tests |
| `cipher_signal_only/latest_complete_observations.json` | date/expiry awareness |
| `cipher_signal_only/latest_ticker_strategy_specifics.json` | ticker rule boundaries |
| `cipher_signal_overlay_research.json` | prospective-only overlay |
| `recent_component_robustness.json` | concentration checks |
| `strategy_research/latest_auxiliary_research_status.json` | auxiliary safety boundaries |
| `strategy_research_2026_ytd/latest_2026_ytd_locked_validation.json` | YTD locked validation |
| `strategy_research_validation/latest_locked_broad_validation.json` | locked broad validation |
| `market_quality/alpaca_holdout_c_price_only_scope_*.json` | 744-partition answer key |
| `ds_796df562a29d2b01d2e1ca24` raw lineage | factor-rotation grid lineage |

## A hollow artifact is worse than a missing one

`scripts/build_cross_period_strategy_matrix.py` runs to completion without its
upstream validations and writes:

```json
{"status": "completed", "matrix": [], "summary": {"candidates": 0}}
```

A file that reports success while describing nothing. Building it turned three
honest skips into three failures, because the tests assert 14 frozen candidate
IDs that a rebuild cannot contain.

So `conftest.require_artifact()` takes a `non_empty_key` and checks content, not
just presence. Do not "fix" these skips by running the producer — that
manufactures an artifact which claims a completed cross-period study never ran.

## The two that are not about Holdout C

**`post_merge_verification`** requires the four `cipher-*` systemd units. Three of
its checks are `all_four_service_pids_changed`,
`all_four_service_cwds_resolve_to_canonical` and `services_active_after_restart`.
This host has no cipher systemd units — `docs/master_end_state_closeout.md`
records that the WSL box has no usable user-systemd bus — so the audit is
structurally FAILED here regardless of code correctness. It can pass on the
deployed VM (`infra/gcp-cipher-vm/systemd/`), and that is the only place it means
anything.

**`test_local_scheduler_records_blocked_jobs_without_execution`** asserts a
`full_volume_gate_reference_scope_unresolved` blocker that is only reachable once
the research runtimes are installed; without them every job stops earlier at
`qlib_or_rdagent_runtime_unavailable`.

## What is still asserted unconditionally

Every skip above keeps its safety assertions outside the skip. Whatever the
environment, the suite still proves: no execution authority, no automatic
promotion, no lean replication, no paper-or-live execution, and every scheduler
job in a recorded state. A skip that hid a safety regression would be worse than
the red tests this replaced.

## How to actually close them

1. Acquire a twelfth strict independent data origin and register the Holdout C
   price-only panel (`scripts/ingest_alpaca_holdout_c_panel.py`,
   `freeze_alpaca_holdout_c_panel.py`,
   `register_holdout_c_canonical_dataset.py`).
2. Run `scripts/run_strategy_research_loop.py --once`; the ten artifacts above
   follow from it.
3. Deploy to the VM for `post_merge_verification`.

Until step 1, these tests are correctly reported as unverified rather than
passing or failing.
