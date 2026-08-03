# Current-Era Holdout C Protocol

The blocked 2017-2019 Holdout C recovery is retired as a final-holdout target.
It remains immutable historical evidence of an insufficient cohort and must not
be combined with the replacement cohort.

## Time Partition

- `2020-01-01` through `2022-12-31`: development only. It may support schema
  validation and factor design but cannot provide final promotion evidence.
- `2023-01-01` through `2025-12-31`: candidate final Holdout C. It must be
  acquired from one source and frozen before any ranking/backtest outcomes are
  inspected.
- `2026-01-01` onward: prospective-only. It cannot be used for historical
  model selection or parameter tuning.

## Current price-only status

The corrected original nine-symbol panel yields **11 of 12 strict independent
origins** in the strongest continuous block: one short, essentially resolved,
but not cleared. The original gap is localized to split-like price
discontinuities for NVDA on `2024-06-10` and XLE on `2025-12-05`; both sessions
contain 391 bars. An existing-data-only audit found no unused same-provider
local store that validly repairs both dates or contributes a fully covered
additional ticker.

A preregistered DIA rescue failed because many DIA sessions had fewer than 391
one-minute bars. A subsequent fixed-basket rescue preregistered `AMD, AMZN,
GOOGL, META, TSLA` before retrieval and evaluated all five on availability and
price continuity only. AMD, AMZN, GOOGL, and TSLA passed all 744 sessions; META
passed 743. The resulting same-provider structural cohort supplies **14 strict
independent origins** with at least nine common tickers and no gate relaxation,
source mixing, volume use, or ranking/model outcome inspection.

This clears structural cohort availability. It does not restore an untouched
final holdout because the 2023–2025 period was previously used for exploratory
research. The allowed claim is therefore
`structural_cohort_eligibility_only_not_restored_untouched_holdout`.

Evidence:

- `data/governance/holdout_c_existing_data_gap_audit.json`
- `data/governance/holdout_c_rescue_v3_preregistration.json`
- `data/governance/holdout_c_alpaca_cohort_rescue_v3.json`

## Unchanged Eligibility

The candidate final holdout must still have one qualified source, at least
eight common eligible tickers, and at least twelve non-overlapping origins of
32 context sessions plus 20 outcome sessions. Volume-sensitive strategies use
the full session-and-volume gate. Price-only forecast studies remain separate
and can never supply promotion evidence for this protocol.

## Acquisition Order

Use existing Alpaca SIP access first. The first task is a read-only coverage
and completeness audit of the frozen nine-symbol panel over 2023-2025. A
three-year block is required because twelve strict 52-session origins require
at least 624 eligible sessions; two trading years cannot satisfy that minimum.
Only if
that audit fails may a separate source be considered; no source may be mixed
into the final cohort.

## Independent Volume Reference

Polygon/Massive is excluded from this recovery path because its entitlement
does not cover the required requests. The reference-volume source may only be
used to reconcile volume, never to patch or replace Alpaca price data. It must
provide independent one-minute share volume that can be filtered exactly to
09:30-16:00 ET for the frozen nine-symbol 2023-2025 panel. Databento metadata
and no-cost estimate checks come first; FirstRate is sample/quote only if that
check cannot pass. The 5% full-gate threshold is unchanged.

### Feasibility Result (2026-08-02)

Databento is not eligible for a no-spend pilot: its documented minute product
(`EQUS.MINI`) aggregates component ATS and Reg NMS venues but does not prove
full SIP-comparable coverage. Its explicitly consolidated `EQUS.SUMMARY`
product is daily-only and therefore cannot be filtered to the regular session.

FirstRate's free AAPL and SPY samples verified CSV `timestamp,open,high,low,
close,volume` data, US Eastern minute-start stamps, individual-share volume,
and enough out-of-hours coverage to filter exactly to the 391-bar
09:30-16:00 regular session. Its historical panel is purchasable, so it is
excluded from this no-purchase path.

London Strategic Edge is the next candidate. Its published free plan offers a
key, REST access, and bounded CSV/Parquet downloads. It must still provide an
API key and prove its minute-volume semantics are comparable to Alpaca SIP
before any immutable 2023 pilot. Until then, the full volume gate remains
blocked; no volume-sensitive backtest, ranking outcome, paper trial, or vendor
patch is permitted.

### LSE Pilot Result (2026-08-03)

The two-session June 2023 pilot is rejected as an independent volume source.
After identical `09:30 <= America/New_York < 16:00` filtering, June 1 passed
4/9 symbols and June 2 passed 3/9 at the unchanged 5% threshold. The same
symbols repeatedly showed large mismatches, so no broader download is
authorized by this protocol. Raw responses and manifests remain isolated under
`data/reference_volume`; the Alpaca price dataset and full gate are unchanged.

The free Hugging Face/Finnhub-derived archive was also tested on June 1 and 2.
June 1 matched volume within 5% for 9/9 symbols, although GE had only 387
minute bars. June 2 matched only 5/9, with repeated material mismatches for
AAPL, MSFT, NVDA, and QQQ. It is therefore retained only for supplemental
price-only research and rejected as the independent volume reference.

### Provider-Neutral Reconciliation Infrastructure (2026-08-03)

The rejected pilots have been replaced with one provider-neutral import and
reconciliation path. Authorized minute-volume CSV evidence must be stored
immutably under `data/reference_volume/raw`, hashed, mapped with an explicit
source timezone and timestamp convention, and validated against the unchanged
391-bar regular-session rule before comparison. The pipeline reads only
provider timestamp, symbol, and share volume; provider prices are ignored and
cannot replace or patch Alpaca data. Invalid, duplicated, incomplete, zero, or
missing reference sessions fail closed. The relative-difference threshold
remains exactly 5%.

This infrastructure does not unblock the full gate by itself. The status
remains `blocked_reference_volume_access_after_free_sources_rejected` until an
independent authorized source supplies valid evidence for the frozen panel.
See `docs/reference_volume_reconciliation_pipeline.md`.
