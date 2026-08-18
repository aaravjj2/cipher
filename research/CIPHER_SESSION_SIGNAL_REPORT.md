# Cipher Session Signal Accuracy Report

**Generated:** 2026-07-24T23:36:15.961856Z
**Source:** `cipher_session_log.csv` (381 rows, 5-second polling)
**Date:** 2026-07-24
**Time range:** 2026-07-24T16:18:59Z to 2026-07-24T18:15:20Z
**Intraday data:** 5-minute bars from Alpaca (SIP feed)
**Unique signals extracted:** 23
**Episodes (merged consecutive same-dir):** 19
**Signals with price data:** 18
**Score anomalies:** 1 signals with score outside 0-100 range
  - GD at 2026-07-24T18:00:20: score=251.0

---

## Executive Summary

| Metric | Value |
|--------|-------|
| Total unique signals | 23 |
| Signals with price data | 18 |
| Targets hit | 7 (38.9%) |
| Invalidated (stop hit) | 3 (16.7%) |
| Timeout (neither hit in 2h) | 8 (44.4%) |
| Average PnL (net of costs) | -0.2836% |
| Median PnL (net of costs) | -0.4188% |
| Positive PnL signals | 6/18 (33.3%) |
| Expectancy (avg PnL per signal) | -0.2836% |
| Profit factor | 0.34 |
| Avg winning signal | +0.4348% |
| Avg losing signal | -0.6428% |
| Transaction cost deducted | 0.20% per signal |

## Signal Accuracy by Ticker

| Ticker | Signals | Target Hit | Invalidated | Timeout | Hit Rate | Avg PnL% |
|--------|---------|------------|-------------|---------|----------|----------|
| AAPL | 3 | 0 | 0 | 3 | 0% | -0.6681% |
| AMD | 3 | 2 | 1 | 0 | 67% | -0.2638% |
| AMZN | 3 | 1 | 0 | 2 | 33% | -0.3525% |
| GOOGL | 3 | 0 | 0 | 3 | 0% | -0.3198% |
| IBIT | 1 | 1 | 0 | 0 | 100% | +0.2700% |
| MU | 2 | 2 | 0 | 0 | 100% | +0.6541% |
| NVDA | 3 | 1 | 2 | 0 | 33% | -0.6235% |

## Signal Accuracy by Direction

| Direction | Signals | Target Hit | Invalidated | Timeout | Hit Rate | Avg PnL% |
|-----------|---------|------------|-------------|---------|----------|----------|
| BEARISH | 7 | 5 | 0 | 2 | 71% | +0.2618% |
| BULLISH | 11 | 2 | 3 | 6 | 18% | -0.6307% |

## Signal Accuracy by Score Bucket

| Score Range | Signals | Target Hit | Invalidated | Timeout | Hit Rate | Avg PnL% |
|-------------|---------|------------|-------------|---------|----------|----------|
| 90-100 (High) | 12 | 5 | 2 | 5 | 42% | -0.1741% |
| 70-89 (Mid) | 3 | 2 | 1 | 0 | 67% | -0.3373% |
| 50-69 (Low) | 3 | 0 | 0 | 3 | 0% | -0.6681% |

## Signal Accuracy by Setup Type

| Setup Type | Signals | Target Hit | Invalidated | Timeout | Hit Rate | Avg PnL% |
|------------|---------|------------|-------------|---------|----------|----------|
| BREAKDOWN CONTINUATION | 2 | 2 | 0 | 0 | 100% | +0.4269% |
| CEILING REJECTION | 1 | 0 | 0 | 1 | 0% | -0.1218% |
| FLOOR BOUNCE | 7 | 0 | 3 | 4 | 0% | -0.8293% |
| REJECTION REVERSAL | 8 | 5 | 0 | 3 | 62% | -0.0040% |

## High-Confidence Signal Analysis (Score >= 90)

- **Signals:** 12
- **Target hit rate:** 5/12 (41.7%)
- **Invalidation rate:** 2/12 (16.7%)
- **Average PnL:** -0.1741%
- **Positive PnL:** 4/12 (33.3%)

- **High-conf BULLISH:** 6 signals, 1 hits, avg PnL -0.6119%
- **High-conf BEARISH:** 6 signals, 4 hits, avg PnL +0.2638%

## Signal Timeline

Chronological view of all unique signals and their outcomes:

| # | Time (UTC) | Ticker | Dir | Score | Setup | Spot | Target | Inv | Result | Exit$ | PnL% | Hold (bars) |
|---|-----------|--------|-----|-------|-------|------|--------|-----|--------|-------|------|-------------|
| 1 | 16:18:59 | AAPL | BULL | 60 | Floor bounce | 333.69 | 335.00 | 330.00 | TIMEOUT | 332.40 | -0.587% | 24 |
| 2 | 16:24:47 | AMD | BULL | 99 | REJECTION REVERSAL | 534.05 | 535.00 | 527.50 | TARGET_HIT | 535.00 | -0.022% | 1 |
| 3 | 16:28:05 | AAPL | BULL | 60 | REJECTION REVERSAL | 334.06 | 335.00 | 330.00 | TIMEOUT | 332.19 | -0.760% | 24 |
| 4 | 16:30:44 | GOOGL | BEAR | 99 | REJECTION REVERSAL | 319.15 | 317.50 | 322.50 | TIMEOUT | 319.74 | -0.385% | 24 |
| 5 | 16:32:20 | AAPL | BULL | 60 | FLOOR BOUNCE | 334.06 | 335.00 | 330.00 | TIMEOUT | 332.53 | -0.658% | 24 |
| 6 | 16:32:45 | NVDA | BULL | 87 | FLOOR BOUNCE | 210.30 | 211.40 | 207.50 | INVALIDATED | 207.50 | -1.531% | 23 |
| 7 | 16:38:36 | GOOGL | BEAR | 92 | CEILING REJECTION | 319.60 | 317.50 | 322.50 | TIMEOUT | 319.35 | -0.122% | 24 |
| 8 | 16:46:20 | MU | BEAR | 94 | BREAKDOWN CONTINUATI | 937.46 | 931.64 | 950.00 | TARGET_HIT | 931.64 | +0.421% | 3 |
| 9 | 16:51:20 | AMZN | BEAR | 86 | REJECTION REVERSAL | 233.55 | 232.50 | 237.50 | TARGET_HIT | 232.50 | +0.250% | 17 |
| 10 | 16:54:20 | IBIT | BULL | 88 | REJECTION REVERSAL | 36.17 | 36.34 | 35.50 | TARGET_HIT | 36.34 | +0.270% | 16 |
| 11 | 17:10:20 | NVDA | BEAR | 99 | REJECTION REVERSAL | 209.80 | 208.65 | 212.50 | TARGET_HIT | 208.65 | +0.348% | 13 |
| 12 | 17:18:20 | GOOGL | BULL | 99 | FLOOR BOUNCE | 320.42 | 322.50 | 317.50 | TIMEOUT | 319.61 | -0.453% | 24 |
| 13 | 17:32:20 | MU | BEAR | 92 | REJECTION REVERSAL | 935.67 | 925.50 | 960.00 | TARGET_HIT | 925.50 | +0.887% | 7 |
| 14 | 17:40:20 | NVDA | BULL | 99 | Floor bounce | 210.18 | 211.51 | 209.16 | INVALIDATED | 209.16 | -0.687% | 5 |
| 15 | 17:43:20 | AMD | BULL | 98 | FLOOR BOUNCE | 532.84 | 535.00 | 527.50 | INVALIDATED | 527.50 | -1.202% | 8 |
| 16 | 17:52:20 | AMZN | BULL | 99 | FLOOR BOUNCE | 233.21 | 235.00 | 230.00 | TIMEOUT | 232.07 | -0.687% | 24 |
| 17 | 18:00:20 | GD | BEAR | 251 | QUAD DOWNSIDE | 385.70 | 375.00 | — | NO_DATA | — | — | 0 |
| 18 | 18:02:20 | AMZN | BULL | 99 | REJECTION REVERSAL | 233.03 | 234.01 | 230.00 | TIMEOUT | 232.05 | -0.621% | 24 |
| 19 | 18:08:20 | AMD | BEAR | 99 | BREAKDOWN CONTINUATI | 530.86 | 527.50 | 535.00 | TARGET_HIT | 527.50 | +0.433% | 3 |

## Target Distance Analysis

How far price moved from signal spot toward target (in %):

| # | Ticker | Dir | Spot | Target | Distance% | Max Excursion% | Reached% |
|---|--------|-----|------|--------|-----------|----------------|----------|
| 1 | AAPL | BULL | 333.69 | 335.00 | 0.393% | 0.162% | 41% |
| 2 | AMD | BULL | 534.05 | 535.00 | 0.178% | 0.881% | 495% |
| 3 | AAPL | BULL | 334.06 | 335.00 | 0.281% | 0.048% | 17% |
| 4 | GOOGL | BEAR | 319.15 | 317.50 | 0.517% | 0.135% | 26% |
| 5 | AAPL | BULL | 334.06 | 335.00 | 0.281% | 0.048% | 17% |
| 6 | NVDA | BULL | 210.30 | 211.40 | 0.523% | 0.143% | 27% |
| 7 | GOOGL | BEAR | 319.60 | 317.50 | 0.657% | 0.275% | 42% |
| 8 | MU | BEAR | 937.46 | 931.64 | 0.621% | 2.997% | 483% |
| 9 | AMZN | BEAR | 233.55 | 232.50 | 0.450% | 0.826% | 184% |
| 10 | IBIT | BULL | 36.17 | 36.34 | 0.470% | 0.608% | 129% |
| 11 | NVDA | BEAR | 209.80 | 208.65 | 0.548% | 2.326% | 424% |
| 12 | GOOGL | BULL | 320.42 | 322.50 | 0.649% | 0.225% | 35% |
| 13 | MU | BEAR | 935.67 | 925.50 | 1.087% | 3.385% | 311% |
| 14 | NVDA | BULL | 210.18 | 211.51 | 0.631% | 0.055% | 9% |
| 15 | AMD | BULL | 532.84 | 535.00 | 0.405% | 0.340% | 84% |
| 16 | AMZN | BULL | 233.21 | 235.00 | 0.768% | 0.000% | 0% |
| 17 | AMZN | BULL | 233.03 | 234.01 | 0.421% | 0.000% | 0% |
| 18 | AMD | BEAR | 530.86 | 527.50 | 0.633% | 2.422% | 383% |

## Key Findings

1. **Overall hit rate:** 7/18 (38.9%) targets hit within 2 hours.
2. **Invalidation rate:** 3/18 (16.7%) signals invalidated (stop hit before target).
3. **Average target distance:** 0.528% from spot — tight targets.
4. **Average PnL per signal:** -0.2836% (including timeouts at last close).
5. **Positive PnL rate:** 6/18 (33.3%) of signals ended with positive PnL.
6. **Best ticker:** MU (avg PnL +0.6541%)
7. **Worst ticker:** AAPL (avg PnL -0.6681%)
8. **BULLISH signals:** 11, 2 hits, avg PnL -0.6307%
9. **BEARISH signals:** 7, 5 hits, avg PnL +0.2618%

---

## Methodology Notes

- **Signal deduplication:** The CSV polls every 5 seconds. Unique signals are identified
  by (ticker, direction, setup_type) changes. Consecutive signals with the same
  (ticker, direction) are merged into one episode — only the first signal is evaluated.
- **Bar alignment (ses1):** Bars strictly AFTER the signal timestamp are used.
  The 5-min bar containing the signal time includes pre-signal price action,
  so using it would contaminate the result with information available before the signal.
- **Target check:** For BULLISH, checks if any subsequent 5-min bar high >= target.
  For BEARISH, checks if any subsequent 5-min bar low <= target.
- **Invalidation check:** Checked BEFORE target (conservative). For BULLISH, if any
  bar low <= invalidation level, signal is invalidated even if target was also reached.
- **Hold window:** Maximum 2 hours (24 five-min bars) after signal.
- **PnL calculation (ses3, ses7):**
  - BULLISH target hit: (target - spot) / spot - round_trip_cost
  - BEARISH target hit: (spot - target) / spot - round_trip_cost
  - BULLISH invalidation: (invalidation - spot) / spot - round_trip_cost
  - BEARISH invalidation: (spot - invalidation) / spot - round_trip_cost
  - Timeout: (last_close - spot) / spot - round_trip_cost (BULLISH)
  - Timeout: (spot - last_close) / spot - round_trip_cost (BEARISH)
  - Round-trip cost = 2 × 10 bps = 0.20%
- **Excursion cap (ses5):** Max excursion is measured only up to the exit bar,
  not the full 2h window, to avoid inflating excursion after trade has exited.
- **Score validation (ses6):** Scores outside 0-100 range are flagged as anomalies.
- **GD ticker:** Not available in Alpaca data (excluded from analysis).

---

**Report generated:** 2026-07-24T23:36:15.962865Z
**Status:** COMPLETE — 19 episodes analyzed against 5-min intraday bars (net of transaction costs)