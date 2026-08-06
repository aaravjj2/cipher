# Focused Post-Merge Verification

Date: 2026-08-03/04 UTC

This verification was run after the source/runtime unification and before any
new research or feature work. It addressed only three questions:

1. Do the four systemd-managed Cipher services actually restart through the
   legacy-path symlink into the canonical Git checkout?
2. Does a fresh post-merge Holdout C 52-session recount still produce the
   original-panel result of 11/12 strict independent origins?
3. Did the point-in-time event timestamp rules and formal eight-layer naming
   corrections survive the merge?

## Verdict

`PASSED`

All operational and semantic verification checks passed. The original audit was
initially qualified because the Holdout C panel was reproducible from 744
Parquet partitions but absent from the canonical registry. The targeted
canonical registration completed on 2026-08-04 with an exact match to the
protected 11/12 baseline. The lineage-only refresh did not rerun the earlier
service restart or route checks; it preserved that evidence and refreshed only
the registry-backed gap state.

## 1. Service restart verification

A direct `systemctl restart` was attempted first. PolicyKit rejected the command
because the non-interactive session did not have authorization.

The four service processes are owned by the `aarav` user and all four units use
`Restart=always`. The verification therefore sent `SIGTERM` to each unit's
current `MainPID` and observed systemd recover the processes through their
configured service definitions.

| Service | PID before | PID after | State after | Resolved process CWD |
|---|---:|---:|---|---|
| `cipher-core.service` | 3206231 | 3296922 | active/running | canonical `cipher-system/` |
| `cipher-web.service` | 3206233 | 3296923 | active/running | canonical `cipher-system/app/` |
| `cipher-gex.service` | 3206658 | 3297280 | active/running | canonical `cipher-system/` |
| `cipher-tradier.service` | 3206493 | 3297113 | active/running | canonical `cipher-system/` |

After recovery:

- Core port 8282 was listening.
- Web port 8283 was listening.
- Core `/health` returned HTTP 200.
- Core `/api/research-status` returned HTTP 200.
- Web `/api/health` returned HTTP 200.
- Web `/api/research-status` returned HTTP 200.
- A live SPY SIP quote returned HTTP 200.
- The scanner universe returned HTTP 200 with 546 symbols.

This is an actual service recovery test, not only a symlink inspection.

## 2. Post-merge Holdout C recount

The price-only scope and cohort construction were rerun from the unified runtime
for 2023-01 through 2025-12.

Fresh artifacts:

- `data/market_quality/alpaca_holdout_c_price_only_scope_20260803T235944Z.json`
- `data/governance/holdout_c_alpaca_cohort_construction_20260803T235952Z.json`

Fresh result:

| Field | Result |
|---|---:|
| Current normalized partitions | 744 |
| Recorded normalized partitions | 744 |
| Selected block | 2023-06-06 through 2025-12-31 |
| Sessions in selected block | 638 |
| Minimum common tickers | 8 |
| Strict independent origins | 11 |
| Required origins | 12 |
| Gap | 1 |
| Ranking outcomes evaluated | false |
| Volume evaluated | false |
| Gate relaxed | false |

The original-panel finding therefore remains:

> 11/12 strict independent origins; one short; essentially resolved but not yet
> cleared.

The merge did not alter this number.

### Registry reconciliation

The canonical merged registry has integrity status `ok` and now contains:

- Holdout C dataset manifests: 1
- Holdout C raw-object entries: 744
- Holdout C dataset-to-raw links: 744

Dataset ID:

`ds_380c76da95f0c3787529c6b8`

The registration transaction exposed the newly inserted dataset and links to a
pre-commit validator, which reran the original price-only scope implementation
and the original 52-session cohort implementation. It committed only after the
partition identities/hashes, selected block, 638-session count, minimum common
ticker count, all origin windows, and 11/12 result matched exactly.

Registration evidence:

`data/governance/holdout_c_canonical_dataset_registration.json`

The canonical-lineage gap is therefore closed. The separate one-origin research
gap remains open: the original panel still supplies 11 origins against 12
required.

## 3. Point-in-time timestamp verification

The merged source still contains the corrected observation rule:

```python
available = max(publication_time, actual_observation_time)
```

The news data model also rejects:

- `received_at < publication_time`
- `available_at < received_at`

The merged registry contains 28 news events with:

| Invariant | Violations |
|---|---:|
| `received_at < publication_time` | 0 |
| `available_at < received_at` | 0 |

Twenty-five events were observed after publication. Three have receipt time
equal to publication time; equality is valid under the corrected rule and is
not evidence of a timestamp inversion.

## 4. Eight-layer and naming verification

The active topology contains exactly eight layers numbered 1 through 8.

- Layer 4: `attribution_and_anomaly_engine`
- Layer 7: `shadow_and_paper_execution`
- Layer 8: `evidence_feedback_loop`
- `SevenLayerStackSpec` remains only a compatibility alias to
  `EightLayerStackSpec`.
- Boundary validation reports no violations.

The verification found one residual warehouse provenance label:

```text
causal_attribution_engine
```

Although the formal layer name had been corrected, this remaining label still
implied causal identification. It was renamed to:

```text
attribution_and_anomaly_engine
```

A regression assertion now protects the warehouse-row source label.

## Machine-readable evidence

Stable artifact:

`data/governance/post_merge_verification.json`

Original timestamped artifact:

`data/governance/post_merge_verification_20260804T000257266878Z.json`

The stable artifact also records a later lineage-only refresh under a
`post_merge_verification_lineage_refresh_*.json` timestamped artifact. That
refresh explicitly states that service restart and route checks were not rerun.

The audit records:

- Pre- and post-restart PIDs
- Current systemd state and resolved process directories
- API route status
- Fresh Holdout C scope/cohort evidence
- Registry integrity and Holdout C lineage counts
- News timestamp invariants
- Eight-layer names, numbers, alias and boundary results
- No ranking/model outcomes
- No volume evaluation
- No execution authority

## Conclusion

The source/runtime merge survives an actual systemd recovery cycle, the
original-panel Holdout C result remains 11/12 after canonical re-derivation, and
the timestamp/topology corrections survived the merge. The complete frozen
lineage is now registered and the `known_canonical_lineage_gap` flag is false.
The one-origin shortfall remains a separate unresolved research-data issue.
