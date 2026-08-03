# Master End-State Eight-Track Close-Out

> **Scope warning:** this document closes one eight-track operational work
> package. It does not certify completion of the original eight-layer hybrid
> architecture or its phased exit criteria. The authoritative architecture
> assessment is produced by `scripts/audit_original_architecture.py`.

## Governing rule

The active work queue follows one fail-closed rule:

> When the available data does not satisfy a frozen requirement, close the
> track as `closed_data_insufficient`. Do not weaken the requirement, mix
> sources, manufacture observations, substitute synthetic evidence, or keep
> searching until a positive result appears.

Volume-sensitive research and independent reference-volume acquisition are
outside this active close-out. Their existing gates remain unchanged and their
blocked state does not prevent honest completion of unrelated tracks.

## Master checklist correction — Section 2c

**Original frozen panel: 11/12 strict independent origins; one short; essentially resolved but not yet cleared.**

**Rescue-v3 structural cohort: 14/12 origins; availability cleared, untouched-holdout status not restored.**

This was a localized completion gap, not a return to initial data discovery.
The existing-data-only audit found no hidden rescue. A fixed, preregistered
same-provider basket subsequently cleared structural availability without
ranking/model outcome inspection. See `docs/master_end_state_checklist.md`,
`data/governance/holdout_c_existing_data_gap_audit.json`, and
`data/governance/holdout_c_alpaca_cohort_rescue_v3.json`.

## Corrected price-only cohort finding

The reconstructed Alpaca SIP panel contains 744 full regular sessions from
2023 through 2025 for the frozen nine-symbol scope. Every accepted session uses
the existing 391-bar price-only rule.

The original cohort constructor counted a ticker as part of an origin when it
was eligible on the origin date. That was insufficient: the same ticker must be
eligible across the complete 32-session context and 20-session outcome window.
The corrected constructor intersects ticker membership across all 52 sessions.

Under the corrected rule, the strongest deterministic panel contains:

- eight or more common symbols per accepted window;
- 11 strict, non-overlapping 52-session origins;
- 12 origins required;
- no ranking or model outcome used to select the windows;
- no gate, ticker, or origin requirement changed.

The subsequent rescue-v3 basket clears structural availability at 14 origins,
but it cannot retroactively make the already-explored 2023–2025 period untouched.
Consequently the factor, expanded-model, and full-backtest close-outs remain in
force for previously explored formulations. A prior pooled factor screen remains
exploratory only and does not qualify as confirmatory evidence.

## Eight-track status

### 1. Price-only Qlib/RD-Agent factor discovery

**State:** `closed_data_insufficient`

The original frozen cohort has 11 of the required 12 independent origins.
Rescue v3 supplies 14 structurally valid origins, but prior exploratory use means
it is not an untouched final holdout for this factor branch. The governed runner
therefore retains the skip close-out.

### 2. Expanded price-only model study

**State:** `closed_data_insufficient`

TimesFM and Kronos were not rerun. Rescue v3 resolves structural availability,
but does not restore an untouched period for these previously explored model
formulations. Kronos retains its existing context-only status; no prior model
verdict was reopened.

### 3. Full-scale price-only backtesting

**State:** `closed_data_insufficient`

No VectorBT selection or LEAN strategy job was run. Structural availability is
now sufficient under rescue v3, but the period cannot be relabeled untouched
after prior exploratory use. The native LEAN build remains infrastructure only
and continues to require a separately frozen, research-grade replication package.

### 4. Public event ingestion and FinBERT triage

**State:** `completed_real`

The initial bounded run captured 25 current public headline-metadata events from
the Yahoo Finance search feed. A repeat run reused 22 provider identities and
scored three genuinely new headlines, leaving 28 governed events in the local
registry. Local FinBERT is pinned to:

- model: `ProsusAI/finbert`
- revision: `4556d13015211d73dccd3fdd39d39232506f3e43`

SEC EDGAR was blocked by HTTP 403 from this VM, GDELT returned provider
rate-limiting, NewsAPI had no configured key, and Claude structured extraction
had no configured endpoint. Each source was recorded separately; none was
silently replaced or claimed as operational.

The event role is fixed to explanation and risk flagging. Directional signal and
execution use are prohibited. Repeated feed documents are reused by
`(source, external_id)` and are not rescored into duplicate governance events.

### 5. Real forecast anomaly attribution

**State:** `closed_data_insufficient`

No validated forecast survived the unchanged cohort requirement. Therefore no
context-only or synthetic forecast was converted into a fake anomaly event. The
anomaly registry remains empty by design.

### 6. Bounded repair actions

**State:** `completed_infrastructure`

The repair executor supports only:

- bounded transient-delivery retries;
- derived-cache reconstruction;
- checksum recomputation and verification.

The first real run verified the latest event artifact and rebuilt its derived
summary cache. Both actions produced immutable incident records. Repair cannot
change cohorts, parameters, data sources, gate thresholds, promotion state, or
execution authority.

### 7. Per-job safe scheduling

**State:** `completed_infrastructure`

A local guarded daemon is active because this VM has no usable user-systemd bus
and its user crontab is inaccessible. The daemon has a PID file, a file lock,
bounded command timeouts, persisted state, and a minimum five-minute loop
interval.

Only four jobs are allowlisted:

1. public event ingestion — daily;
2. bounded repair audit — daily;
3. research infrastructure audit — daily;
4. master end-state refresh — hourly.

Factor research, model studies, backtests, paper trading, and live execution are
explicitly absent from the scheduler.

Operator commands:

```bash
.venv-research-py312/bin/python cipher-system/scripts/manage_safe_scheduler.py status
.venv-research-py312/bin/python cipher-system/scripts/manage_safe_scheduler.py start --interval-seconds 3600
.venv-research-py312/bin/python cipher-system/scripts/manage_safe_scheduler.py stop
```

### 8. Operator research-status UI

**State:** `completed_infrastructure`

The local read-only app now exposes:

- `GET /api/research-status`
- a visible **Research Status** navigation view;
- all eight track states and reasons;
- evidence summaries;
- real event records and FinBERT flags;
- an explicit `EXECUTION AUTHORITY: NONE` indicator.

The UI reads governed local artifacts. It does not trigger research runs,
promotion, paper simulation, or broker actions.

## Stable status artifact

The authoritative machine-readable summary is:

`cipher-system/data/governance/master_end_state_status.json`

It reports each track as one of:

- `completed_real`
- `completed_infrastructure`
- `closed_data_insufficient`
- `pending`

A track is closed only by one of the first three states. The current artifact
reports all eight tracks closed. Live execution remains absent, and the maximum
governance state remains `LIVE_REVIEW_REQUIRED`.
