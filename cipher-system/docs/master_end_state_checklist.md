# Master End-State Checklist — Repository Status Mirror

This file records repository-side status corrections to the external master
checklist. Sections not reproduced here retain their prior wording and status.
The machine-readable architecture audit remains separate from this checklist.

For implementation order and acceptance gates, use the [consolidated Cipher
execution roadmap](cipher_product_and_hackathon_roadmap_2026-08-17.md#consolidated-execution-plan--revision-2). This checklist is an evidence mirror,
not a second competing backlog.

## 2. Research evidence and data readiness

### 2c. Current-era price-only Holdout C

**Base-panel correction: 11/12 strict independent origins; one short; essentially resolved but not yet cleared.**

**Current rescue-v3 status: structural cohort eligibility cleared at 14/12 origins, but the prior exploratory use of the period means this does not restore an untouched final holdout.**

The `11/12` correction remains the accurate status of the original frozen nine-symbol panel. It was a small, localized gap and not a return to initial data discovery. The original Alpaca SIP panel contains:

- one provider;
- the frozen 2023–2025 period;
- nine symbols;
- 744 sessions represented in the scope artifact;
- a strongest continuous eligible block of 638 sessions;
- at least eight common eligible symbols in 11 strict, non-overlapping
  `32-context + 20-outcome` windows;
- no volume features or volume evaluation;
- no gate relaxation or vendor mixing.

The corrected window-wide eligibility test requires the same ticker set across
all 52 sessions. Under that rule, the strongest block supplies 11 of the 12
required origins.

**Canonical lineage status: complete.** The protected 744-partition panel is now
registered as one frozen dataset (`ds_380c76da95f0c3787529c6b8`) with exactly
744 canonical raw-object records and 744 dataset-to-raw links. Every partition
hash was checked against the protected scope artifact before registration. The
transaction then reran the original scope and 52-session cohort implementations
through the registered manifest and committed only after reproducing the same
selected block, 638 sessions, minimum eight common tickers, and 11/12 origins.
This closes the provability/lineage gap only; it does not close the one-origin
shortfall.

The remaining gap is concentrated in two otherwise complete 391-bar sessions:

- `NVDA` on `2024-06-10`, rejected by the unchanged split-like close-ratio
  rule (`0.100645...`);
- `XLE` on `2025-12-05`, rejected by the same rule (`0.497831...`).

Repairing only one does not produce 12 origins. Repairing both under a valid,
predeclared price-continuity contract—or adding one already-eligible,
same-provider symbol with complete coverage across the affected windows—would
produce 12. Neither action may use ranking outcomes, mix vendors, infer missing
prices, or silently change the frozen gate.

The first recovery step was an inventory of already-ingested, unused,
same-provider data. That inventory found no hidden rescue and sourced no new
market data. A fixed DIA rescue was then preregistered and failed because DIA
frequently had fewer than 391 one-minute bars.

A second rescue preregistered the complete candidate basket `AMD, AMZN, GOOGL,
META, TSLA` before retrieval, required every candidate to be evaluated, and
forbade any return/model/ranking outcome analysis. The resulting same-provider
availability audit found:

- AMD: 744/744 eligible sessions;
- AMZN: 744/744;
- GOOGL: 744/744;
- META: 743/744;
- TSLA: 744/744.

The resulting structural cohort spans all 744 sessions and supplies 14 strict,
non-overlapping origins with at least nine common tickers. This closes the
single-origin **availability** gap without changing the gate or mixing vendors.
It does not erase the fact that the original nine-symbol period had already
been used for exploratory research, so the allowed claim remains structural
cohort eligibility—not a newly untouched final holdout.

Evidence:

- `data/market_quality/alpaca_holdout_c_price_only_scope_20260803T200831Z.json`
- `data/governance/holdout_c_alpaca_cohort_construction_20260803T201001Z.json`
- `data/governance/holdout_c_existing_data_gap_audit.json`
- `data/governance/holdout_c_rescue_v2_preregistration.json`
- `data/governance/holdout_c_rescue_v3_preregistration.json`
- `data/governance/holdout_c_alpaca_cohort_rescue_v3.json`
- `data/governance/holdout_c_canonical_dataset_registration.json`
- `data/governance/post_merge_verification.json`

## Interpretation

The corrected `11/12` result was a more accurate measurement of the same system,
not a regression in the system itself. Rescue v3 then improved the structural
availability measurement to 14 origins without changing the interpretation of
prior exploratory contamination. The bounded eight-track work package, cohort
availability, untouched-holdout status, and original architecture audit remain
separate status dimensions.
