# Cipher: Current-State Architecture Thesis and Hybrid Evolution Blueprint

**Prepared:** August 1, 2026  
**Status:** Architecture and research design only; no implementation authorized by this document  
**Scope:** `/home/aarav/Aarav/cipher` and `/home/aarav/Aarav/cipher-system/CipherCapture`

---

## Executive thesis

Cipher is not presently an autonomous quantitative trading stack. It is a substantial, evidence-conscious **options intelligence, data-capture, research, backtesting, and prospective simulation platform** whose strongest capabilities are already operational:

- Live and historical options-market data acquisition from Alpaca and Tradier.
- A local research terminal exposing Strike Matrix, Night Vision, Spyglass, scanners, watchlists, journal, and charting surfaces.
- Persistent GEX, option-flow, stock-bar, browser-scan, and option-history datasets.
- Numerous strategy laboratories with increasingly strict point-in-time and holdout controls.
- Kronos integration as a preregistered, context-only forecasting feature.
- TimesFM runtime integration with deliberate refusal to use an unproven or provenance-free project checkpoint.
- An isolated shadow/paper executor with conservative fill assumptions, contract filters, restart reconciliation, position limits, a kill switch, and no broker-order pathway.
- Always-on Google Cloud VM services, Secret Manager integration, GCS durability, systemd supervision, and private administration.
- Prospective Cluster and Cluster-plus-Kronos forward tests that record predictions before outcomes.

The platform's main weakness is not a shortage of models, strategies, or data. Its main weakness is **architectural fragmentation**. Data is spread across many SQLite databases, raw JSON trees, runtime folders, GCS objects, and external QuantConnect artifacts. Research scripts use different assumptions and output contracts. Documentation still describes an earlier read-only product boundary while a separate shadow execution and forward-test estate now exists. Version control and immutable artifact identity are not yet strong enough for safe autonomous research.

The correct next architecture is therefore not a wholesale replacement with an AI-native seven-layer stack. The best design is a **hybrid architecture**:

1. Preserve Cipher's working local/VM services, raw datasets, conservative simulation, and validation culture.
2. Add canonical data contracts, immutable raw storage, dataset manifests, experiment identity, strategy registries, and explicit promotion states.
3. Use BigQuery selectively as a research warehouse rather than as the sole real-time operational database.
4. Retain SQLite where it is operationally efficient, while separating mutable operational state from append-only audit history.
5. Standardize the existing fast Python laboratories as the first research gate and use LEAN as the authoritative event-driven replication gate.
6. Keep Kronos, TimesFM, news models, and future LLM agents as measured features or context providers until each earns a promotion through prospective evidence.
7. Keep deterministic strategy rules and risk controls in charge of position eligibility; do not let an LLM originate or override trades.
8. Delay autonomous factor generation and live broker execution until the data, registry, validation, and audit layers are reproducible.

The target is an **evidence-first autonomous research platform**, not an autonomous trading agent. Execution, if ever enabled, should be the final and smallest component of the system.

---

# Part I — What Cipher has actually built

## 1. System identity and operating boundaries

The repository defines Cipher as a private, personal-use, clean-room options research terminal. The active product remains `cipher-system/`. The principal guardrails are:

- No proprietary AccessObsidian, APEX, Hermes, Fincept, or commercial Cipher internals are copied.
- Credentials remain server-side.
- Missing gamma or open interest remains unknown rather than being silently converted to zero.
- GEX is described as a public-open-interest heuristic, not verified dealer positioning.
- The active terminal exposes research and analytics rather than broker-order functionality.

The current estate now contains two related but distinct operational domains:

### Domain A — Active research terminal

The active read-only application consists of:

```text
Browser UI
  cipher-system/app/public
        |
        v
Node same-origin proxy
  cipher-system/app/server.mjs
        |
        v
Python research API
  cipher-system/core/app.py
        |
        +--> Alpaca OPRA options snapshots and contracts
        +--> Alpaca SIP/IEX stock data
        +--> local SQLite/JSON research data
```

The user-facing surfaces include:

- Strike Matrix
- Night Vision
- Spyglass
- Setup Scanner
- Flash, Cluster, and Liq-style scoring
- Watchlists
- Journal
- Chart saves
- Ranking and weight laboratories

### Domain B — Shadow research and forward-test runtime

A separate runtime exists under:

```text
/home/aarav/Aarav/cipher-system/CipherCapture
```

This domain contains:

- Shadow paper-executor configuration.
- Prospective Cluster forward tests.
- Preregistered Cluster-plus-Kronos forward tests.
- Runtime locks, state files, logs, and SQLite output.
- Browser-capture ingestion from Windows/AccessObsidian payloads.
- Virtual debit-spread entries and exit-profile comparisons.

This runtime does not submit broker orders. It is a simulation and evidence-generation environment.

### Architectural implication

The system is no longer accurately described by a single phrase such as “read-only terminal.” It is better described as:

> A read-only market-intelligence terminal connected to a separate shadow execution and prospective validation estate, with no live-order capability.

That distinction should become formal architecture rather than remaining an accidental repository condition.

---

## 2. Layer A — Data acquisition and durable capture

Cipher has built a meaningful market-data acquisition estate across multiple vendors and collection methods.

### 2.1 Alpaca data plane

Alpaca is the primary source for the active terminal:

- OPRA option-chain snapshots.
- Option metadata and current open interest.
- Historical option bars and trades.
- SIP-preferred stock quotes and bars, with IEX fallback.
- Current option greeks and implied volatility where available.

The active application joins current option snapshots with current contract metadata to produce exposure surfaces. It correctly acknowledges that historical option bars alone cannot reconstruct historical GEX without point-in-time greeks and open interest.

### 2.2 Tradier streaming data plane

Tradier is used for persistent underlying and option market-data streaming, paper-executor quotes, and prospective observation.

The retained stream database currently contains:

| Metric | Current inventory |
|---|---:|
| Tradier stream events | 30,428,168 |
| Stream runs | 2,620 |
| Latest-quote records | 692 |
| Underlyings represented in the latest selection | 14 |

The collector records:

- Capture timestamp.
- Provider timestamp.
- Event type.
- Symbol.
- Bid, ask, last, price, and size when present.
- Asset class.
- Underlying.
- Option expiration, type, and strike.
- Raw JSON.

This is one of the strongest existing foundations because it preserves the raw event stream rather than only retaining derived signals.

### 2.3 GEX capture data plane

The GEX system stores raw snapshots and normalized strike cells using the established convention:

```python
call_gex =  call_gamma * call_oi * 100 * spot**2 * 0.01
put_gex  = -put_gamma  * put_oi  * 100 * spot**2 * 0.01
net_gex  = call_gex + put_gex
```

Current retained inventory:

| Metric | Current inventory |
|---|---:|
| GEX snapshots | 19,686 |
| GEX strike cells | 192,418 |
| Distinct tickers | 543 |
| Retained capture period | July 22–31, 2026 |

The normalized cells contain:

- Expiration and strike.
- Call, put, and net GEX.
- Call, put, and net VEX.
- Call and put open interest.
- Volume.
- Call and put midpoints.
- Listed/available flags.

The design correctly distinguishes listed cells from calculable cells and does not manufacture GEX when gamma or OI is missing.

### 2.4 Historical bar estate

The central historical-bar database currently contains:

| Metric | Current inventory |
|---|---:|
| Historical bars | 72,377 |
| Symbols | 27 |
| Coverage | July 24, 2025–July 23, 2026 |

Other historical-equity databases exist for focused studies, including index and leveraged-ETF research.

### 2.5 Historical option archive estate

Cipher retains 35 historical-options SQLite archives totaling approximately **9.26 GB**. These include:

- AMZN, GOOGL, NVDA, and META monthly and weekly archives.
- EOD index archives.
- Leveraged ETF wheel studies.
- Earnings-focused archives.
- Targeted calls and puts.

The strongest audited low-capital options study used:

- Eight selected archives.
- 2,544 point-in-time selected contracts.
- 4,782,842 option minute bars.
- 9,146 daily underlying bars.
- SHA-256 hashes recorded in the authoritative report.
- SQLite integrity checks.
- Verification that every selected contract existed on its decision date.

The key limitation remains that local historical option NBBO quotes and quote sizes are unavailable. The current evidence is therefore a conservative trade-bar approximation, not final execution-quality proof.

### 2.6 Browser and scanner capture

Cipher captures externally displayed scan cards through browser-side scripts and imports them into a VM-accessible folder structure. The ingestion system includes:

- Raw, ready, uploaded, failed, and log states.
- Duplicate handling.
- GCS import ledger.
- Market-hours workflows.
- Flash, Flash Agentic, and Cluster payloads.

This capture path is valuable because it allows point-in-time prospective observation of externally generated scanner cards without reconstructing proprietary internals.

### 2.7 Storage topology today

The effective storage topology is:

```text
External market APIs and browser captures
        |
        +--> raw JSON / JSONL folders
        +--> many purpose-specific SQLite databases
        +--> GCS browser-transfer and backup objects
        +--> report JSON/CSV/Markdown artifacts
        +--> external QuantConnect object storage
```

This topology is operationally successful but analytically fragmented. It lacks one universal data contract and one central catalog of provenance.

---

## 3. Layer B — Market intelligence and signal generation

Cipher's primary product value currently comes from deterministic options-market interpretation rather than predictive AI.

### 3.1 Exposure surfaces

Strike Matrix and Night Vision transform current option-chain information into:

- Strike-by-expiration exposure matrices.
- Call, put, and net GEX/VEX.
- Walls, peaks, support, resistance, and zero-crossing heuristics.
- Stock-price context.
- Compact visual overlays.

The implementation preserves missing data and formula caveats, which is crucial for research integrity.

### 3.2 Flow interpretation

Spyglass and related modules infer option-flow characteristics using:

- Latest prints.
- Bid/ask relationship.
- Premium size.
- Volume and open interest.
- Gamma-weighted flow approximations.
- Side and direction classification.

Cipher distinguishes historical flow-cluster testing from historical GEX reconstruction. That conceptual separation is correct and should remain.

### 3.3 Scanner and ranking system

The current scanner and research modules include:

- `scanner.py`
- `signal_aggregator.py`
- `ranking_lab.py`
- `weight_lab.py`
- `cluster_confidence.py`
- `cluster_decay.py`
- `dynamic_strike_zones.py`
- `flow_imbalance.py`
- `smart_money_divergence.py`
- `pre_entry_factor_scorer.py`
- `research_feature_fusion.py`
- `setup_research_engine.py`

These components constitute a manually designed factor and ranking ecosystem. They are not an autonomous factor-discovery framework, but they already represent the practical precursor to one.

### 3.4 Company and event context

`company_research_engine.py` adds:

- Current market context.
- Recent weekly movement.
- Public headlines through Yahoo Finance RSS.
- Basic direction-alignment notes.

The current system does not implement FinBERT, SEC-document chunking, GDELT event parsing, causal extraction, or document embeddings. News remains lightweight contextual enrichment.

---

## 4. Layer C — Forecasting and model features

## 4.1 Kronos

Kronos is installed from the external open-source repository and is integrated through:

- OHLCV export and validation.
- Model readiness checks.
- Forecast generation.
- Option-price research adapters.
- Preregistered Cluster-plus-Kronos prospective tests.

The most important finding is not that Kronos runs; it is that Cipher correctly refuses to over-promote it.

The July 30 review concluded:

- Kronos failed as an entry gate or sizing input.
- The small and mini checkpoints produced unstable filter effects.
- The historical sample was too biased because all 14 valid candidates were winners before filtering.
- Kronos should remain context-only.

The later prospective preregistered test currently contains:

| Group | N | Cluster win rate | Average directional return |
|---|---:|---:|---:|
| Kronos agreed | 23 | 47.8% | +0.2020% |
| Kronos disagreed | 22 | 36.4% | -0.0101% |

Only 45 of the required 100 observations have been scored. The rule is locked: Kronos cannot gate entries, rank exits, or change sizing until the minimum sample is reached and a preregistered analysis supports stable benefit.

This is a strong example of the system's desired future governance.

## 4.2 TimesFM

Cipher has built a careful TimesFM bridge with:

- A strict model manifest contract.
- Training-cutoff validation.
- Point-in-time validation fields.
- Allowed-use restrictions.
- Runtime availability checks.
- A TimesFM 2.5 adapter.

The public TimesFM package can perform CPU inference. However, the Cipher-specific GEX checkpoint and manifest are missing:

```text
data/timesfm_model/timesfm_gex_finetuned.pt
data/timesfm_model/manifest.json
```

The system therefore marks TimesFM unavailable for strict GEX research. This is the correct behavior. Model availability is not equivalent to model validity.

## 4.3 What is not currently built

The model layer does not presently include:

- FinBERT sentiment inference.
- GDELT Global Knowledge Graph ingestion.
- SEC filing chunking and aggregation.
- News embeddings.
- RD-Agent(Q).
- Co-STEER factor implementation.
- Contextual Thompson sampling.
- A factor registry.
- Automated prompt evolution.
- A production LLM agent panel.

These should be treated as possible future components, not as current architecture.

---

## 5. Layer D — Strategy research and backtesting

Cipher has a broad research surface consisting of 81 top-level Python modules in `cipher-system/core/` and 31 active test files in `cipher-system/tests/`.

### 5.1 Existing research families

The research estate includes:

- Underlying-price strategy labs.
- Intraday and daily backtests.
- Cluster and flow-cluster backtests.
- GEX momentum, interpolation, replay, and forecast research.
- Option contract planning.
- Historical option downloads.
- Option portfolio and structure simulation.
- Earnings strategy laboratories.
- EOD option-pattern and walk-forward research.
- Leveraged ETF CSP/wheel studies.
- Watchlist alert and exit studies.
- Cross-stock and cross-ticker validation.
- Regime-conditioned analysis.
- Synthetic stress testing.
- QuantConnect/LEAN experiments.

### 5.2 Increasingly strict evidence standards

The strongest recent options audit explicitly reports:

```text
NO_VALIDATED_LOW_CAPITAL_OPTIONS_STRATEGY
```

Across 194 fixed strategy variants and 776 account-size rankings, no candidate was promoted. Positive headline P/L was rejected where accompanied by:

- Sparse samples.
- Excessive account risk.
- Large drawdowns.
- Negative 2026 holdout performance.
- Best-trade concentration.
- Multiple-testing failure.

The validation methods include:

- Discovery, validation, and holdout periods.
- Point-in-time contract existence.
- Corporate-action checks.
- Liquidity sensitivity.
- Best-trade exclusion.
- Holm-Bonferroni correction.
- Archive hashes.
- SQLite integrity checks.

This rejection discipline is one of Cipher's most important assets. It should become a formal platform service rather than remaining embedded separately in individual studies.

### 5.3 Custom walk-forward infrastructure

`walk_forward.py` provides:

- Sequential train/test folds.
- Regime classification.
- Conditional summaries.
- Approximate significance tests.
- Option P/L approximation.

More recent option laboratories implement stronger validation than this earlier generic module, but the common idea is established.

### 5.4 QuantConnect/LEAN status

Cipher has an external QuantConnect Cloud project:

```text
quantconnect/cipher_conditional_put_write
```

The project compiled under LEAN 2.5, and a local one-day observed-quote smoke test passed with:

- 390 quote observations.
- Two order events.
- One entry and one close.

However, the authoritative cloud backtest remains incomplete:

- The previous web run generated zero trades.
- A current-chain timing fix was applied.
- The corrected project still requires a web-IDE rerun.
- Research claims remain disallowed.
- The LEAN project source and full reproducible environment are not established as a first-class local repository component.

Therefore LEAN is presently an external validation experiment, not yet Cipher's formal graduation gate.

### 5.5 Missing research-platform services

Cipher does not yet have one universal contract for:

- Strategy definitions.
- Datasets and data cutoffs.
- Fill models.
- Parameters.
- Feature versions.
- Experiment runs.
- Statistical corrections.
- Promotion decisions.
- Deployment artifacts.

The research scripts are individually capable but institutionally fragmented.

---

## 6. Layer E — Prospective forward testing and shadow execution

## 6.1 Cluster forward-test engine

The Cluster forward test consumes captured scanner files, constructs virtual debit spreads, and evaluates several locked exit profiles.

Current state as of August 1, 2026:

- 38 processed capture files.
- 129 closed virtual positions.
- 196 open virtual positions.
- Kronos context available for 325 profile-position observations.
- Setup research available for 55.
- Company-news context available for 20.

Current profile summaries:

| Profile | Closed | Open | Win rate | Total P/L | Profit factor |
|---|---:|---:|---:|---:|---:|
| Patient 120m, TP 40, SL 25 | 34 | 31 | 44.12% | 328.50 | 1.69 |
| Longer 180m, TP 50, SL 35 | 30 | 35 | 43.33% | 239.25 | 1.53 |
| Runner 240m, TP 60, SL 50 | 22 | 43 | 50.00% | 269.50 | 1.95 |
| Wide 180m, TP 40, SL 50 | 29 | 36 | 48.28% | 324.50 | 1.78 |
| EOD runner, TP 70, SL 50 | 14 | 51 | 50.00% | 772.50 | 5.61 |

These figures are preliminary because many positions remain open, samples differ by exit profile, and the largest-performing profile has only 14 closed observations.

## 6.2 Paper executor

`core/paper_executor/` implements an isolated simulation service with:

- Signal ingestion and validation.
- Episode deduplication.
- Contract candidate selection.
- Long-option or debit-spread simulation.
- Entry at ask and exit at bid by default.
- Additional simulated slippage.
- Quote freshness checks.
- Maximum spread percentage.
- Minimum bid, volume, and open interest.
- DTE and contract-cost limits.
- Position and daily-entry limits.
- Daily stopped-trade limits.
- Underlying target and invalidation exits.
- Option TP/SL and maximum-hold exits.
- Forced close time.
- Restart reconciliation.
- Worker health and queue observability.
- VM forwarding.
- A filesystem kill switch.

The service binds only to `127.0.0.1`, forbids wildcard CORS, starts in shadow mode, and rejects configurations that declare live order code.

### Important boundary

The executor simulates orders and records virtual positions. It does not call broker account, preview, or order endpoints. This boundary must remain explicit.

---

## 7. Layer F — Cloud operations and durability

Cipher is deployed to a private always-on GCP VM named `cipher-main` with:

- Google IAP SSH access.
- Tailscale-based private service exposure.
- Secret Manager-backed credentials.
- systemd-supervised services.
- GCS backups.
- Browser-folder synchronization.
- VM-local runtime state.

Managed services include or have included:

- Cipher core API.
- Cipher web app.
- DevSpace.
- Tradier stream collector.
- GEX capture.
- Browser import and folder synchronization.
- Cluster forward test.
- Cluster-plus-Kronos forward test.
- Backup timers.

The migration philosophy has been pragmatic: do not add BigQuery or Pub/Sub merely for architectural appearance. Add cloud services when an implemented pipeline needs them.

This pragmatic constraint should remain part of the hybrid design.

---

## 8. Layer G — Audit, feedback, and governance

Cipher currently implements governance through a mixture of:

- Markdown research reports.
- JSON status manifests.
- Preregistered configuration files.
- Dataset hashes.
- Strict blocker messages.
- Test suites.
- Runtime logs.
- SQLite event tables.
- Human review and explicit promotion decisions.

The system has not yet built a unified autonomous feedback loop. There is no service that automatically:

- Attributes live-vs-backtest decay.
- Updates a factor-search posterior.
- Rewrites prompts.
- Generates a new experiment.
- Registers it.
- Runs all validation stages.
- Promotes or rejects it.

This absence is appropriate at the current maturity level.

---

# Part II — Architectural diagnosis

## 9. What Cipher has done especially well

### 9.1 It preserves evidence instead of only preserving conclusions

Raw stream events, raw snapshots, raw browser payloads, archive hashes, manifests, and status artifacts are retained. This allows later reprocessing and audit.

### 9.2 It exposes uncertainty and unavailable data

The code does not silently replace missing GEX inputs with zero. TimesFM refuses to claim readiness without a model manifest. Historical options studies distinguish trade-bar approximation from NBBO evidence.

### 9.3 It is willing to reject attractive results

The system has rejected positive-P/L strategies because of drawdown, concentration, holdout failure, and multiple testing. This is stronger than maximizing a backtest leaderboard.

### 9.4 It has a useful operational footprint

A small private VM, systemd, SQLite, GCS, Secret Manager, IAP, and Tailscale are sufficient for the current workload. The architecture is understandable and inexpensive relative to a prematurely distributed platform.

### 9.5 It has separated live orders from research

Even the paper executor contains no broker-order client. This prevents accidental migration from simulation into capital deployment.

### 9.6 It has started true prospective validation

Predictions and signal captures are being recorded before outcomes. This is the only reliable path for evaluating features such as Kronos when historical data is incomplete or selection-biased.

---

## 10. Current structural weaknesses

### 10.1 No canonical source-of-truth contract

The system has many useful stores but no universal record identity. A robust point-in-time record should consistently include fields such as:

```text
record_id
source
source_symbol
canonical_symbol
event_time
provider_time
received_at
available_at
ingestion_run_id
schema_version
revision
raw_object_uri
raw_sha256
normalizer_version
corporate_action_version
```

Without `available_at`, a record can be chronologically correct yet still leak information that was not actually available when a decision was made.

### 10.2 Data access is coupled to vendor APIs

Many upper-level modules call external APIs directly. Research, UI serving, capture, and simulation can therefore observe different data and failure conditions.

The intended boundary should be:

```text
Vendor adapters --> normalized internal contracts --> all consumers
```

The system does not yet enforce that universally.

### 10.3 Backtests do not share one execution contract

Different laboratories use different:

- Entry clocks.
- Exit clocks.
- Quote fallbacks.
- Slippage assumptions.
- Liquidity filters.
- Position sizing.
- Corporate-action logic.
- Output metrics.
- Statistical tests.

These differences are sometimes appropriate, but they are not centrally declared and comparable.

### 10.4 Mutable state and audit history are mixed

SQLite is not the problem. The problem is that operational “latest state” and historical audit events are not consistently separated.

Examples of mutable operational data include:

- Latest quotes.
- Position status updates.
- Daily account state.
- Lock and PID files.
- Latest report JSON.

These are useful. However, an authoritative audit trail should record every transition as an append-only event rather than relying on the final row state.

### 10.5 Documentation boundaries are stale

Repository instructions still state that no paper-trading launcher or scheduled executor exists, while an isolated paper executor and multiple forward-test services are now present.

The system needs three formal labels:

- **Research terminal:** read-only analytics.
- **Shadow simulator:** simulated positions and fills.
- **Broker execution:** absent and prohibited unless separately authorized.

### 10.6 Version control is not ready for autonomous research

The repository reports no commits on `main`, and the active estate is untracked. Autonomous factor discovery, model promotion, or prompt evolution would be unauditable without immutable code identities.

Every future experiment needs at minimum:

- Git commit or content hash.
- Dataset snapshot ID.
- Strategy specification hash.
- Feature-set hash.
- Environment lock hash.
- Result artifact hash.

### 10.7 Model research is ahead of data maturity

Kronos and TimesFM are technically integrated, but the evidence is not sufficient to use them as decision gates. Adding more models before improving the data and experiment registry would increase complexity faster than confidence.

### 10.8 LEAN is not yet a reproducible platform gate

QuantConnect has been tested, but the authoritative rerun remains outstanding and the complete project is not a locally versioned, automatic stage of Cipher's research lifecycle.

---

# Part III — The best-of-both-worlds target architecture

## 11. Design principle

The hybrid architecture combines:

### From the existing Cipher system

- Lightweight private VM operations.
- SQLite efficiency for operational state.
- Raw JSON/JSONL retention.
- GCS backups and object durability.
- Alpaca and Tradier adapters.
- Existing UI and scanner workflows.
- Conservative simulation.
- Explicit missing-data behavior.
- Prospective validation.
- Strict research rejection standards.

### From the proposed AI-native architecture

- Layer separation.
- Canonical warehouse contracts.
- Offline feature generation.
- Standard strategy graduation.
- Event-driven replication.
- Causal/anomaly attribution.
- Portfolio-level risk optimization.
- Controlled feedback loops.
- Autonomous research only behind hard gates.

The resulting architecture should contain eight layers plus a governance plane.

---

## 12. Governance plane — identity, provenance, and promotion

Governance must sit across every layer rather than being treated as a final report.

### Required registries

#### Dataset registry

Each dataset snapshot should declare:

```yaml
dataset_id: ds_<hash>
created_at: <UTC timestamp>
sources:
  - alpaca_opra
  - tradier_stream
availability_cutoff: <UTC timestamp>
symbol_universe_id: universe_<hash>
corporate_action_version: ca_<hash>
raw_object_manifest: <URI>
normalizer_version: <git/content hash>
schema_version: 1
row_counts: {}
quality_checks: {}
```

#### Feature registry

Each feature should declare:

- Feature name and version.
- Inputs.
- Lookback.
- Availability lag.
- Missing-value policy.
- Training cutoff if model-based.
- Allowed use: context, filter, ranking, sizing, or execution.
- Leakage checks.

#### Strategy registry

Each strategy should declare:

- Signal rule.
- Instrument rule.
- Contract-selection rule.
- Entry timing.
- Exit rule.
- Position sizing.
- Portfolio constraints.
- Required features.
- Fill model.
- Benchmark.
- Statistical plan.
- Promotion thresholds.

#### Experiment registry

Every run should bind:

```text
code_hash
dataset_id
feature_set_id
strategy_id
parameter_set_id
engine_id
runtime_environment_id
random_seed
started_at
completed_at
result_artifact_hash
verdict
```

#### Promotion registry

Promotion states should be explicit:

```text
IDEA
SPECIFIED
DATA_VALIDATED
FAST_BACKTESTED
WALK_FORWARD_PASSED
LEAN_REPLICATED
PROSPECTIVE_SHADOW
PAPER_ELIGIBLE
LIVE_REVIEW_REQUIRED
REJECTED
RETIRED
```

No component should infer promotion from a high Sharpe ratio alone.

---

## 13. Layer 1 — Hybrid data foundation

The best design is not “BigQuery for everything.” It is a three-tier data plane.

### Tier 1A — Immutable raw object lake in GCS

All external payloads should first land in immutable, date-partitioned GCS objects:

```text
gs://<bucket>/raw/<source>/<dataset>/<YYYY>/<MM>/<DD>/<run_id>/...
```

Examples:

- Tradier raw stream JSONL.
- Alpaca option snapshots.
- Option contract metadata.
- Historical option pages.
- GEX raw payloads.
- Browser scanner captures.
- Public news and SEC documents.

Objects should have:

- SHA-256 checksums.
- Ingestion manifests.
- Source request metadata.
- Received timestamps.
- Retention rules.
- No destructive overwrite.

The existing raw folders and backup scripts can be evolved into this layer rather than discarded.

### Tier 1B — Operational state in SQLite initially, Postgres only when needed

SQLite should remain for:

- Latest quotes.
- Worker checkpoints.
- Queue state.
- Locks and reconciliation.
- Local UI caches.
- Shadow positions.
- Small forward-test ledgers.

Operational tables should be explicitly classified as mutable. Every material transition should also emit an immutable audit event.

Postgres should be added only if concurrency, transactional coordination, or service boundaries exceed SQLite's practical limits.

### Tier 1C — Analytical warehouse in BigQuery

BigQuery should be introduced for normalized, research-oriented tables when the queries justify it. Appropriate tables include:

- `market_bars`
- `option_quotes`
- `option_trades`
- `option_contract_reference`
- `gex_snapshots`
- `gex_strike_cells`
- `scanner_signals`
- `news_events`
- `model_forecasts`
- `forward_outcomes`
- `experiment_metrics`
- `audit_events`

Tables should be:

- Partitioned by event or availability date.
- Clustered by symbol and other frequent filters.
- Loaded idempotently from GCS manifests.
- Point-in-time queryable using `available_at`.

BigQuery becomes the analytical source of truth, but it should not be the sole low-latency quote cache or order-risk store.

### Data-access rule

Upper research layers should consume normalized internal repositories rather than directly calling vendors. Vendor access should be isolated in adapters and ingestion jobs.

---

## 14. Layer 2 — Feature and forecasting services

This layer should run offline or on scheduled windows and write versioned features back to the warehouse.

### 14.1 Deterministic feature service

The first-class feature service should consolidate existing features:

- GEX/VEX levels.
- Flow clusters.
- OI/volume relationships.
- Momentum and mean-reversion features.
- Volatility regimes.
- Cross-ticker context.
- Scanner strength and rank.
- Earnings proximity.
- Liquidity and spread quality.
- Time-of-day and day-of-week features.

Each feature must have point-in-time availability rules.

### 14.2 Kronos service

Kronos should remain:

- A versioned forecast feature.
- Preregistered by model, context length, horizon, and seed.
- Context-only until prospective promotion criteria are met.
- Stored as a forecast distribution or clearly identified point forecast.

It should never be called ad hoc inside live trade logic without a registered artifact.

### 14.3 TimesFM service

TimesFM should remain blocked until one of two valid paths occurs:

1. Recover the genuine Cipher-specific weights and manifest; or
2. Retrain with a declared dataset, cutoff, walk-forward plan, and model artifact hash.

Its first allowed use should be context-only forecasting of GEX or selected feature trajectories.

### 14.4 News and filing service

FinBERT can be added as a low-cost classifier, but only after the ingestion and availability contracts are established.

A reasonable pipeline is:

```text
Raw headline/document
  --> exact publication and received timestamps
  --> ticker/entity mapping
  --> chunking for long documents
  --> FinBERT probabilities
  --> event taxonomy
  --> optional heavier LLM extraction
  --> versioned event feature
```

The heavier LLM should be used for structured extraction and explanation, not as the sole sentiment score.

---

## 15. Layer 3 — Controlled research factory

The existing custom laboratories should become a standardized research factory before RD-Agent is introduced.

### 15.1 Phase-one research factory: human-specified, machine-executed

Researchers or coding agents provide a formal hypothesis specification. The system then:

1. Validates required datasets.
2. Materializes a point-in-time dataset snapshot.
3. Runs parameter sweeps.
4. Applies multiple-testing controls.
5. Performs walk-forward validation.
6. Runs robustness and exclusion tests.
7. Registers the result.

This captures most of the benefit of automated research without uncontrolled hypothesis generation.

### 15.2 Fast screening engine

Cipher can initially standardize its existing NumPy/SciPy/Python engines instead of immediately requiring VectorBT PRO.

A common adapter should return:

- Trades.
- Daily equity curve.
- Exposure.
- Turnover.
- Drawdown path.
- Benchmark comparison.
- Per-regime metrics.
- Fill-model details.
- Data-quality exclusions.
- Statistical tests.

VectorBT or another vectorized engine can later replace or complement this layer if performance becomes the bottleneck.

### 15.3 Future RD-Agent role

RD-Agent should only be considered after:

- Dataset and feature registries are operational.
- The experiment runner is reproducible.
- Strategy grammars restrict allowed operations.
- Compute and experiment budgets are enforced.
- Leakage tests are automated.
- Failed experiments are retained.
- Generated code is sandboxed.
- Human approval remains required before LEAN promotion.

The autonomous agent should generate hypotheses and code candidates, not deploy strategies.

---

## 16. Layer 4 — Attribution and anomaly analysis

A causal claim should not be inferred merely because a headline occurred near a price move. The initial layer should therefore be called an **attribution and anomaly engine**, not a causal engine.

### Inputs

- Forecast distributions.
- Realized returns and volatility.
- GEX/flow changes.
- Scanner signals.
- News and filing events.
- Market and sector factors.

### Outputs

- Forecast residual.
- Confidence-band breach.
- Market-adjusted and sector-adjusted residual.
- Candidate associated events.
- Event timing confidence.
- Whether the observation is suitable for model evaluation.
- Human-readable explanation with explicit uncertainty.

### Uses

- Split strategy results into ordinary and anomaly regimes.
- Identify event-sensitive strategies.
- Explain live-vs-backtest divergence.
- Supply structured context to research agents.
- Prevent single-event outliers from dominating promotion metrics.

The engine should avoid claiming causality unless a stronger research design supports it.

---

## 17. Layer 5 — Two-stage strategy graduation

This is the most important addition from the proposed architecture.

### Gate 1 — Fast standardized research validation

Required checks should include:

- Point-in-time dataset validation.
- Corporate actions.
- Survivorship-aware universe.
- Discovery/validation/holdout split.
- Walk-forward folds.
- Parameter stability.
- Slippage and fee sensitivity.
- Liquidity sensitivity.
- Best-trade and best-period exclusion.
- Multiple-testing correction.
- Benchmark comparison.
- Regime analysis.
- Drawdown and losing-streak simulation.
- Capacity and concentration.

### Gate 2 — LEAN event-driven replication

A candidate that passes Gate 1 should be rebuilt in a version-controlled LEAN project using:

- The same strategy specification.
- The same feature availability times.
- Realistic option-chain selection.
- Brokerage and margin models.
- Assignment and exercise behavior where applicable.
- Atomic multileg handling where supported.
- Custom data ingestion for Cipher features.
- A detailed audit output.

The LEAN result should not simply compare headline return. A reconciliation report should explain every material difference from Gate 1.

### Gate 3 — Prospective shadow test

LEAN replication is still historical. A candidate must then accumulate a preregistered prospective sample using the existing VM forward-test infrastructure.

Required fields include:

- Signal timestamp.
- Feature values and versions.
- Selected contract candidates.
- Rejection reasons.
- Simulated fill and quote timestamps.
- Maximum favorable and adverse excursion.
- Exit reason.
- Expected versus observed performance.

### Gate 4 — Paper eligibility

Only strategies that pass the prospective gate may be allowed to run in `Mode.PAPER`. This still means simulated positions unless a separate broker sandbox is explicitly introduced.

---

## 18. Layer 6 — Decision synthesis and portfolio risk

### 18.1 Deterministic signal authority

Validated strategy artifacts should generate candidate positions. An LLM must not originate a trade outside those artifacts.

The live candidate object should be mechanically produced:

```yaml
strategy_id: <registered strategy>
signal_id: <immutable id>
symbol: <ticker>
direction: bullish | bearish | neutral
instrument_template: debit_spread
entry_window: <time range>
contract_constraints: {}
expected_holding_period: <duration>
invalidation: <rule>
feature_snapshot_id: <id>
```

### 18.2 LLM context panel

A Claude or other LLM panel can be used for:

- Summarizing disagreements among validated models.
- Explaining relevant events.
- Identifying missing or stale context.
- Producing a manual-review brief.
- Flagging contradictions.

It should not:

- Override deterministic risk limits.
- Invent a new position.
- Increase size.
- Bypass strategy eligibility.
- Use hidden chain-of-thought as an audit artifact.

The audit should store structured inputs, outputs, model version, prompt version, and concise stated rationale—not private hidden reasoning.

### 18.3 Portfolio optimization

Riskfolio-Lib is potentially useful after multiple independently validated strategies exist. Before that point, simple deterministic allocation is safer.

Initial portfolio controls should include:

- Maximum loss per position.
- Maximum aggregate premium at risk.
- Maximum positions.
- Per-ticker and sector limits.
- Correlation buckets.
- Event-risk limits.
- Daily loss stop.
- Liquidity limits.

Riskfolio-Lib may later optimize allocations using CVaR, CDaR, HRP, or NCO, but expected returns should come only from registered, calibrated strategy estimates.

---

## 19. Layer 7 — Shadow execution, paper execution, and future broker boundary

The existing `paper_executor` should be preserved and formalized as this layer.

### Current allowed modes

```text
DISABLED
SHADOW
PAPER
```

### Recommended meaning

- `DISABLED`: ingest and record only.
- `SHADOW`: evaluate eligible signals and virtual positions without affecting any account.
- `PAPER`: run the complete simulation lifecycle with stricter operational monitoring; still no live capital.

### Future broker adapter

A broker adapter should not be added until separately authorized. If later built, it should exist in a separate package and process only signed, promoted order intents.

Required deterministic controls would include:

- Maximum order quantity and notional.
- Price collars.
- Quote freshness.
- Spread and liquidity checks.
- Duplicate-order prevention.
- Self-trade prevention.
- Position reconciliation.
- Broker-state reconciliation.
- Rate limiting.
- Market-hours validation.
- Circuit breakers.
- Manual global kill switch.
- Immutable order-intent and broker-response audit.

The broker adapter should be incapable of generating a strategy signal.

---

## 20. Layer 8 — Evidence feedback loop

The feedback loop should first be deterministic and report-driven.

### Weekly reconciliation cycle

For every promoted strategy, compare:

- Fast-backtest expectation.
- LEAN replication.
- Prospective shadow results.
- Paper simulation.
- Execution-quality assumptions.

Measure:

- Signal-frequency drift.
- Selection drift.
- Fill decay.
- Slippage drift.
- Feature drift.
- Regime dependence.
- Exposure and concentration changes.
- Outcome attribution.

### Allowed automated actions

The system may automatically:

- Open a research issue.
- Mark a strategy degraded.
- Pause new shadow entries.
- Schedule a fixed diagnostic experiment.
- Produce a report.

### Actions requiring human approval

- Change a strategy rule.
- Change an LLM prompt used in decision support.
- Change a feature's allowed use.
- Change position sizing.
- Promote a new strategy.
- Enable broker execution.

Autonomous prompt rewriting and bandit-driven factor generation should remain deferred until the full experiment registry is operational and reproducible.

---

# Part IV — Target data flow

## 21. Hybrid architecture diagram

```text
                  EXTERNAL SOURCES
    Alpaca | Tradier | Browser captures | SEC | News | Macro
                         |
                         v
             INGESTION / VENDOR ADAPTERS
       exact timestamps, raw payload, checksum, manifest
                         |
             +-----------+-----------+
             |                       |
             v                       v
      IMMUTABLE GCS RAW LAKE    OPERATIONAL STATE
      raw JSON/JSONL/files      SQLite initially
             |                 latest quotes/queues
             |                       |
             v                       |
      NORMALIZATION JOBS              |
      canonical schemas               |
             |                       |
             +-----------+-----------+
                         v
               BIGQUERY RESEARCH WAREHOUSE
       point-in-time market, options, scans, features,
          forecasts, events, outcomes, experiments
                         |
             +-----------+------------+
             |                        |
             v                        v
      FEATURE/MODEL JOBS       RESEARCH FACTORY
  GEX, flow, regime, Kronos,   fast standardized tests
  TimesFM, news/event features  and experiment registry
             |                        |
             +-----------+------------+
                         v
             ATTRIBUTION / ANOMALY ENGINE
                         |
                         v
          STRATEGY GRADUATION AND LEAN REPLICATION
                         |
                         v
             PROSPECTIVE SHADOW VALIDATION
                         |
                         v
       DETERMINISTIC SIGNAL + PORTFOLIO RISK GATES
                         |
            optional LLM context, no trade authority
                         |
                         v
           SHADOW / PAPER EXECUTOR AND AUDIT LOG
                         |
                         v
             WEEKLY EVIDENCE RECONCILIATION
                         |
                         +--> back to research registry
```

---

# Part V — What to keep, refactor, add, and defer

## 22. Component disposition

| Component | Decision | Reason |
|---|---|---|
| Active Cipher UI and core API | **Keep** | It is the working research product. |
| Alpaca OPRA/SIP adapters | **Keep and isolate** | Valuable data, but vendor access should move behind internal contracts. |
| Tradier stream collector | **Keep** | Large raw event history and useful live quote path. |
| GEX capture and missing-data rules | **Keep** | Strong domain logic and integrity behavior. |
| Existing SQLite databases | **Keep, catalog, and migrate selectively** | They are efficient operational stores and valuable historical evidence. |
| GCS backups and browser transfer | **Keep and expand into raw lake** | Existing cloud durability can become the immutable object layer. |
| Custom research labs | **Keep, wrap in a common experiment interface** | Significant research value already exists. |
| Kronos | **Keep context-only** | Operational but not validated as a gate. |
| TimesFM bridge | **Keep blocked until provenance is valid** | The strict manifest behavior is correct. |
| Cluster prospective tests | **Keep and generalize** | This is the strongest route to trustworthy model evaluation. |
| Paper executor | **Keep, formalize as separate shadow runtime** | Good safety design and no live-order code. |
| QuantConnect/LEAN | **Complete and promote to formal Gate 2** | Needed for realistic event-driven replication. |
| BigQuery | **Add selectively** | Useful for canonical analytical queries, not necessary for all runtime state. |
| FinBERT/GDELT/SEC pipeline | **Add after data contracts** | Valuable attribution context, but timing and entity mapping must be correct first. |
| VectorBT PRO | **Optional later** | Common experiment contracts matter more than a particular sweep engine. |
| Riskfolio-Lib | **Add after multiple strategies graduate** | Portfolio optimization is premature with no validated strategy set. |
| RD-Agent(Q) | **Defer** | Current governance and reproducibility are not ready for autonomous hypothesis generation. |
| LLM directional trade panel | **Reject as trade authority** | Use LLMs for context and review, not signal origination. |
| Automatic prompt rewriting | **Defer and require approval** | It creates hidden experimental degrees of freedom. |
| Live broker execution | **Defer** | No strategy is validated or authorized for live deployment. |

---

# Part VI — Phased evolution plan

## 23. Phase 0 — Establish the truth of the repository

Before adding infrastructure:

1. Initialize and use real version control.
2. Commit the active source, configuration templates, tests, and architecture documents.
3. Keep secrets, generated databases, model weights, and raw data excluded.
4. Update project instructions to distinguish research terminal, shadow simulator, and absent broker execution.
5. Create a machine-readable system inventory.
6. Assign owners and lifecycle labels to active, experimental, and archived modules.

### Exit criteria

- Every active source file has a commit identity.
- Runtime artifacts reference a code hash.
- Documentation matches the actual execution boundary.

## 24. Phase 1 — Canonical data contracts and raw-lake manifests

1. Define canonical schemas and `available_at` semantics.
2. Add ingestion-run and raw-object manifests.
3. Create one symbol and corporate-action reference model.
4. Catalog every existing SQLite archive.
5. Add quality reports and row-count reconciliation.
6. Begin writing new raw payloads to immutable GCS paths.
7. Continue using existing SQLite services during migration.

### Exit criteria

- A decision-time query can prove exactly which data was available.
- Any normalized record can be traced to a raw object and normalizer version.

## 25. Phase 2 — Experiment and strategy registry

1. Create strategy-spec and experiment-manifest schemas.
2. Wrap the strongest existing laboratories in a common runner.
3. Standardize metrics, trades, equity curves, exclusions, and verdicts.
4. Create explicit promotion states.
5. Register current Kronos and TimesFM allowed-use policies.

### Exit criteria

- The same experiment can be rerun from its manifest.
- Two strategies can be compared under declared, equivalent assumptions.

## 26. Phase 3 — Formal two-stage backtesting

1. Select one or two strategy families as pilots.
2. Run them through the common fast engine.
3. Complete the corrected QuantConnect/LEAN reruns.
4. Version the LEAN source and environment.
5. Build automated reconciliation between the fast engine and LEAN.
6. Reject candidates whose differences cannot be explained.

### Exit criteria

- At least one candidate has a fully reproducible fast and LEAN result.
- LEAN audit artifacts are stored in the experiment registry.

## 27. Phase 4 — Generalized prospective validation

1. Generalize the current Cluster forward-test framework to registered strategies.
2. Store feature snapshots and contract candidates for every signal.
3. Lock sample sizes and thresholds before observation.
4. Compare model/context groups without changing entry rules mid-sample.
5. Add drift and data-quality alerts.

### Exit criteria

- A candidate reaches its preregistered sample without retrospective rule changes.
- Prospective results reconcile with historical assumptions.

## 28. Phase 5 — Event attribution and news features

1. Add exact-timestamp news and SEC ingestion.
2. Implement entity mapping.
3. Add FinBERT as a versioned feature.
4. Build anomaly/residual tables.
5. Keep LLM extraction context-only.

### Exit criteria

- News features have defensible `available_at` timestamps.
- Event features can be replayed in historical tests without leakage.

## 29. Phase 6 — Portfolio risk and advanced research automation

Only after multiple strategies graduate:

1. Add portfolio covariance and concentration analysis.
2. Evaluate deterministic risk parity or capped allocation.
3. Introduce Riskfolio-Lib in shadow mode.
4. Add constrained research-agent hypothesis generation.
5. Keep generated strategies behind human review and all validation gates.

### Exit criteria

- Portfolio optimization improves risk metrics out of sample without relying on unstable expected returns.
- Automated research produces fully reproducible, sandboxed experiments.

## 30. Phase 7 — Separate live-execution decision

This is not an automatic continuation of the earlier phases. It requires a separate approval document.

Minimum prerequisites:

- A validated strategy.
- LEAN replication.
- Prospective shadow success.
- Stable paper operation.
- Broker-state reconciliation.
- Independent risk review.
- Explicit capital and loss limits.
- Manual shutdown procedures.
- Separate broker adapter package.
- No LLM trade authority.

---

# Part VII — Non-negotiable principles

## 31. Data principles

1. Raw data is immutable.
2. Every record has an availability time.
3. Every derived feature is versioned.
4. Missing market data remains missing.
5. Corporate actions and universe membership are point-in-time.
6. Research queries use frozen dataset snapshots.

## 32. Research principles

1. Hypotheses are specified before holdout evaluation.
2. Failed experiments remain recorded.
3. Multiple testing is measured.
4. Parameter stability matters more than the best parameter.
5. Benchmark and capacity comparisons are mandatory.
6. Historical evidence does not replace prospective validation.
7. No model is promoted because it is technologically impressive.

## 33. Model principles

1. Model weights require manifests.
2. Training cutoffs are explicit.
3. Allowed use is explicit.
4. Context-only means no gating or sizing.
5. Model disagreements are evidence, not noise to be hidden.
6. LLM output is advisory and structured.

## 34. Execution principles

1. Strategy generation and order routing are separate systems.
2. Risk controls are deterministic.
3. Every transition emits an audit event.
4. Restarts require reconciliation.
5. Stale data blocks action.
6. Kill switches do not depend on an AI model.
7. Broker execution remains absent until separately authorized.

---

# Final conclusion

Cipher has already built the difficult early foundation that many ambitious architecture proposals overlook: real data capture, market-domain reconstruction, operational persistence, conservative simulation, and a willingness to reject weak evidence.

Its present form is best understood as four connected systems:

1. A working options-intelligence terminal.
2. A large but fragmented market-data and research estate.
3. A broad experimental backtesting laboratory.
4. A separate prospective shadow-execution runtime.

The next step should not be to add every component from an idealized AI-native stack. The next step should be to **make the current system reproducible, canonical, and promotable**.

The best-of-both-worlds architecture therefore keeps Cipher's VM, SQLite, GCS, Alpaca, Tradier, scanners, research modules, Kronos controls, forward tests, and paper-executor safeguards. It adds a selective BigQuery research warehouse, immutable data manifests, feature and strategy registries, standardized experiments, LEAN replication, anomaly attribution, and explicit promotion gates.

Autonomous agents should enter only after those foundations exist, and their first role should be autonomous research assistance—not autonomous capital control.

The desired end state is:

> A private, evidence-first quantitative research platform that can continuously collect data, generate and test hypotheses, explain model failure, and prospectively validate strategies while keeping market execution deterministic, bounded, auditable, and separately authorized.
