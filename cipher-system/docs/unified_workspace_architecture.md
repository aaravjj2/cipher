# Unified Cipher Workspace and Architecture

## 1. Purpose and operating boundary

Cipher is a private, local-first, read-only options and market-research terminal.
Its active product combines:

- Alpaca-backed stock and options market data;
- option exposure analytics such as GEX and VEX;
- scanner, flow, context, and research views;
- historical and prospective research infrastructure;
- a guarded shadow/paper simulator;
- model and factor experimentation infrastructure;
- governance, provenance, audit, repair, and scheduling controls.

The product does **not** contain a live broker-order adapter. No component may
submit an order. The maximum governance state remains `LIVE_REVIEW_REQUIRED`,
which is a review state rather than execution authority.

## 2. Unified filesystem topology

The product now has one canonical source tree and one persistent runtime tree.

```text
/home/aarav/Aarav/cipher/
├── cipher-github/                    Canonical Git repository
│   └── cipher-system/                Active product source
│       ├── core/
│       ├── app/
│       ├── scripts/
│       ├── tests/
│       ├── docs/
│       ├── config/
│       ├── schemas/
│       ├── cloud/
│       ├── mcp-server/
│       ├── access-obsidian-complete-audit/
│       ├── data -> ../../runtime/data
│       ├── logs -> ../../runtime/logs
│       └── .env -> ../../runtime/config/cipher.env
│
├── cipher-system -> cipher-github/cipher-system
│                                      Compatibility alias used by existing
│                                      systemd units and rollback tooling
│
└── runtime/                           Persistent non-Git state
    ├── data/                          All active databases, captures, artifacts
    ├── logs/                          Shared operational logs
    ├── config/
    │   ├── cipher.env                 Unified server-side credentials/settings
    │   └── scanner-ingest-token       Local browser-ingest token
    ├── governance/                    Unification and source-alias reports
    └── backups/
        ├── pre_unification_20260803T231700Z/
        │   ├── legacy_source_original/
        │   ├── canonical_data_original/
        │   └── canonical_logs_original/
        └── registry_quarantine/       Pre-quarantine registry backup
```

### Why the compatibility alias exists

The VM's existing systemd units were installed with paths under:

```text
/home/aarav/Aarav/cipher/cipher-system
```

That path now resolves to the canonical Git checkout. The unit files therefore
continue to work without maintaining a second codebase. Their process command
lines retain the legacy path, but their resolved working directories and loaded
source are canonical.

### Persistent-state rule

Source-control operations must never move or delete runtime evidence. Git
checkout changes affect `cipher-github`; databases, captures, raw responses,
logs, credentials, and governance artifacts remain under `runtime`.

## 3. Current runtime services

### Systemd-managed services

| Service | Role | Source/runtime relationship |
|---|---|---|
| `cipher-secrets.service` | Materializes secrets from Google Secret Manager into the VM environment file | Server-side configuration only |
| `cipher-core.service` | Python read-only market-data and research API on `127.0.0.1:8282` | Loads canonical `core/app.py` through the compatibility alias |
| `cipher-web.service` | Node same-origin proxy and static UI server on `127.0.0.1:8283` | Loads canonical `app/server.mjs` and `app/public/` |
| `cipher-gex.service` | Repeated GEX snapshot capture | Writes to shared runtime GEX files and SQLite |
| `cipher-tradier.service` | Read-only Tradier quote/trade stream capture | Writes to shared runtime stream database and raw event files |
| `cipher-devspace.service` | Remote coding workspace server | Development access, not market logic |

### Timer/service pairs

The infrastructure directory also defines timer-driven operations for:

- GCS backup;
- browser-folder synchronization;
- browser batch import;
- cluster alert passes;
- data-health alerts;
- governance catalog refreshes.

Whether each timer is enabled is a deployment concern; their source definitions
live under `infra/gcp-cipher-vm/systemd/`.

### Locally managed guarded daemons

| Daemon | Role | Boundary |
|---|---|---|
| Safe research scheduler | Runs four allowlisted read-only maintenance jobs | No factor/model/backtest/paper/live jobs |
| Build/test healer | Watches source fingerprints and validates changes | May retry commands and clear generated caches only; cannot edit source |

The safe scheduler and healer are detached user processes. Unlike the core,
web, GEX, and Tradier services, they are not yet reboot-persistent systemd
services.

## 4. Main live request flow

```text
Browser
  │
  ▼
Node server — app/server.mjs — port 8283
  │  static assets, same-origin API proxy, scanner ingest
  ▼
Python core — core/app.py — port 8282
  │
  ├── Alpaca stock data: SIP preferred, IEX fallback
  ├── Alpaca options data: OPRA preferred
  ├── contract metadata and open interest
  ├── scanner and exposure calculations
  ├── local historical databases and caches
  └── research/governance status artifacts
```

The browser never receives Alpaca or Tradier credentials. The Node server does
not call market-data vendors directly; it proxies local browser requests to the
Python core.

## 5. Market-data and capture flow

### Alpaca path

```text
Alpaca SIP/OPRA
  ↓
core/app.py and focused ingestion/capture scripts
  ↓
normalized application responses + immutable/raw capture files
  ↓
runtime/data databases, JSON, Parquet, and governance artifacts
```

Alpaca supplies:

- underlying stock quote/trade data;
- minute and historical stock bars;
- option snapshots;
- latest option quotes and trades;
- Greeks and implied volatility where available;
- option-contract metadata;
- public open interest and its observation date.

### Tradier path

```text
Tradier read-only stream
  ↓
core/tradier_stream_capture.py
  ↓
tradier_stream.sqlite + raw stream event files
  ↓
flow/context research, health monitoring, and paper quote support
```

Tradier is used only for market data in the active boundary. Account and order
endpoints are forbidden in active files.

### Browser capture path

```text
External browser logger/capture
  ↓
Node scanner-ingest endpoint with local token
  ↓
runtime/data/browser_ingest
  ↓
normalization/import hooks
  ↓
scanner history, forward tests, or governance registration
```

## 6. Frontend application

### `app/server.mjs`

The Node server performs four main jobs:

1. serves the static browser application;
2. proxies allowed API routes to the Python core;
3. proxies server-sent-event streams;
4. accepts locally authenticated browser scanner-ingest payloads.

It exposes the new research status route as well as the existing quote, matrix,
night-vision, flow, scan, ranking, weight-lab, and backtest routes.

### `app/public/index.html`

Defines the shell of the local terminal:

- top quote/search bar;
- workspace selector;
- navigation rail;
- Strike Matrix view;
- Night Vision view;
- research-panel mount;
- account/settings and local-lab navigation.

### `app/public/app.js`

Contains browser state, API loading, rendering, interactions, scanner controls,
charts, matrices, flow tables, watchlists, journal surfaces, local research
panels, and the Research Status view.

### `app/public/styles.css`

Contains the visual system and responsive layout for the matrix, scanners,
research cards, tables, and local operator status.

### Supporting frontend files

- `scanner_ingest.mjs`: authenticated local ingestion handler;
- `launcher.mjs`: simple combined core/web launcher for manual development;
- `research-brief.*`: standalone local research-brief view;
- `signals-dashboard.html`: auxiliary signal dashboard;
- `app/test/scanner-ingest.test.mjs`: Node ingest tests.

## 7. Python core application

### Runtime/API foundation

| Module | Role |
|---|---|
| `core/app.py` | Main read-only HTTP API; vendor requests, caches, API routes, governance and research status |
| `core/env.py` | Loads the unified root `.env` without overriding explicit process variables |
| `core/data_fetcher.py` | Focused historical/provider data retrieval helper |
| `core/exposure.py` | Option exposure matrix calculations and expiration/chain controls |
| `core/option_dataset.py` | Option data normalization and reusable dataset structures |
| `core/signals.py` | Shared signal primitives |
| `core/signal_aggregator.py` | Combines multiple signal inputs into research context |
| `core/regime_detector.py` | Market-regime classification helpers |

### Scanner and setup analysis

| Module | Role |
|---|---|
| `core/scanner.py` | Primary Setup Scanner, Flash, Cluster, Liq, and Cipher-model scoring |
| `core/dynamic_strike_zones.py` | Converts exposure profiles into dynamic strike zones |
| `core/gex_momentum.py` | GEX momentum/context features |
| `core/gex_forecast.py` | GEX-history forecasting research |
| `core/gex_surface_interpolation.py` | Interpolates incomplete exposure surfaces |
| `core/flow_imbalance.py` | Option flow imbalance features |
| `core/smart_money_divergence.py` | Divergence/context heuristic |
| `core/pre_entry_factor_scorer.py` | Pre-entry factor scoring for research |
| `core/setup_research_engine.py` | Structured setup research reports |
| `core/company_research_engine.py` | Company-level research context |
| `core/index_daytrade_context.py` | Intraday index context |

### Capture and live-state modules

| Module | Role |
|---|---|
| `core/gex_capture.py` | Captures Alpaca-backed GEX snapshots and normalizes cells into SQLite |
| `core/gex_replay.py` | Replays historical GEX snapshots |
| `core/live_option_chain_capture.py` | Captures current option chains for the liquid scanner panel |
| `core/live_chain_archive.py` | Archives captured chain files |
| `core/eod_option_archive.py` | End-of-day option archive support |
| `core/scan_option_mark_capture.py` | Captures option marks associated with scanner observations |
| `core/tradier_stream_capture.py` | Read-only Tradier streaming collector |
| `core/position_monitor.py` | Local simulated/research position monitoring |

### Forward-test and cluster modules

| Module | Role |
|---|---|
| `core/cluster_backtest.py` | Scores recorded scanner clusters and forward outcomes |
| `core/cluster_confidence.py` | Cluster confidence calculation |
| `core/cluster_decay.py` | Cluster signal decay behavior |
| `core/cluster_kronos_forward.py` | Kronos-conditioned cluster forward research |
| `core/flow_cluster_backtest.py` | Flow-plus-cluster historical evaluation |
| `core/flow_forward_test.py` | Prospective flow observations |
| `core/first_quad_outcome.py` | First-quadrant outcome studies |
| `core/flash_agentic_sim.py` | Simulation-only Flash Agentic behavior |
| `core/flash_agentic_live_loop.py` | Live-data observation loop; still simulation/read-only |

### Backtest engines and research labs

The workspace contains many focused research modules. They are not all active
production services. They are grouped by purpose:

- General engines: `historical_backtest.py`, `intraday_backtest.py`,
  `price_backtest.py`, `strategy_backtest.py`, `edge_backtest.py`,
  `walk_forward.py`, `option_backtest_engine.py`, `option_portfolio_backtest.py`.
- Historical option data: `historical_options_download.py`,
  `historical_option_strategy_lab.py`, `equity_history_download.py`.
- Earnings studies: `earnings_advanced_technique_lab.py`,
  `earnings_defined_risk_lab.py`, `earnings_robinhood_compatible_lab.py`.
- End-of-day studies: `eod_pattern_lab.py`, `eod_option_pattern_lab.py`,
  `eod_option_walkforward.py`, `eod_best_strategy_lab.py`,
  `eod_best_strategy_options_lab.py`.
- Watchlist studies: `watchlist_history_analysis.py`,
  `watchlist_exit_backtest.py`, `watchlist_indicator_exit_backtest.py`,
  `watchlist_final_strategy_backtest.py`.
- Option strategy expansion: `recent_call_combo_strategy_lab.py`,
  `recent_option_strategy_expansion.py`, `weekly_option_strategy_validation.py`,
  `option_strategy_synthetic_stress.py`, `option_outcome_factor_lab.py`,
  `option_flow_alert_audit.py`, `research_backed_option_strategy.py`,
  `research_feature_fusion.py`.
- Capital/portfolio studies: `capital_efficient_multi_stock_option_lab.py`,
  `leveraged_etf_csp_wheel.py`, `leveraged_etf_wheel_download.py`,
  `leveraged_etf_wheel_iterate.py`, `leveraged_etf_wheel_parameter_lab.py`.
- Specific setup studies: `amzn_setup_refinement_lab.py`,
  `amzn_setup_strategy_lab.py`, `cross_stock_reclaim_validation.py`,
  `cross_ticker_correlation.py`.
- Ranking and calibration: `ranking_lab.py`, `weight_lab.py`.
- Contract planning/pricing: `option_contract_planner.py`,
  `kronos_option_pricer.py`.

These labs generate evidence; they do not automatically acquire promotion or
execution authority.

## 8. Research governance platform

`core/research_platform/` is the formal evidence and governance plane.

### Identity, configuration, and storage

| Module | Role |
|---|---|
| `models.py` | Canonical dataclasses/enums for datasets, features, strategies, experiments, promotions, and audits |
| `hashing.py` | Stable canonical hashes and IDs |
| `config.py` | Research-platform configuration |
| `bootstrap.py` | Initializes the registry and artifact directories |
| `registry.py` | SQLite canonical registry and entity persistence |
| `artifact_store.py` | Content-addressed immutable artifact storage |
| `inventory.py` | Runtime/source/data inventory snapshots |
| `current_evidence.py` | Reads and summarizes current evidence state |

### Data plane

| Module | Role |
|---|---|
| `raw_lake.py` | Immutable raw-object manifests and storage |
| `datasets.py` | Dataset freezing, manifests, lineage, and snapshots |
| `warehouse.py` | Local analytical warehouse utilities |
| `canonical_exports.py` | Stable exports from canonical records |
| `local_market_catalog.py` | Catalogs available local market data |
| `market_data_providers.py` | Provider-neutral market-data contracts |
| `market_quality.py` | Price-only/full-volume gate evaluation |
| `corporate_actions.py` | Corporate-action capture and adjustment evidence |
| `reference_volume.py` | Independent reference-volume import/reconciliation contracts; currently deferred |
| `huggingface_datasets.py` | Bounded Hugging Face dataset import support |

### Features, models, and events

| Module | Role |
|---|---|
| `features.py` | Registers feature definitions and snapshots |
| `factors.py` | Safe factor DSL and factor candidate contracts |
| `qlib_price_only.py` | Price-only Qlib/RD-Agent study adapter |
| `model_context.py` | Context-only model-use boundaries |
| `forecast_ranking.py` | Cross-sectional forecast ranking and evaluation |
| `news.py` | News-document identity, chunking, FinBERT sentiment, event registration |
| `attribution.py` | Non-causal forecast residual and event-association engine |

### Experiments and graduation

| Module | Role |
|---|---|
| `experiments.py` | Common experiment manifest/result contract and runner |
| `engine_adapters.py` | Fast-engine adapters such as VectorBT-style outputs |
| `lean.py` | LEAN audit and replication validation |
| `risk.py` | Research risk checks |
| `portfolio.py` | Simulation-only portfolio optimization/proposals |
| `promotion.py` | Evidence-gated promotion state machine |
| `prospective.py` | Preregistered prospective tests and first-observation-wins scoring |
| `reconciliation.py` | Fast/LEAN/prospective/paper evidence reconciliation |
| `context_panel.py` | Guarded multi-agent/LLM context memos without trade authority |

### Operations and repair

| Module | Role |
|---|---|
| `local_scheduler.py` | Read-only job definitions and cadence state |
| `repair_boundary.py` | Prohibits repairs from changing research meaning |
| `repair_actions.py` | Allowlisted checksum/cache/retry repairs with incident records |
| `build_healing.py` | Compile/test validation and bounded mechanical healing |
| `external_integrations.py` | Registers external tool/repository boundaries |
| `local_capabilities.py` | Runtime capability detection |
| `cloud_deploy.py` | Historical/optional cloud-deployment helpers; not the active local-first path |

## 9. Formal eight-layer architecture

The formal topology is represented by `EightLayerStackSpec` in
`research_platform/seven_layer_stack.py`. `SevenLayerStackSpec` is retained only
as a compatibility alias.

### Governance plane

Cross-cutting contracts for identity, provenance, contamination control,
preregistration, promotion, audit, and evidence retention.

### Layer 1 — Hybrid data foundation

Collects raw market/event data, stores immutable evidence, normalizes data,
checks missingness/quality, and produces dataset manifests.

Current state: partial. Real raw records and 11 dataset manifests exist, but the
entire active 744-partition panel is not comprehensively linked through one
canonical frozen dataset lineage.

### Layer 2 — Feature and forecasting services

Produces registered features, model context, event sentiment, and safe factor
candidates.

Current state: partial. Two feature definitions and 55 feature snapshots exist;
Kronos and TimesFM remain context-only/rejected for promotion, while FinBERT is
active for headline triage.

### Layer 3 — Controlled research factory

Runs common governed experiments, standardized outputs, statistical gates,
walk-forward analysis, and fast-engine screening.

Current state: structural only. The registry contains zero governed experiments.

### Layer 4 — Attribution and anomaly analysis

Associates forecast residuals or interval breaches with market/sector/event
context without claiming causal identification.

Current state: code only. There are no real registered anomaly events because no
validated forecast stream exists.

### Layer 5 — Strategy graduation

Requires registered strategy specifications, historical experiments, corrected
statistics, LEAN replication, prospective validation, and promotion evidence.

Current state: partial inputs only. Two strategy specifications, one prospective
test, and 55 observations exist, but no governed experiment, promotion event,
LEAN replication, or reconciliation chain exists.

### Layer 6 — Decision synthesis and simulated portfolio risk

Combines already-graduated strategies, deterministic risk checks, portfolio
constraints, and context memos.

Current state: deferred by prerequisite. There are no promoted strategies.

### Layer 7 — Shadow and paper execution

Simulates contracts, fills, positions, exits, recovery, and risk controls in a
separate process with no broker-order client.

Current state: strongly implemented but not activated by a promoted canonical
strategy.

### Layer 8 — Evidence feedback loop

Compares historical, LEAN, prospective, and paper evidence; identifies drift;
and recommends research review or shadow pauses without changing protected
research definitions.

Current state: code only. The registry contains zero evidence reconciliations.

## 10. Shadow/paper executor package

`core/paper_executor/` is an isolated simulation runtime.

| Module | Role |
|---|---|
| `config.py` | Shadow/paper configuration and fail-closed defaults |
| `models.py` | Internal signal, contract, fill, position, and episode models |
| `database.py` | Persistent simulation state and reconciliation |
| `ingestion.py` | Ingests allowed signal/capture payloads |
| `contract_selector.py` | Deterministic option-contract selection |
| `quote_manager.py` / `quote_stream.py` | Read-only quote management |
| `fill_simulator.py` | Simulated fill/slippage behavior |
| `position_manager.py` | Position lifecycle and exits |
| `risk_guard.py` | Position/capital/duplicate/kill-switch protections |
| `episode_tracker.py` | Groups signals and outcomes into episodes |
| `runtime.py` | Coordinates workers and queues |
| `service.py` | Loopback-only service/API |
| `health.py` | Runtime health and readiness |
| `reporting.py` | Paper/shadow reports |
| `validation.py` | Validates boundaries and configuration |
| `tradier_market_data.py` | Read-only Tradier quote adapter; no account/order endpoints |
| `option_timesales_downloader.py` | Read-only option time-and-sales capture |
| `capture_files.py` | Capture file handling |
| `capture_backtest.py` | Replays recorded captures |
| `cluster_forward_test.py` | Prospective cluster observation logic |
| `vm_forwarder.py` | Controlled payload forwarding between capture/runtime locations |

## 11. Build/test healing

The healer observes a source/configuration fingerprint. On change it runs:

1. `git diff --check`;
2. Python compilation;
3. Node server syntax;
4. Node launcher syntax;
5. browser JavaScript syntax;
6. the full active Cipher pytest suite.

Allowed repairs:

- retry a deterministic validation command;
- remove generated Python/pytest caches;
- rerun the complete suite;
- write incident and diagnostic artifacts.

Forbidden behavior:

- source editing;
- package installation/upgrades;
- gate/threshold changes;
- research-data mutation;
- promotion changes;
- commits or pushes;
- paper/live execution.

## 12. Safe scheduler

The scheduler has four allowlisted jobs:

| Job | Cadence | Role |
|---|---:|---|
| Master status refresh | Hourly | Refreshes operator status artifacts |
| Public-event ingestion | Daily | Captures real public headline metadata and applies pinned FinBERT |
| Bounded repair audit | Daily | Verifies checksums and rebuildable caches only |
| Infrastructure audit | Daily | Rechecks installed tools, model cache, host blockers, and boundaries |

No strategy-discovery, model-search, backtest, paper, or live job is part of the
unattended scheduler.

## 13. Persistent runtime data

The unified runtime currently contains approximately 57.2 GB and 68,834 files.
Major stores include:

### Streaming and live capture

| Path | Role |
|---|---|
| `tradier_stream.sqlite` | Large normalized Tradier event/quote database |
| `tradier_stream_events/` | Immutable/raw stream event files |
| `live_option_chains/` | Current and historical option-chain captures |
| `live_option_chains_archive.sqlite` | Chain archive index/database |
| `gex_history.sqlite` | Normalized GEX snapshots and cells |
| `gex_snapshots/` | Raw GEX response snapshots |
| `browser_ingest/` | Browser-originated scanner/capture batches |

### Historical market data

| Path | Role |
|---|---|
| `historical_options/` | Historical option-chain/quote/trade archives |
| `historical_equities/` | Historical equity files |
| `historical_bars.sqlite` | Historical stock-bar store |
| `raw/` | New governed raw provider responses |
| `normalized/` | Normalized Parquet research panels |
| `raw_lake/` | Canonical raw-lake objects where registered |

### Governance and research

| Path | Role |
|---|---|
| `governance/research_registry.sqlite` | Merged canonical registry |
| `governance/` | Preregistrations, architecture audits, scheduler state, manifests, model/cache audits |
| `artifacts/` | Content-addressed research artifacts |
| `research_snapshots/` | Frozen/research snapshot material |
| `warehouse_exports/` | Canonical export outputs |
| `market_quality/` | Price/full-volume gate reports and cohort studies |
| `events/` | Public-event ingestion artifacts |
| `cache/` | Rebuildable derived summaries |
| `repair_incidents/` | Immutable bounded-repair incidents |

### Research output families

The runtime also retains historical outputs from:

- backtests and option labs;
- earnings strategy studies;
- EOD pattern and walk-forward studies;
- leveraged-ETF wheel studies;
- watchlist analyses;
- setup/company research;
- option flow audits;
- index day-trade context;
- factor and ranking labs;
- cluster/flow forward tests;
- paper trades, positions, and simulated episodes;
- contract plans and option mark captures.

These directories are evidence archives. Their existence does not imply that the
results passed the canonical experiment/promotion chain.

## 14. Merged registry state

After unification and explicit test-record quarantine, the active registry
contains:

| Entity | Count |
|---|---:|
| Raw objects | 4,857 |
| Dataset manifests | 11 |
| Dataset-to-raw links | 11 |
| Feature definitions | 2 |
| Feature snapshots | 55 |
| Strategy specifications | 2 |
| Governed experiments | 0 |
| Experiment artifacts | 0 |
| Promotion events | 0 |
| Prospective tests | 1 |
| Prospective observations | 55 |
| News events | 28 |
| Anomaly events | 0 |
| Evidence reconciliations | 0 |
| Active audit events | 5,013 |

Twenty pytest fixture records were moved into
`quarantined_registry_records`. They remain auditable but are no longer active
production evidence. A complete pre-quarantine database backup is retained.

## 15. Models and external research engines

### Kronos

- copied reference source under `Stock data/external/Kronos`;
- checkpoint and tokenizer revisions are pinned in the model cache;
- CPU synthetic inference works;
- current Cipher formulations are archived/context-only;
- copied source lacks verifiable local Git metadata.

### TimesFM

- package and checkpoint cached;
- CPU inference smoke passes;
- current formulations are rejected/reproducibility-only;
- no promotion based on runtime availability.

### FinBERT

- pinned model revision;
- active for headline-metadata sentiment triage;
- explanatory/risk context only;
- not a directional trade generator.

### Qlib and RD-Agent

- installed as factor/research infrastructure;
- only usable through a preregistered, leakage-safe study;
- no autonomous factor search is currently scheduled.

### VectorBT

- installed for fast screening;
- intended as the first stage before LEAN;
- no candidate currently has a complete governed experiment.

### LEAN

- native source is in an external working directory outside this repository;
- native launcher compiles under .NET 10;
- source revision and launcher checksum are audited;
- no real Cipher strategy replication has been run;
- upstream dependency advisories remain recorded.

## 16. Ticker scopes

Ticker lists are purpose-specific.

### Active dynamic setup scanner

The scanner currently loads the restored cap-filtered optionable universe:

- 546 symbols;
- mega, large, and medium tiers;
- small and unknown tiers excluded.

### Flash panel

```text
SPY, QQQ, IWM, AAPL, MSFT, NVDA, TSLA, AMD, AMZN, META, GOOGL, NFLX
```

### Flash Index

```text
SPY, QQQ, IWM
```

### Original Holdout C panel

```text
AAPL, GE, IWM, MSFT, NVDA, QQQ, SPY, XLE, XLF
```

### Structural rescue candidates

```text
AMD, AMZN, GOOGL, META, TSLA
```

DIA was tested separately and had repeated sub-391-bar sessions, although it
appears in one eligible final window.

### Public-event monitoring

```text
SPY, QQQ, IWM, XLF, XLE, AAPL, MSFT, NVDA, GE
```

## 17. Configurations and schemas

### `config/`

- `research-platform.json`: research-platform paths and settings;
- `kronos_forward_preregistered.json`: frozen Kronos forward protocol;
- `cluster_kronos_forward_preregistered.json`: frozen cluster/Kronos protocol;
- `leveraged_etf_wheel_universe.json`: wheel-study universe.

### `schemas/research/`

Machine-readable schemas exist for:

- raw-object manifests;
- dataset manifests;
- feature specifications;
- factor candidates;
- strategy specifications;
- experiment manifests;
- LEAN audits;
- news documents.

## 18. Scripts and operational entrypoints

Scripts fall into these groups:

- capture: GEX, option chains, browser scans, provider data, corporate actions;
- ingestion: Alpaca Holdout C, Hugging Face, public events, browser batches;
- cohort/gates: scope, construct, freeze, reconcile, and close Holdout C studies;
- models/factors: Kronos, TimesFM, Qlib/RD-Agent, forecast ranking;
- audits: architecture, infrastructure, LEAN, reference volume, local data,
  profitability, unification;
- scheduling/repair: safe scheduler, build healer, bounded repairs;
- deployment/maintenance: archive, health, Hermes delivery, PowerShell wrappers;
- unification: runtime migration, source alias, unified service manager.

PowerShell scripts remain primarily for Windows/manual operational workflows.
Linux production services use the canonical Python/Node modules and the
`infra/gcp-cipher-vm` shell/systemd definitions.

## 19. Tests

The active pytest suite covers:

- data-plane manifests and raw storage;
- research governance and promotion boundaries;
- experiments, prospective tests, risk, attribution, and reconciliation;
- market-quality and reference-volume rules;
- Holdout C cohort construction;
- Kronos/TimesFM research boundaries;
- scanner safety;
- Tradier capture;
- browser importer isolation;
- paper executor end-to-end behavior, recovery, deduplication, fills, exits,
  risk, and security;
- operational maintenance;
- build healing;
- original architecture audit;
- unified runtime migration and registry quarantine.

External copied repository tests are intentionally excluded from the active
Cipher suite and are audited separately.

## 20. Deployment and VM infrastructure

`infra/gcp-cipher-vm/` contains the reproducible VM deployment definitions:

- systemd unit/timer templates;
- secret synchronization;
- GEX and Tradier loop wrappers;
- GCS backup;
- browser-folder synchronization;
- governance catalog refresh;
- deployment and repair scripts;
- Tailscale setup helpers.

The installed `/etc/systemd/system` units may lag source templates. The
compatibility alias ensures the currently installed core/web/GEX/Tradier units
execute canonical source without a privileged unit-file rewrite.

## 21. Reference and archive areas

### Root reports

The repository root contains earlier backtest reports, improvement logs,
strategy analyses, migration handoffs, session reports, and raw CSV/JSON
artifacts. These are historical evidence and planning documents, not the active
runtime contract.

### `access-obsidian-complete-audit/`

Contains screenshots, DOM captures, JSON exports, research notes, and
weight-inference material used for clean-room functional comparison. It is a
reference/audit corpus, not runtime code.

### `Stock data/external/Kronos/`

Copied external Kronos source used for research/reference. It is not part of the
active pytest collection and does not supply execution authority.

### `mcp-server/`

Earlier/experimental MCP research-engine server, local SQLite database, and
exports. It is not the main Python core or Node UI service.

### `cloud/tradier-stream-service/`

Containerized Tradier stream-service reference/deployment package. The active VM
uses the systemd Tradier collector.

### Root `data/`

A small older root-level data directory remains outside the unified product
runtime. Active data is under `/home/aarav/Aarav/cipher/runtime/data`.

## 22. Governance lifecycle

The intended research path is:

```text
Immutable provider response
  ↓
RawObjectManifest
  ↓
DatasetManifest + raw links + normalizer identity
  ↓
FeatureSpec and FeatureSnapshot
  ↓
StrategySpec
  ↓
ExperimentManifest and standardized result
  ↓
statistical / regime / quality gates
  ↓
LEAN replication and reconciliation
  ↓
preregistered prospective observations
  ↓
promotion decision
  ↓
shadow/paper simulation
  ↓
historical / LEAN / prospective / paper reconciliation
  ↓
LIVE_REVIEW_REQUIRED
```

The current system has partial records near the beginning and in prospective
observation, but it does not yet have an uninterrupted end-to-end chain.

## 23. Safety boundary

The following remain prohibited:

- live order endpoints;
- broker trading clients;
- automatic capital allocation;
- LLM-originated trades;
- LLM overrides of deterministic risk;
- promotion based only on model availability or synthetic smoke tests;
- threshold/gate relaxation to force a passing study;
- hidden data source mixing;
- treating missing gamma, OI, prices, or volume as valid zero values;
- treating context-only models as validated strategies.

## 24. Current completion state

### Unified product runtime

The source/runtime merge is complete when the unified-product audit passes:

- canonical source alias correct;
- shared data/log/config links correct;
- core/web/GEX/Tradier active from canonical source;
- core and web health routes pass;
- Research Status works through Python and Node;
- registry integrity passes;
- active pytest contamination is zero;
- safe scheduler and healer are active;
- execution authority is absent.

A separate focused post-merge verification then confirms that the four systemd
services recover through the canonical alias, the original Holdout C panel
still recounts to 11/12 strict origins, and the point-in-time timestamp and
eight-layer naming corrections remain intact. Its current verdict is
`PASSED_WITH_KNOWN_CANONICAL_LINEAGE_GAP`: runtime behavior is verified, but the
Holdout C panel is not yet represented by a complete canonical registry
lineage. See `docs/post_merge_verification.md`.

### Original research architecture

The original architecture remains `INCOMPLETE`:

- phase exit criteria met: 0/8;
- operational layers fully complete: 0/8;
- safety boundary: passed;
- formal topology: 8 layers plus governance;
- canonical registry: partially adopted;
- complete experiment-to-LEAN-to-prospective-to-paper chain: absent.

This is a calibrated measurement, not a regression. The unified product now
makes the real state observable from one source and one runtime.

## 25. Rollback

The original source tree is retained at:

```text
/home/aarav/Aarav/cipher/runtime/backups/
  pre_unification_20260803T231700Z/legacy_source_original
```

The original canonical data and logs from before the merge are also retained in
that backup directory. The active registry additionally has a full
pre-quarantine backup.

A rollback would require stopping services, removing the compatibility symlink,
restoring the legacy source directory, and restarting systemd services. Runtime
data remains shared and should not be deleted during rollback.
