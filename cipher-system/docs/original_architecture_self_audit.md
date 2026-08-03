# Original Architecture Self-Audit

**Baseline:** `CIPHER_CURRENT_STATE_AND_HYBRID_ARCHITECTURE_THESIS.md`  
**Machine-readable audit:** `data/governance/original_architecture_self_audit.json`  
**Verdict:** `INCOMPLETE`  
**Live execution authority:** none

## Scope correction

The later `Master End-State Eight-Track Close-Out` closes one bounded operational
work package. Its `all_eight_closed` field does **not** certify completion of the
original thesis's eight layers, governance plane, or phased exit criteria.

The stable work-package status now records both facts independently:

- `work_package_complete`: whether that bounded queue has an honest close-out;
- `architecture_complete`: always determined separately by this architecture audit.

At the time of this audit, the work package is closed and the architecture is
not complete.

## Executive verdict

Cipher has a substantial and safety-conscious architectural foundation:

- a committed active codebase;
- immutable artifact and identity primitives;
- dataset, feature, strategy, experiment, promotion, prospective, risk,
  attribution, and reconciliation service implementations;
- strong paper-executor isolation and recovery controls;
- revision-pinned Kronos, TimesFM, and FinBERT runtime evidence;
- a native, revision-identified LEAN build;
- extensive safety and governance tests;
- no active broker-order path.

However, the original target was an **operational evidence pipeline**, not only a
set of classes and tests. The canonical registry currently contains no real
registered datasets, features, strategies, experiments, promotions, prospective
tests, or evidence reconciliations. No strategy has passed the fast-test, LEAN,
prospective, and paper sequence. Therefore the architecture's principal exit
criteria remain unmet.

Strictly evaluated, zero of the eight thesis phases have reached their complete
exit criteria. This does not mean zero implementation exists; it means every
phase still has at least one required operational condition missing.

## Highest-severity findings

### 1. Work-package completion was overstated as system completion

The `all_eight_closed` status referred to eight recently selected work tracks,
not the eight architectural layers. Several of those tracks were closed because
data was insufficient. That is a valid research close-out, but it is not an
implemented architectural layer.

The UI and status schema now say **WORK PACKAGE CLOSED** and explicitly state
that the original architecture is not thereby complete.

### 2. Canonical governance contracts are not used by real research runs

Current canonical registry counts:

| Entity | Count |
|---|---:|
| Raw objects | 1 |
| Datasets | 0 |
| Features | 0 |
| Feature snapshots | 0 |
| Strategies | 0 |
| Experiments | 0 |
| Promotion events | 0 |
| Prospective tests | 0 |
| Prospective observations | 0 |
| Evidence reconciliations | 0 |
| News events | 28 |
| Anomaly events | 0 |

The registry code is mature enough to represent the architecture, but the real
market panel, factor screen, model close-outs, existing forward tests, and
paper-executor evidence have not been imported into those canonical entities.

### 3. A test polluted the production governance registry

The one canonical raw-object record points to a file under a pytest temporary
directory. The browser-importer test invoked the normal governance hook without
redirecting or disabling it.

The test suite now sets `CIPHER_GOVERNANCE_HOOKS=0` automatically for those
importer tests. The existing row is retained and reported rather than silently
deleted from an append-oriented audit store. It must not be treated as production
market evidence.

### 4. Normalized data is not traceable through canonical manifests

The checkout contains:

- 744 normalized Alpaca SIP Parquet partitions;
- 767 raw data files;
- zero files in the canonical local raw-lake directory;
- zero frozen research snapshots;
- zero warehouse exports;
- zero canonical dataset manifests.

The data exists and was quality-audited for a specific study, but a normalized
record cannot yet be traced through the canonical registry to a registered raw
object and normalizer version as required by Phase 1.

### 5. Runtime artifact code identity is sparse

There are 833 JSON artifacts under `cipher-system/data`. Only one contains a
recognized code, normalizer, or source-commit identity: the native LEAN build
audit.

The thesis requires runtime evidence to reference an immutable code identity.
The current Git repository is healthy, but most previously generated artifacts
cannot prove which code revision produced them.

### 6. The formal stack topology now matches the eight-layer design

The earlier self-audit found that the formal stack omitted shadow/paper
execution and used a causality-implying attribution name. That discrepancy has
been corrected. `EightLayerStackSpec.default()` now defines:

1. foundational data warehouse;
2. forecasting and feature generation;
3. factor discovery;
4. attribution and anomaly analysis;
5. backtesting gate;
6. decision synthesis and simulated portfolio risk;
7. shadow and paper execution;
8. evidence feedback.

The attribution layer is now named `attribution_and_anomaly_engine`. The legacy
`SevenLayerStackSpec` import remains only as a compatibility alias and returns
the same eight-layer topology. This fixes the topology description; it does not
change the operational-completion verdict for any layer.

### 7. Vendor access is not isolated behind the data layer

At least 24 active files contain direct vendor URLs. The core UI API itself calls
Alpaca directly. This preserves the working product, but does not meet the target
rule that upper research/application layers consume normalized internal
repositories while only ingestion adapters contact vendors.

### 8. External repository revisions are declared but not verifiable

Eight external repositories are registered and their directories exist. Their
copied trees do not contain `.git` metadata, so none of the declared commit
hashes can be independently verified from the local evidence. Registration is
therefore path- and policy-level, not version-proven functional integration.

### 9. Event data is real but not historical-replay complete

The event registry contains 28 real Yahoo Finance headline-metadata records,
scored by revision-pinned FinBERT. This is meaningful real operation.

It is still partial because:

- SEC submissions were unavailable from the VM;
- GDELT was unavailable or throttled;
- no full document bodies or event taxonomy were produced;
- no structured LLM extraction ran;
- no real anomaly/residual record exists;
- prior event rows used publication time as receipt time.

Future ingestion now records system receipt/availability no earlier than the
actual observation time. Existing rows remain useful current context but must
not be represented as complete point-in-time historical replay evidence.

### 10. Scheduling is active but not durable across reboot

The guarded scheduler is running and excludes factor, model, backtest, paper,
and live-execution jobs. It is a detached process without systemd or crontab
registration. It will not automatically return after a VM reboot.

### 11. The proposed six-screen governed UI is not built

Among Morning Brief, Strategy Lab, Agent Observatory, Event Log,
Portfolio/Risk, and Settings, only Settings exists under that name. The new
Research Status screen is useful and truthful, but it does not complete the
six-screen architecture.

## Phase audit

| Phase | Target | Status | Exit criteria |
|---:|---|---|---|
| 0 | Establish repository truth | **Partial** | Not met: code is committed, but runtime artifacts rarely reference code identity and external repo commits are unverifiable. |
| 1 | Canonical data contracts and raw manifests | **Not met** | No real canonical dataset manifests or normalized-to-raw registry links. |
| 2 | Experiment and strategy registry | **Not met** | Contracts exist, but no real strategy or experiment is registered. |
| 3 | Formal fast + LEAN backtesting | **Not met** | LEAN builds; no paired fast/LEAN candidate or reconciliation exists. |
| 4 | Generalized prospective validation | **Not met** | Service code exists; canonical prospective tables are empty. |
| 5 | Event attribution and news features | **Partial** | Real FinBERT events exist; replay-safe coverage, event taxonomy, and anomaly records do not. |
| 6 | Portfolio risk and advanced automation | **Deferred by prerequisite** | No graduated strategies provide valid inputs. |
| 7 | Separate live-execution decision | **Deferred by design** | Correctly absent; no authorization document or prerequisites exist. |

## Layer audit

| Plane/layer | Status | What exists | What prevents completion |
|---|---|---|---|
| Governance plane | **Structural partial** | IDs, schemas, artifacts, registry, promotion gates, audits | Minimal real adoption; one test-contaminated record |
| 1. Hybrid data foundation | **Partial** | Real captures, raw files, Parquet panel, quality gates | No canonical registered dataset; vendor calls remain distributed |
| 2. Feature/forecast services | **Partial** | Model runtimes, factor DSL, FinBERT | Feature registry empty; forecasts context-only/rejected |
| 3. Controlled research factory | **Structural partial** | Common output contract and gate evaluator | Zero real canonical experiments |
| 4. Attribution/anomaly | **Code only** | Tested attribution engines | Zero real anomalies and no validated forecast stream |
| 5. Strategy graduation | **Code only** | Promotion service, LEAN validator, prospective service | Zero strategies, experiments, promotions, or replications |
| 6. Decision/portfolio risk | **Deferred** | Deterministic risk and SciPy portfolio optimizer | No graduated strategies or real context-panel records |
| 7. Shadow/paper execution | **Implemented, not promotion-fed** | Strong isolated simulator, recovery, kill switch, market-data allowlist | Not formalized in stack spec; no promoted strategy input |
| 8. Evidence feedback | **Code only** | Reconciliation and feedback classes | Zero evidence reconciliations or weekly promoted-strategy cycle |

## Strongest architecture areas

### Execution safety

The active product and paper executor contain no broker-order client. Tradier
access is allowlisted to market-data endpoints. Shadow mode, kill switch,
restart reconciliation, loopback binding, and security checks are extensively
tested. This is the clearest part of the original architecture that has been
implemented faithfully.

### Model/checkpoint provenance

Kronos and TimesFM public checkpoints are revision-pinned and cached with file
checksums. Synthetic CPU inference was verified without reopening rejected
research claims. FinBERT is revision-pinned. LEAN source has a recorded commit
and compiled launcher checksum.

### Structural governance code

The data models and service boundaries are materially more complete than the
runtime registry suggests. Dataset, feature, strategy, experiment, promotion,
prospective, risk, portfolio, attribution, and reconciliation services exist and
have meaningful tests. The main deficiency is adoption by actual workflows, not
absence of every component.

### Fail-closed research behavior

The corrected original Holdout-C constructor found 11 strict origins instead
of the required 12. Factor, model, and backtest work was stopped rather than
weakening the rule. An existing-data audit then found no hidden rescue. A fixed,
preregistered same-provider candidate basket subsequently established 14
structural origins without ranking/model outcome inspection. Because the period
had already been used for exploratory work, that result is explicitly limited
to structural cohort eligibility and does not claim to restore an untouched
final holdout. This sequence matches the architecture's evidence-first
principles even though the research and graduation layers remain operationally
empty.

## Accepted later deviations

### Local-first storage

The later governing checklist replaced the original GCS/BigQuery requirement
with local DuckDB/SQLite. This audit does not list cloud provisioning as an open
task. It does still require a canonical local analytical store and manifest
chain; those are not currently complete.

### Volume-sensitive research

Independent reference-volume acquisition is deferred. The full volume gate
remains unchanged and does not block honest price-only or infrastructure work.

### Live execution

Live trading remains separately authorized and outside the current system. Its
absence is a safety success, not a missing coding task to fill automatically.

## Correct interpretation of current state

The accurate statement is:

> Cipher has a strong, tested, execution-safe research-platform scaffold and a
> working market-intelligence product, plus real event ingestion and real data
> capture. It has not yet operationalized the canonical evidence chain required
> by the original architecture. No real strategy has traveled from a registered
> frozen dataset through a registered fast experiment, LEAN replication,
> prospective validation, portfolio review, paper simulation, and evidence
> reconciliation.

Consequently:

- the bounded eight-track work package is closed;
- the original architecture is **not complete**;
- no strategy is paper-eligible through the canonical promotion system;
- no live execution is authorized;
- further claims of architectural completion must be based on this audit's phase
  and layer criteria, not on work-item closure counts.
