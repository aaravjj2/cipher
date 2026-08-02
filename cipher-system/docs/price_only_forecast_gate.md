# Price-Only Forecast Gate

This gate is exclusively for evaluating forecasts that use and score prices
only. It requires exactly 391 local regular-session minute bars and rejects a
daily close ratio to the prior session at or below 0.5 or at or above 2.0.
Those conservative bounds reject raw split-like discontinuities without
adjusting prices or assuming corporate-action availability.

The gate does not reconcile volume. A result admitted through it must carry
`price_forecast_research_only_no_volume_features` and must never be used for
volume features, liquidity filtering, sizing, strategy promotion, or trading.
Those uses continue to require the unchanged full session-and-volume gate in
`core.research_platform.market_quality.require_eligible_market_day`.

## Initial Scope

The 2026-08-02 catalog scan considered 15 ingested monthly files and found
124 independent stretches of at least 52 sessions across 87 tickers. A stretch
needs 52 sessions to supply 32 price-context sessions and 20 realized sessions.
The scanner splits at catalog gaps, missing/incomplete sessions, and split-like
close changes. This is enough for the pre-registered 18-case model study below;
it does not validate a strategy or authorize any non-price-only use.

The study uses AMD, MSFT, and SPY, each with origins 2020-07-15, 2020-08-13,
and 2020-09-14. Every origin uses 32 close-only context sessions and evaluates
both 5 and 20 sessions; each ticker's 20-session realized ranges do not
overlap. TimesFM receives close only. Kronos-mini receives OHLC prices only;
its adapter fills omitted volume and amount with zeros. No volume feature,
volume-based filter, or volume evaluation is permitted.

TimesFM uses `google/timesfm-2.5-200m-pytorch` locally on CPU with
`max_context=32`, horizons 5 and 20, and the package's documented default
forecast configuration. Its native p10/p90 channels are 1 and 9. Kronos uses
`NeoQuasar/Kronos-mini` with `NeoQuasar/Kronos-Tokenizer-2k`, CPU,
`T=0.6`, `top_p=0.9`, a fixed-seed N=10 internal ensemble for its point
forecast, and ten separately seeded paths (42-51) only to form an empirical
uncertainty band. All outputs remain research-only.

## Study Result

The reproducible scope result is
`data/market_quality/price_only_forecast_scope_20260802T181228Z.json`. It
considered these 15 local files: 1992-01, 2008-10, 2015-06, 2020-06 through
2020-12, 2022-02 through 2022-05, and 2026-03. It found 124 valid stretches
across 87 tickers, so the relaxed gate materially solves the data-availability
problem for price-only model verification.

The pre-registered 18-case output is
`data/market_quality/price_only_model_study_20260802T1818Z.json`.

| Model | Point beats naive | p10-p90 captures | Mean terminal absolute error |
| --- | --- | --- | --- |
| TimesFM 2.5 200M | 6/18 | 11/18 | 7.978 |
| Kronos-mini | 9/18 | 6/18 | 7.277 |

At five sessions, TimesFM was 2/9 against naive with 6/9 interval captures;
Kronos-mini was 5/9 with 3/9 captures. At 20 sessions, TimesFM was 4/9 with
5/9 captures; Kronos-mini was 4/9 with 3/9 captures. These cases use three
tickers and three origins each, so within-ticker origins share context and are
not independent evidence. The study rejects neither model outright, but it
does not establish an actionable point edge or calibrated uncertainty.

The price-only gate was a real fix for forecast research availability, but not
for deployment: both models remain context-only and require a larger,
cross-regime, non-overlapping study before any scheduling, persistence, sizing,
promotion, or trading use. The full session-and-volume gate is unchanged.

## Holdout C Independent Origins

For Holdout C ranking research, independence is enforced in
`scripts/construct_alpaca_holdout_c_cohort.py`. A candidate day belongs to a
block only when at least eight tickers are price-only eligible on that same
day. A strict independent origin consumes a non-overlapping 52-session slice:
32 context sessions followed by 20 outcome sessions. Origins are generated at
offsets `0, 52, 104, ...` inside one contiguous eligible block; the cohort gate
uses the strongest single block and does not add origins from separate blocks.
Changing ticker alone therefore cannot create an independent origin, and an
additional ticker helps only if it repairs the common daily universe for every
session in the candidate block. This definition applies before any outcomes
are evaluated.
