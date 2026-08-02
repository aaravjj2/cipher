# Expanded Price-Only Forecast Model Study

## Design

The frozen [pre-registration](../data/market_quality/expanded_price_only_preregistration_20260802T1827Z.json)
contains 54 cases: nine tickers, three origins each, and two horizons (5 and
20 sessions). Each ticker contributes at most six cases. Selection was based
only on price-only gate availability and a fixed diversification list. Cases
use close-only TimesFM input and OHLC-only Kronos-mini input; neither receives
volume or amount.

The three mechanically assigned pre-origin SPY regimes are bullish/low
(2020-07-15), mixed/medium (2022-03-17), and bearish/low (2022-04-18).
Origins are at least ten sessions apart. The April 2022 context overlaps the
March outcome, so the strict-independence subset excludes it.

## Earlier Study Audit

The earlier field described as `naive wins: 6/18` was mislabeled in prose: its
stored boolean was `timesfm_absolute_error < naive_absolute_error`. Therefore
it means **TimesFM won 6/18**, while persistence won 12/18; it does not mean
TimesFM won 12/18. The same strict comparison applies to both models here.
There were no ties, failures, missing predictions, or invalid comparisons in
the expanded study. MAE is terminal-target raw-price absolute error, averaged
per case; it is not per-timestep error.

## Results

| Subset | Model | Model / naive wins | MAE | p10-p90 coverage |
| --- | --- | --- | --- | --- |
| Full (54) | TimesFM | 16 / 38 | 11.245 | 57.4% |
| Full (54) | Kronos-mini | 28 / 26 | 8.221 | 27.8% |
| Strict (36) | TimesFM | 12 / 24 | 9.389 | 61.1% |
| Strict (36) | Kronos-mini | 12 / 24 | 8.072 | 27.8% |
| One case/ticker-regime (27) | TimesFM | 6 / 21 | 8.103 | 70.4% |
| One case/ticker-regime (27) | Kronos-mini | 12 / 15 | 5.202 | 33.3% |

The full summary contains case-level metrics, interval widths/scores, by-case
records, and 500-resample uncertainty intervals. TimesFM's paired
model-minus-naive error is positive in every full and strict bootstrap interval
(worse than persistence). Kronos's apparent full-sample advantage does not
survive the strict case bootstrap: its 95% interval crosses zero; the
ticker-cluster interval is negative in full data but positive in the strict
subset. Neither model therefore has stable evidence of a point edge.

TimesFM's higher coverage is not by itself a calibration win: nominal 80%
coverage is not reached in any main subset, and coverage must be interpreted
alongside its saved width and interval-score metrics. Kronos's empirical bands
under-cover severely.

## Verdict

Both models remain **inconclusive but worth further study**, and context-only.
TimesFM is not a research candidate because it loses to persistence across all
subsets. Kronos-mini is not a research candidate because its advantage weakens
under independence controls and its intervals are not decision-useful. The
highest-value next experiment is a larger, temporally separated price-only
panel with more distinct volatility regimes, followed by the same clustered
analysis.

The price-only gate was used only for forecast research. No volume feature was
used; the full session-plus-volume gate is unchanged. Neither model was wired
to sizing, promotion, execution, or trading.
