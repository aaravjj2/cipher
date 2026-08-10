# Cipher Research Platform

This package implements the structural governance, provenance, research, and
prospective-validation services described in
`../../../docs/architecture/CIPHER_CURRENT_STATE_AND_HYBRID_ARCHITECTURE_THESIS.md`. Structural
implementation is not equivalent to operational completion: the strict current
assessment is recorded in `docs/original_architecture_self_audit.md` and
`data/governance/original_architecture_self_audit.json`.

It extends the existing Cipher terminal and forward-test runtime. It does not replace the current Alpaca/Tradier collectors, UI, scanners, historical option archives, Kronos watcher, or shadow paper executor.

## Hard boundary

The platform contains no broker client, order endpoint, live-trading state, or automatic capital deployment path.

The furthest strategy state is:

```text
LIVE_REVIEW_REQUIRED
```

That state means a human review is required. It does not mean live execution is enabled.

## Implemented layers

### Governance and identity

- Deterministic content IDs for raw objects, datasets, features, strategies, experiments, results, promotions, prospective observations, and audit events.
- Append-only audit events.
- Immutable content-addressed artifact storage.
- Explicit feature allowed-use levels: context, filter, ranking, sizing, execution.
- Ordered strategy promotion gates.

### Hybrid data plane

- Existing mutable SQLite databases remain operational stores.
- SQLite backup snapshots create transactionally consistent frozen research datasets.
- Final JSON/JSONL captures receive raw-object manifests.
- Large mutable files use explicitly labelled sampled fingerprints for cataloging.
- Canonical BigQuery DDL and JSONL load artifacts can be generated without automatically performing cloud writes.

### Research factory

- Standard result contract for trades, equity curves, metrics, benchmarks, regimes, statistical tests, exclusions, quality checks, and assumptions.
- Legacy JSON report adapter.
- Callable experiment adapter.
- Statistical and quality gate evaluator.
- Safe factor DSL restricted to raw OHLCV/VWAP columns and whitelisted time-series functions.

### Model and event features

- Kronos registered as context-only.
- TimesFM registered as blocked until project-specific weights and valid provenance are present.
- Optional local FinBERT adapter with overlapping chunking and normalized sentiment probabilities.
- News records store hashes and extracted features rather than duplicating raw article text.
- Anomaly attribution associates point-in-time events without asserting causality.

### Strategy graduation

```text
IDEA
→ SPECIFIED
→ DATA_VALIDATED
→ FAST_BACKTESTED
→ WALK_FORWARD_PASSED
→ LEAN_REPLICATED
→ PROSPECTIVE_SHADOW
→ PAPER_ELIGIBLE
→ LIVE_REVIEW_REQUIRED
```

Every promotion is evidence-gated. LEAN replication requires an audit containing point-in-time provenance, contract-selection records, observed fill evidence, corporate-action handling, survivorship controls, and reconciled cash, positions, orders, fees, and slippage.

### Prospective validation

- Immutable preregistration.
- First-observation-wins signal identity.
- Pending, scored, and rejected observations.
- Locked minimum sample and acceptance criteria.
- Manual locked-analysis state when automatic promotion is forbidden.
- Existing Cluster/Kronos prospective evidence can be imported without restarting its clock.

### Context and risk

- Context memo output explicitly forbids order, quantity, sizing, override, or broker fields.
- Deterministic candidate risk review.
- Simulation-only portfolio proposals using equal weight, inverse volatility, minimum variance, mean variance, or empirical CVaR.
- No order intents are generated.

### Evidence feedback

- Historical fast-test, LEAN, prospective, and paper evidence can be reconciled.
- Drift can open research issues or recommend pausing new shadow entries.
- Rule changes, allowed-use changes, sizing changes, promotions, and broker execution require human approval.

## Configuration

Default configuration:

```text
cipher-system/config/research-platform.json
```

Generated local state:

```text
cipher-system/data/governance/
cipher-system/data/raw_lake/
cipher-system/data/research_snapshots/
cipher-system/data/warehouse_exports/
```

These paths are ignored by Git.

Cloud writes are disabled by default:

```json
{
  "cloud_writes_enabled": false,
  "gcs_bucket": null
}
```

The implementation generates GCS/BigQuery transfer artifacts and command vectors, but it does not execute cloud writes unless a separately reviewed deployment layer is added.

## CLI

Run from the repository root:

```bash
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py init
```

Show current state:

```bash
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py status
```

Import current forward tests and authoritative evidence:

```bash
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py import-current-evidence
```

Print canonical BigQuery DDL:

```bash
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py ddl
```

Catalog an operational SQLite database without claiming it is immutable:

```bash
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py catalog-sqlite \
  cipher-system/data/gex_history.sqlite \
  --name gex_history \
  --source alpaca_opra \
  --row-counts
```

Freeze a point-in-time SQLite research snapshot:

```bash
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py freeze-sqlite \
  cipher-system/data/gex_history.sqlite \
  --name gex_history_research \
  --source alpaca_opra \
  --availability-cutoff 2026-08-01T20:00:00Z \
  --universe-id optionable_universe_v1 \
  --corporate-action-version corporate_actions_v1 \
  --normalizer-version gex_normalizer_v1 \
  --schema-name gex_sqlite_v1
```

Import a future research-grade LEAN audit:

```bash
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py import-lean-audit \
  /path/to/cipher_quantconnect_option_audit.json
```

A compile, smoke test, or zero-trade run is not a research-grade LEAN result and will be blocked.

## Active ingestion hooks

When the governance registry exists, these existing data paths automatically register append-closed files:

- `core/gex_capture.py`
- `core/tradier_stream_capture.py`
- `scripts/import_browser_gcs_payloads.py`

Hooks are nonfatal. Capture and import continue if governance is unavailable. Set the following only to disable manifest registration:

```bash
export CIPHER_GOVERNANCE_HOOKS=0
```

## Core API

The existing read-only core exposes:

```text
GET /api/governance
```

The route opens the registry with SQLite `mode=ro`. It cannot initialize or mutate governance state.

## Scheduled catalog

The VM infrastructure includes:

```text
cipher-governance-catalog.service
cipher-governance-catalog.timer
```

The timer runs after each weekday market session. It inventories source, catalogs current operational stores, imports current forward-test evidence, and updates manifests. It has no cloud-write or broker capability.

## JSON schemas

Machine-readable contracts are stored under:

```text
cipher-system/schemas/research/
```

Included schemas cover:

- Raw-object manifests
- Dataset manifests
- Feature specifications
- Strategy specifications
- Experiment manifests
- LEAN audit payloads

## Tests

Focused tests:

```bash
/home/aarav/.venvs/cipher/bin/python -m pytest -q \
  cipher-system/tests/test_research_platform_governance.py \
  cipher-system/tests/test_research_platform_experiments.py \
  cipher-system/tests/test_research_platform_data_plane.py \
  cipher-system/tests/test_research_platform_forward_risk_attribution.py \
  cipher-system/tests/test_research_platform_advanced_layers.py
```

The tests cover immutable identities, artifacts, point-in-time feature retrieval, dataset freezing, operational cataloging, warehouse exports, research gates, prospective validation, attribution, risk, factors, news, context memos, LEAN audits, and portfolio proposals.

## Current imported evidence

At initial implementation, the platform imports these existing conclusions without altering them:

- No validated low-capital options strategy.
- No live deployment authorization.
- QuantConnect authoritative rerun still required.
- Kronos remains context-only.
- TimesFM remains blocked pending valid project artifacts and leakage-safe evidence.
- Cluster/Kronos prospective minimum is not yet reached.

The registry is designed to preserve those conclusions until new evidence passes the declared gates.
