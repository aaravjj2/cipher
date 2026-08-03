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
