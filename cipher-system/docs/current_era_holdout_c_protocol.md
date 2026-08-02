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
