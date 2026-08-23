# Skew Map implementation brief — 2026-08-23

Source reviewed: “Build Your Own Skew Map — How to See What Options Traders
Are Actually Paying For (and the Weekend Version You Can Build Yourself).”

## Method retained

```text
raw skew        = mirrored 25-delta put IV - mirrored 25-delta call IV
normalized skew = raw skew / ATM IV
map x-axis      = aligned one-month underlying return
map y-axis      = raw skew
```

Positive skew means downside protection is relatively expensive. Negative skew
means upside calls are relatively expensive. Equal-delta contracts are used
because equal-dollar or equal-percent strikes do not represent equal
probability. Raw volatility-point differences are the primary cross-sector
comparison; normalized skew is secondary and is safer within similar names.

## Quadrants

| One-month return | Raw skew | Research label | Interpretation |
| --- | --- | --- | --- |
| Negative | Negative | Contrarian bid | Price fell while calls are relatively bid; investigate, do not auto-buy. |
| Positive | Negative | Chase | Price rose while calls are relatively bid; crowding/chase risk. |
| Positive | Positive | Hedged rally | Price rose while puts remain relatively bid; protection is present. |
| Negative | Positive | Fear | Price fell while puts are relatively bid; downside concern dominates. |

These are research buckets, not predictions, entries, or performance claims.

## Data contract implemented

- Latest stored OPRA-derived surface per ticker from `option_history.sqlite`.
- Front-expiry ATM IV and mirrored 25-delta put/call IV.
- Underlying daily closes filtered so no bar occurs after the option observation.
- Up to 22 aligned sessions for the one-month return.
- IV coverage, quote coverage, median spread, contract count, feed, expiration,
  observation time, and stored-session count.
- Missing IV, skew, or aligned price stays `null`; no zero filling.
- A 100-volatility-point absolute-skew sanity ceiling marks observations suspect.
- Fewer than 20 stored sessions is visibly provisional.

## Required follow-through

- Continue one canonical close-adjacent surface capture per New York session.
- Treat 20 sessions as only provisional and 60+ as a more usable history.
- Attach the earnings calendar for the selected expiration; event premium can
  dominate sentiment.
- Add week-over-week change and sector breadth only after enough homogeneous
  observations exist.
- Validate outliers against contract-level quotes before using the map to choose
  research priorities.
- Never feed a quadrant directly into order submission or strategy promotion.

## Known limits on 2026-08-23

The local store contains 26 tickers but only five distinct recent market
sessions. The guest surface deliberately uses a bounded 12-name liquid universe.
Every displayed observation is therefore `PROVISIONAL`. The map is useful for
inspection and capture validation, not for a mature historical edge claim.
