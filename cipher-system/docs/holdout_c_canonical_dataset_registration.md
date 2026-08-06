# Holdout C Canonical Dataset Registration

Date: 2026-08-04 UTC

## Verdict

`COMPLETED_EXACT_MATCH`

The original nine-symbol Holdout C price-only panel is now represented by one
frozen canonical dataset with complete links to all 744 registered partition
objects. Registration reproduced the protected 11/12 independent-origin result
exactly. It did not close or reinterpret the one-origin shortfall.

## Protected baseline

The registration answer key was loaded before any registry mutation from:

- `data/market_quality/alpaca_holdout_c_price_only_scope_20260803T235944Z.json`
- `data/governance/holdout_c_alpaca_cohort_construction_20260803T235952Z.json`

The protected fields were:

| Field | Protected value |
|---|---:|
| Partition identities/hashes | 744 |
| Selected block | 2023-06-06 through 2025-12-31 |
| Sessions in selected block | 638 |
| Minimum common tickers | 8 |
| Strict independent origins | 11 |
| Required origins | 12 |
| Ranking/model outcomes evaluated | false |
| Volume evaluated | false |
| Gate relaxed | false |

The stable machine-readable registration report includes the complete verbatim
744-entry path-to-SHA-256 mapping and the SHA-256 identities of both baseline
artifacts.

## Partition registration

Before the transaction began, the canonical registry contained no records for
this dataset:

| Entity | Before |
|---|---:|
| Dataset manifests | 0 |
| Raw-object records | 0 |
| Dataset-to-raw links | 0 |

For each protected Parquet partition, registration verified:

- the file still existed at its protected identity;
- its current SHA-256 exactly matched the baseline scope artifact;
- its same-session immutable Alpaca raw response existed;
- the raw response identified `Alpaca SIP` and the matching session date;
- the provider receipt timestamp was timezone-aware;
- the Parquet row count and file size were recorded;
- the source raw-response SHA-256 was recorded;
- the normalizer identity was recorded.

Exactly 744 `RawObjectManifest` records were inserted. Their disposition is
`frozen_snapshot`, reflecting that the registered objects are normalized,
append-closed Parquet partitions rather than mutable operational files.

## Frozen dataset

Canonical dataset ID:

`ds_380c76da95f0c3787529c6b8`

Registry result:

| Entity | After |
|---|---:|
| Dataset manifests | 1 |
| Raw-object records | 744 |
| Dataset-to-raw links | 744 |

The dataset is marked frozen and records:

- source: `Alpaca SIP`;
- schema: `alpaca_sip_holdout_c_1m_parquet_v1`;
- panel symbols: 9;
- partitions/sessions: 744;
- total rows: 2,617,971;
- selected-block sessions: 638;
- immutable raw-object IDs: 744.

## Normalizer and code identity

Normalizer implementation SHA-256:

`1166e9b90d3b3d8df5a553450861fa5ed105e40038bc88ad63138ecab0f6efe1`

The earliest repository commit containing those exact normalizer script bytes
is:

`dada8289efb34c3a4457c05aa660a8cfb294b279`

The original ingest artifacts did not record the runtime Git `HEAD`. Therefore
the report describes this honestly as a reconstructed producer-source identity:
the commit is proven to contain the exact normalizer implementation, but it is
not claimed as direct evidence of the process's checked-out `HEAD` at every
partition's creation time.

The registration implementation itself was uncommitted during execution. It is
therefore pinned in the report by exact SHA-256 values for each participating
source file, together with base commit:

`020dff6aeb25c89b2d32854edad7dd0ec67342fe`

This distinction prevents the base commit from being misrepresented as the
identity of uncommitted registration code.

## Atomic canonical re-derivation

The registry bundle operation used one SQLite transaction. Within that open
transaction, after the raw objects, dataset, and links were visible but before
commit, a validator:

1. resolved all 744 partition paths through the dataset-to-raw links;
2. ran the original `build_scope` implementation used by
   `scope_alpaca_holdout_c_price_only.py`;
3. ran the original `build_cohort_payload` and
   `construct_candidate_blocks` implementation used by
   `construct_alpaca_holdout_c_cohort.py`;
4. compared the canonical result to the protected answer key.

Compared fields included:

- partition count;
- the complete partition identity/SHA-256 mapping;
- provider, feed, and panel;
- selected-block start and end;
- selected-block session count;
- minimum common ticker count;
- strict independent origin count;
- required origin count;
- every origin window;
- hashes of all daily eligibility results;
- hashes of all common-eligible-by-day results;
- outcome, volume, and gate-boundary flags.

Every comparison passed. Had any field differed, the validator would have raised
and SQLite would have rolled back the complete 744-object bundle, dataset, links,
and audit rows.

## Status impact

The stable post-merge verification now reports:

- verdict: `PASSED`;
- `known_canonical_lineage_gap: false`;
- Holdout C dataset manifests: 1;
- Holdout C raw objects: 744;
- Holdout C dataset-to-raw links: 744;
- registry integrity: `ok`.

The master end-state status now separately reports:

- canonical frozen lineage complete: true;
- strict independent origins: 11;
- required origins: 12;
- origin gap: 1;
- registration closes origin gap: false.

## Remaining open issue

The original-panel independent-origin result remains:

`11/12 — one origin short`

This registration task made that result durably provable. It did not add data,
change filters, alter the price-continuity rule, use the rescue cohort, evaluate
returns, or manufacture a twelfth origin. Closing the one-origin shortfall
remains a separate future research-data task.

## Evidence

Stable registration artifact:

`data/governance/holdout_c_canonical_dataset_registration.json`

The report contains:

- all 744 protected partition identities and hashes;
- all 744 raw-object IDs and source-raw hashes;
- the frozen dataset manifest;
- before/operation/after registry counts;
- the exact field-by-field canonical comparison;
- normalizer and registration code identities;
- explicit confirmation that the origin gap remains open;
- explicit no-volume, no-outcome, no-gate-relaxation, and no-execution flags.
