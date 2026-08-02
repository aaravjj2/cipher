# Cipher Model Weight Inference Addendum

Parsed records: 30

## What Was Calculated

The primary Cipher Model Scan completed and exposed ranked rows with ticker, rank, score, direction, major supports, major resistances, pull target, vacuum targets, and narrative read. I parsed these into a structured CSV and fit a first-pass standardized regression against the visible 0-100 score.

## Important Limitation

These are not exact proprietary weights. They estimate which visible output fields explain the displayed score. The true model likely uses raw GEX, VEX, OI, IV, volume, spread quality, distance-to-spot, ATR, VWAP, flow, and full-universe comparison features that are not visible in the result cards.

Visible-feature R^2: 0.322

## Standardized Coefficients

| Feature | Coefficient | Correlation to score |
|---|---:|---:|
| vacuum_count | -4.363 | -0.132 |
| near_gap_pct | 0.846 | -0.013 |
| pull_from_support_pct | -1.087 | 0.050 |
| stretch_from_support_pct | -0.036 | 0.037 |
| support_count | 5.023 | 0.221 |
| resistance_count | 0.000 | 0.000 |

## Top Parsed Rows

| Rank | Ticker | Direction | Score | Supports | Resistances | Pull Target | Vacuum Targets |
|---:|---|---|---:|---|---|---:|---|
| 1 | LAES | BULLISH | 82 | 2.5, 2.0 | 3.0, 3.5 | 3.0 | 3.0, 3.5 |
| 2 | PCG | BULLISH | 79 | 16.5, 16.0 | 17.5, 18.0 | 20.0 | 18.0, 19.0, 20.0, 21.0 |
| 3 | IOVA | BULLISH | 78 | 4.5, 4.0 | 5.5, 7.5 | 5.5 | 5.5, 6.0, 7.0, 7.5 |
| 4 | AXTI | BULLISH | 76 | 43.0, 40.0 | 49.5, 50.0 | 50.0 | 50.0, 55.0, 57.0, 60.0 |
| 5 | HIVE | BULLISH | 73 | 2.5, 2.0 | 3.0, 3.5 | 4.0 | 3.0, 3.5, 4.0 |
| 6 | NUAI | BULLISH | 73 | 4.0, 3.5 | 4.5, 5.0 | 4.5 | 4.5, 5.0, 5.5, 6.0 |
| 7 | CSCO | BULLISH | 72 | 109.0, 108.0 | 112.0, 114.0 | 120.0 | 115.0, 120.0, 125.0, 130.0 |
| 8 | CLOV | BULLISH | 71 | 4.0, 4.5 | 5.0, 5.5 | 5.0 | 5.0, 5.5, 6.0, 6.5 |
| 9 | WULF | BULLISH | 71 | 17.0, 16.0 | 18.5, 19.0 | 20.0 | 20.0, 22.0, 23.0, 25.0 |
| 10 | HIMS | BULLISH | 70 | 31.0, 30.5 | 33.0, 34.0 | 40.0 | 35.0, 36.5, 40.0, 45.0 |
| 11 | POET | BULLISH | 70 | 5.0, 7.0 | 7.5, 8.0 | 8.0 | 8.0, 9.0, 9.5, 10.0 |
| 12 | PYPL | BULLISH | 70 | 54.0, 53.0 | 57.0, 57.5 | 60.0 | 58.0, 60.0, 65.0, 70.0 |
| 13 | SOFI | BULLISH | 70 | 16.5, 16.0 | 17.5, 18.0 | 20.0 | 18.0, 20.0, 22.0, 25.0 |
| 14 | GILD | BULLISH | 69 | 129.0, 126.0 | 135.0, 136.0 | 150.0 | 135.0, 136.0, 140.0, 150.0 |
| 15 | HAL | BULLISH | 68 | 34.0, 33.0 | 35.5, 36.0 | 36.0 | 36.0, 37.0, 40.0, 50.0 |

## Next Data Needed For True Weight Recovery

- Full scanner CSV or API response for many completed runs.
- Raw option-chain features for every candidate: GEX, VEX, OI, volume, IV, bid/ask spread, delta/gamma/vega.
- Underlying features: spot, ATR, VWAP, session levels, distance to support/resistance, momentum.
- Flow features: last-5-minute net premium, sweep/block counts, bid/ask classification.
- Repeated captures across dates and scanner modes to fit stable rank-learning weights.