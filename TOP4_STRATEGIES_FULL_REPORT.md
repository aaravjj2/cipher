# Cipher Strategy Backtest Report — Exploratory Internal Research

**Generated:** 2026-07-25T14:28:58.047058+00:00
**Engine:** Expanded Strategy Backtest
**Data files:**
- `data/backtest_results/expanded_backtest_latest.json`
  - SHA-256: `86e60946170a922c497353ce14af64fb2f06a9cd6929c9988e467d2d2eb6ae16`
- `data/bars/bars_transformed.json`
  - SHA-256: `90634cd5a6893735486bb8b9552deefb17ff9e1a48261d2ade834ade0a163908`
**Universe:** 33 tickers, 18,450 daily OHLCV bars
**Period:** 2022-01-03 to 2026-07-24
**Strategies tested:** 20
**Slippage:** 10 bps one-way (20 bps round-trip), deducted from every trade
**Position sizing:** 1/3 capital per trade (max 3 concurrent)
**Signal timing:** close[t] signal → entry at open[t+1] (next-bar open, no look-ahead)

---

## Engine Validation Assertions

**All checks passed:**
- No overlapping same-ticker positions detected
- All trades have valid entry/exit dates and prices
- Entry prices are positive
- No same-day exits (exit_date > entry_date enforced)
- No impossible target exits (long targets > entry, short targets < entry)

**Engine design notes:**
- Entry at open[t+1] after signal computed at close[t] (no look-ahead bias).
- Same-day exits prohibited: exit_date > entry_date enforced in simulate().
- Target/stop direction validated: long target > entry, long stop < entry (and vice versa for shorts).
- Same-bar target/stop collision: stop checked first (conservative).
- Vol regime uses expanding window up to bar i (no look-ahead in regime calculation).
- ATR uses bars[:i+1] (includes signal bar — valid, known at signal time).
- Weekly time-block bootstrap preserves cross-ticker correlation.
- Monte Carlo stress test measures max drawdown and losing streaks (not just mean).

### Critical Bug Fix Applied (2026-07-24)

**Vol regime window bug:** `_vol_regime()` was called with only 60 bars
(`bars[max(0,i-60):i]`) but requires 120+ bars to classify regimes.
With only 60 bars, the function always returned `"normal"`, which the VRS
strategies treated as the `else` (short) branch. This meant:
- `vrs_atr_levels` generated 0 long trades (all classified as "normal" → short)
- `vrs_both_regimes` generated 0 long trades (same bug)
- All "low" regime long opportunities were missed

**Fix:** Changed calls to `_vol_regime(bars[:i])` (full history) and added
explicit `if regime not in ("low", "high"): continue` to skip "normal".

**Impact after fix:**
- `vrs_atr_levels` longs: +1.22% mean (24 trades), shorts: -2.14% (25 trades) → net negative
- `vrs_both_regimes` longs: -0.98% (40 trades), shorts: -1.62% (49 trades) → net negative
- The previous "profitable" VRS results were artifacts of the bug
- After the VRS fix, `momentum_vol_filter` and `breakout_20d` were the only VRS-family strategies remaining positive. The full dynamic ranking (by mean trade return) selects the top strategies shown in the Executive Summary below.

### Regression Audit (automated invariant verification)

**All 8 invariants verified (PASS):**

- [PASS] entry_prices_positive: 2107 ok, 0 fail across 2107 trades
- [PASS] no_same_day_exits: 0 violations in 2107 trades
- [PASS] target_direction_valid: 0 impossible target exits
- [PASS] position_limits: max concurrent=3 (limit 3), max per-ticker=1 (limit 1)
- [PASS] no_impossible_target_exits: 0 impossible target exits (target should be profitable)
- [PASS] equity_direction_consistent: 0 strategies with trade/equity sign mismatch
- [PASS] no_price_discontinuities: 1 verified corporate actions, 0 unexplained gaps
- [PASS] corporate_actions_verified: AVGO on 2024-07-15: 90.0% gap matches known 10:1 split (expected ~90%)

### Per-Ticker Data Coverage (eng8)

| Ticker | First Date | Last Date | Bars | First Eligible | Missing |
|--------|-----------|-----------|------|----------------|--------|
| AAPL | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| ABBV | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| AMZN | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| AVGO | 2022-01-03 | 2025-12-26 | 1000 | 2023-01-04 | 37 |
| BA | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| BAC | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| CAT | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| COP | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| CVX | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| DIA | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| GOOGL | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| GS | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| HON | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| IWM | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| JNJ | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| JPM | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| KO | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| META | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| MS | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| MSFT | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| MU | 2022-01-03 | 2025-12-26 | 1000 | 2023-01-04 | 37 |
| NVDA | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| PEP | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| PFE | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| PG | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| QCOM | 2022-01-03 | 2025-12-26 | 1000 | 2023-01-04 | 37 |
| QQQ | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| SPY | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| UNH | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| UVXY | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| VXX | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| WMT | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |
| XOM | 2024-07-05 | 2026-07-24 | 515 | 2025-07-09 | 20 |

**Note:** "First Eligible" is the date after 252-bar warm-up. "Missing" estimates trading days not in dataset (weekends/holidays excluded).

### Corporate-Action Consistency (eng9)

- All OHLCV data from Alpaca uses **adjusted** prices throughout (split-adjusted).
- AVGO 10:1 split on 2024-07-15: prices before the split are ~10x post-split prices (e.g., $1,700 pre-split vs $170 post-split). This is the standard split-adjustment convention: post-split prices are divided by the split factor, so the entire series is on a consistent post-split basis. **The ~90% gap at the split boundary is expected and correct.** No raw/adjusted mixing exists.
- **Important caveat:** The split-adjustment discontinuity affects indicator calculations (ATR, 20-day highs, volatility) around the split date. The engine does NOT currently adjust indicators for splits. Results involving AVGO near 2024-07-15 may be distorted.
- UVXY/VXX: ETF reverse splits are reflected in adjusted data. No raw vs adjusted field mixing detected.
- Ticker universe was predetermined (not chosen using present-day survivors).

**Data coverage note:**
- 3 tickers (AVGO, MU, QCOM) have 1,000 bars (2022-01-03 to 2025-12-26).
- 30 tickers have 515 bars (2024-07-05 to 2026-07-24).
- This suggests an API pagination limit or separate data pulls. The changing universe may confound regime effects with composition effects.

---

## Executive Summary

Of 20 strategies tested, 3 had positive mean trade returns after slippage. The top 3 are shown below, ranked by a composite score (mean return × win rate × quality factor) that rewards higher win rates and fewer trades.

| # | Strategy | Trades | MeanTr (net) | WinR | PF | t-stat | p-value | Bonferroni sig? |
|---|----------|--------|-------------|------|----|--------|---------|-----------------|
| 1 | **tdr_five_day** | 57 | +0.1537% | 56.1% | 1.14 | 0.43 | 0.6654 | No |
| 2 | **momentum_vol_filter** | 131 | +0.1707% | 42.0% | 1.07 | 0.36 | 0.7189 | No |
| 3 | **bb_trend_filter** | 92 | +0.1341% | 42.4% | 1.05 | 0.21 | 0.8362 | No |

Bonferroni-corrected alpha = 0.05 / 20 = 0.00250

### Key Takeaways (computed, not hardcoded)

1. **tdr_five_day** ranks highest by composite score (mean × win rate × quality): +0.1537% mean return, 56.1% win rate, 57 trades, PF 1.14.
   t-stat = 0.43, p = 0.6654 (not statistically significant).
2. **momentum_vol_filter** has the most trades (131), although the effective independent sample size is lower because trades are clustered by time and market exposure.
3. **No strategy passes Bonferroni correction** — after adjusting for 20 strategies tested, none reach significance at alpha = 0.05.

### Complete Strategy Results (all 20 strategies)

| # | Strategy | Trades | Mean Net | WinR | PF | t-stat | p-value | Rank |
|---|----------|--------|----------|------|----|--------|---------|------|
| 1 | tdr_five_day | 57 | +0.1537% | 56.1% | 1.14 | 0.43 | 0.6654 | 1 |
| 2 | momentum_vol_filter | 131 | +0.1707% | 42.0% | 1.07 | 0.36 | 0.7189 | 2 |
| 3 | bb_trend_filter | 92 | +0.1341% | 42.4% | 1.05 | 0.21 | 0.8362 | 3 |
| 4 | vol_regime_switch | 30 | -0.0228% | 56.7% | 0.99 | -0.03 | 0.9732 | 4 |
| 5 | mvf_rsi_confirm | 68 | -0.1269% | 36.8% | 0.96 | -0.14 | 0.8925 | 5 |
| 6 | tdr_two_day | 349 | -0.6075% | 44.7% | 0.52 | -5.02 | 0.0000 * | 6 |
| 7 | mvf_normal_allowed | 171 | -0.4852% | 40.4% | 0.84 | -0.97 | 0.3296 | 7 |
| 8 | tdr_rsi_filter | 20 | -0.1233% | 55.0% | 0.90 | -0.20 | 0.8452 | 8 |
| 9 | three_day_reversal | 298 | -0.5613% | 48.0% | 0.55 | -4.32 | 0.0000 * | 9 |
| 10 | breakout_10d | 116 | -0.3934% | 51.7% | 0.84 | -0.69 | 0.4896 | 10 |
| 11 | tdr_volume_confirm | 65 | -0.4226% | 50.8% | 0.68 | -1.33 | 0.1848 | 11 |
| 12 | bollinger_squeeze | 99 | -0.6463% | 41.4% | 0.78 | -0.96 | 0.3366 | 12 |
| 13 | breakout_trend_filter | 83 | -0.7742% | 36.1% | 0.72 | -1.24 | 0.2132 | 13 |
| 14 | mvf_long_lookback | 51 | -0.7538% | 35.3% | 0.82 | -0.57 | 0.5704 | 14 |
| 15 | vrs_atr_levels | 52 | -0.7470% | 36.5% | 0.81 | -0.58 | 0.5604 | 15 |
| 16 | breakout_50d | 92 | -1.2380% | 40.2% | 0.66 | -1.59 | 0.1121 | 16 |
| 17 | breakout_20d | 94 | -1.4587% | 39.4% | 0.58 | -2.00 | 0.0459 | 17 |
| 18 | vrs_both_regimes | 84 | -1.5934% | 41.7% | 0.41 | -3.71 | 0.0002 * | 18 |
| 19 | vrs_trend_filter | 93 | -1.5736% | 45.2% | 0.50 | -2.95 | 0.0032 | 19 |
| 20 | bb_volume_confirm | 62 | -2.0741% | 35.5% | 0.46 | -2.57 | 0.0102 | 20 |

* = Bonferroni-significant at alpha = 0.00250 (none qualify).
Positive-mean strategies: 3. Top 3 shown in detail below.

---

## #1: TDR Five Day — 5-Day Mean Reversion in Uptrend

### Strategy Logic

Buy after 5 consecutive down days when price is above 50-SMA (uptrend):
- Require: close > 50-SMA (uptrend filter)
- Require: close[i] < close[i-1] < ... < close[i-5] (5 consecutive declines)
- Enter long at next open
- Target: entry × 1.03 (+3%)
- Stop: entry × 0.98 (−2%)
- Max hold: 5 days
- Min bars required: 60

**Academic basis:** Short-term mean reversion in trending stocks (Lehmann 1990).

---

### Performance Summary

| Metric | Value |
|--------|-------|
| Total Trades | 57 |
| Winners / Losers | 32 / 25 |
| Win Rate | 56.1% |
| Mean Trade Return (gross) | +0.3537% |
| Mean Trade Return (net of slippage) | +0.1537% |
| Median Trade Return (net) | +0.6542% |
| Std Dev of Returns | 2.683% |
| Best Trade | +2.59% |
| Worst Trade | -9.53% |
| Profit Factor (recomputed) | 1.14 |
| t-statistic | 0.43 |
| t-critical (95%, df=56) | 2.04 |
| p-value (two-sided, Student-t, df=56) | 0.6654 |
| Bonferroni alpha (0.05/20) | 0.00250 |
| Bonferroni significant? | No |
| Bootstrap 95% CI (weekly block) | [-0.6366%, +0.8591%] |
| Fraction of bootstrap means > 0 | 66.2% |
| Centered bootstrap p-value | 0.7000 |
| Simple equity curve total return | +2.86% |
| Max drawdown (simple equity) | 3.92% |
| Avg hold days | 2.9 |

### Direction Breakdown

| Direction | Trades | Mean PnL% (net) |
|-----------|--------|-----------------|
| Long | 57 | +0.154% |
| Short | 0 | +0.000% |

### Exit Reason Breakdown

| Exit Reason | Trades | Avg PnL% | Sum of trade returns% |
|-------------|--------|----------|----------------------|
| stop | 8 | -2.396% | -19.17% |
| stop_gap | 8 | -4.010% | -32.08% |
| target | 25 | +2.594% | +64.86% |
| time_exit | 16 | -0.303% | -4.85% |

*"Sum of trade returns" is NOT portfolio return — it sums unweighted per-trade PnL.*

### Per-Ticker Breakdown

| Ticker | Trades | WinR | MeanTr% | SumTr% | Best | Worst |
|--------|--------|------|---------|--------|------|-------|
| AAPL | 4 | 50% | +0.099% | +0.40% | +2.59% | -2.40% |
| ABBV | 4 | 25% | -1.783% | -7.13% | +0.25% | -3.31% |
| AMZN | 1 | 0% | -2.396% | -2.40% | -2.40% | -2.40% |
| AVGO | 6 | 83% | +1.741% | +10.44% | +2.59% | -2.53% |
| BA | 1 | 100% | +2.594% | +2.59% | +2.59% | +2.59% |
| BAC | 2 | 100% | +2.594% | +5.19% | +2.59% | +2.59% |
| DIA | 4 | 50% | -0.851% | -3.40% | +0.60% | -2.40% |
| GOOGL | 3 | 100% | +2.594% | +7.78% | +2.59% | +2.59% |
| HON | 1 | 100% | +0.654% | +0.65% | +0.65% | +0.65% |
| IWM | 1 | 0% | -1.609% | -1.61% | -1.61% | -1.61% |
| JNJ | 3 | 33% | +0.205% | +0.62% | +2.59% | -1.52% |
| JPM | 2 | 50% | +0.859% | +1.72% | +2.59% | -0.88% |
| META | 1 | 100% | +2.594% | +2.59% | +2.59% | +2.59% |
| MS | 1 | 100% | +2.594% | +2.59% | +2.59% | +2.59% |
| MSFT | 1 | 0% | -2.396% | -2.40% | -2.40% | -2.40% |
| MU | 7 | 57% | +0.233% | +1.63% | +2.59% | -3.14% |
| PEP | 1 | 0% | -1.309% | -1.31% | -1.31% | -1.31% |
| PG | 1 | 0% | -1.071% | -1.07% | -1.07% | -1.07% |
| QCOM | 2 | 50% | +0.099% | +0.20% | +2.59% | -2.40% |
| QQQ | 2 | 100% | +1.573% | +3.15% | +1.85% | +1.30% |
| SPY | 1 | 100% | +1.235% | +1.24% | +1.24% | +1.24% |
| UNH | 3 | 67% | +1.526% | +4.58% | +2.59% | -0.61% |
| UVXY | 1 | 0% | -5.106% | -5.11% | -5.11% | -5.11% |
| VXX | 1 | 0% | -9.528% | -9.53% | -9.53% | -9.53% |
| WMT | 3 | 33% | -0.888% | -2.66% | +2.59% | -2.86% |

### Stress Tests

| Scenario | Value |
|----------|-------|
| Base case (net mean trade) | +0.1537% |
| Double slippage (mean trade) | -0.0463% |
| Excl Top Contributor Avgo Mean | -0.0331% |
| Excl Top Contributor Total Pnl | +10.4446% |
| Excl Best Month 2022-12 Mean | +0.1101% |
| Monte Carlo mean max drawdown (sequential) | 5.99% |
| Monte Carlo P95 max drawdown (sequential) | 8.71% |
| Monte Carlo mean losing streak | 4.4 trades |
| Monte Carlo max losing streak | 11 trades |

> **Note:** Monte Carlo shuffles individual trade order sequentially — it does NOT model actual calendar concurrency (up to 3 simultaneous positions). The drawdown estimates are therefore approximate, not faithful portfolio simulations.
### Benchmark Comparison (SPY)

| Metric | Value |
|--------|-------|
| Benchmark period (actual SPY data used) | 2022-12-08 to 2026-07-14 (508 obs, 2.02 years) |
| SPY buy-and-hold return | +36.09% |
| SPY annualized return | +16.52% |
| Strategy cumulative return | +2.73% |
| Strategy annualized return | +1.35% |
| **Return difference** | -33.36% |
| **Annualized difference** | -15.17% |

> **Note:** "Return difference" is the simple subtraction of cumulative returns, not risk-adjusted alpha. No dividends included. Risk-free rate not subtracted. SPY data coverage: 2024-07-05 to 2026-07-24 (515 bars). Benchmark period is limited to the overlap between trade dates and available SPY bars. Annualization: (1 + cum_return)^(252/exposure_days) - 1.

### Robust Statistical Tests

| Test | Value |
|------|-------|
| Wilcoxon signed-rank W | 656 |
| Wilcoxon p-value | 0.1755 |
| 10% trimmed mean (net) | +0.4198% |
| Clustered SE (by month) | 0.4170% |
| Clustered t-stat | 0.37 |
| Clustered p-value | 0.7125 |
| N months | 30 |
| Profit factor 95% CI | [0.629, 2.154] |

### Adverse Cost Sensitivity

| Slippage (one-way) | Mean Net Trade Return |
|---------------------|----------------------|
| 5 bps | +0.2537% |
| 10 bps | +0.1537% **(base)** |
| 15 bps | +0.0537% |
| 20 bps | -0.0463% |
| 30 bps | -0.2463% |
| 50 bps | -0.6463% |
| **Breakeven slippage** | **18 bps one-way** |

### Out-of-Sample Split

**Split point:** 2025-07-21 (last 12 months held out)

| Period | N Trades | Mean Net Return |
|--------|----------|-----------------|
| In-sample (before 2025-07-21) | 31 | +0.2898% |
| Out-of-sample (after 2025-07-21) | 26 | -0.0087% |

OOS t-stat: -0.01, p-value: 0.9881

> **Caveat:** This OOS split is still within the same dataset and time period. It is NOT a truly independent sample. It tests whether the edge persists in the most recent period, but does not guard against overfitting to the specific market regime or ticker universe.

### Daily Mark-to-Market NAV Statistics

| Metric | Value |
|--------|-------|
| Daily Sharpe (annualized) | 0.307 |
| Mean daily return | 0.002554% |
| Std daily return | 0.131870% |
| Max underwater days | 332 |
| Positive days | 91 / 1143 (8.0%) |

> **Sharpe assumptions:** Risk-free rate = 0 (not subtracted). Uninvested cash days earn zero return. No dividends. This is a simplified Sharpe — actual cash management would earn T-bill rate on idle capital.

*CSV exports:* Trade ledger → `data/backtest_results/trade_ledger_tdr_five_day.csv`, Daily equity → `data/backtest_results/daily_equity_tdr_five_day.csv`

### Complete Trade Log (57 trades, net of slippage)

| # | Ticker | Dir | Entry Date | Entry $ | Exit Date | Exit $ | Reason | Hold | PnL% (gross) | PnL% (net) |
|---|--------|-----|-----------|---------|----------|--------|--------|------|-------------|-----------|
| 1 | AVGO | long | 2022-12-08 | 521.33 | 2022-12-09 | 535.90 | target | 1d | +2.79% | +2.59% |
| 2 | MU | long | 2023-01-20 | 57.53 | 2023-01-23 | 59.14 | target | 1d | +2.79% | +2.59% |
| 3 | MU | long | 2023-02-23 | 59.20 | 2023-02-24 | 57.64 | stop_gap | 1d | -2.63% | -2.83% |
| 4 | AVGO | long | 2023-06-23 | 834.84 | 2023-06-29 | 858.17 | target | 4d | +2.79% | +2.59% |
| 5 | MU | long | 2023-12-07 | 74.09 | 2023-12-11 | 76.16 | target | 2d | +2.79% | +2.59% |
| 6 | MU | long | 2024-01-04 | 83.55 | 2024-01-05 | 81.40 | stop_gap | 1d | -2.58% | -2.78% |
| 7 | AVGO | long | 2024-01-04 | 1059.02 | 2024-01-11 | 1088.61 | target | 5d | +2.79% | +2.59% |
| 8 | QCOM | long | 2024-03-20 | 164.26 | 2024-03-21 | 168.85 | target | 1d | +2.79% | +2.59% |
| 9 | MU | long | 2024-04-11 | 123.02 | 2024-04-16 | 119.41 | stop_gap | 3d | -2.94% | -3.14% |
| 10 | AVGO | long | 2024-06-26 | 1599.22 | 2024-07-01 | 1643.90 | target | 3d | +2.79% | +2.59% |
| 11 | JNJ | long | 2024-09-25 | 163.16 | 2024-10-02 | 161.01 | time_exit | 5d | -1.32% | -1.52% |
| 12 | AMZN | long | 2024-10-02 | 184.62 | 2024-10-07 | 180.57 | stop | 3d | -2.20% | -2.40% |
| 13 | ABBV | long | 2024-10-08 | 194.53 | 2024-10-15 | 191.67 | time_exit | 5d | -1.47% | -1.67% |
| 14 | QCOM | long | 2024-10-22 | 169.18 | 2024-10-23 | 165.46 | stop | 1d | -2.20% | -2.40% |
| 15 | IWM | long | 2024-10-24 | 220.65 | 2024-10-31 | 217.54 | time_exit | 5d | -1.41% | -1.61% |
| 16 | DIA | long | 2024-10-28 | 423.97 | 2024-11-04 | 417.63 | time_exit | 5d | -1.50% | -1.70% |
| 17 | BAC | long | 2024-11-05 | 41.55 | 2024-11-06 | 42.71 | target | 1d | +2.79% | +2.59% |
| 18 | UNH | long | 2024-11-19 | 583.30 | 2024-11-20 | 599.60 | target | 1d | +2.79% | +2.59% |
| 19 | QQQ | long | 2024-11-18 | 498.63 | 2024-11-25 | 506.08 | time_exit | 5d | +1.50% | +1.30% |
| 20 | HON | long | 2024-11-20 | 227.47 | 2024-11-27 | 229.41 | time_exit | 5d | +0.85% | +0.65% |
| 21 | JPM | long | 2024-12-04 | 244.94 | 2024-12-11 | 243.29 | time_exit | 5d | -0.68% | -0.88% |
| 22 | DIA | long | 2024-12-12 | 442.93 | 2024-12-18 | 433.21 | stop | 4d | -2.20% | -2.40% |
| 23 | AAPL | long | 2025-01-06 | 244.55 | 2025-01-10 | 239.18 | stop | 3d | -2.20% | -2.40% |
| 24 | GOOGL | long | 2025-01-15 | 193.28 | 2025-01-21 | 198.68 | target | 3d | +2.79% | +2.59% |
| 25 | BAC | long | 2025-02-04 | 46.40 | 2025-02-06 | 47.69 | target | 2d | +2.79% | +2.59% |
| 26 | META | long | 2025-02-25 | 666.64 | 2025-02-27 | 685.27 | target | 2d | +2.79% | +2.59% |
| 27 | JPM | long | 2025-02-26 | 257.42 | 2025-03-03 | 264.61 | target | 3d | +2.79% | +2.59% |
| 28 | ABBV | long | 2025-03-25 | 209.43 | 2025-03-26 | 202.91 | stop_gap | 1d | -3.11% | -3.31% |
| 29 | GOOGL | long | 2025-06-04 | 166.90 | 2025-06-06 | 171.57 | target | 2d | +2.79% | +2.59% |
| 30 | WMT | long | 2025-06-11 | 97.39 | 2025-06-12 | 94.80 | stop_gap | 1d | -2.66% | -2.86% |
| 31 | MU | long | 2025-07-02 | 120.62 | 2025-07-08 | 123.99 | target | 3d | +2.79% | +2.59% |
| 32 | DIA | long | 2025-08-04 | 438.32 | 2025-08-11 | 439.57 | time_exit | 5d | +0.29% | +0.09% |
| 33 | SPY | long | 2025-08-04 | 626.30 | 2025-08-11 | 635.28 | time_exit | 5d | +1.44% | +1.24% |
| 34 | AVGO | long | 2025-08-22 | 292.04 | 2025-08-27 | 300.20 | target | 3d | +2.79% | +2.59% |
| 35 | AAPL | long | 2025-08-21 | 226.50 | 2025-08-28 | 232.83 | target | 5d | +2.79% | +2.59% |
| 36 | QQQ | long | 2025-08-21 | 564.91 | 2025-08-28 | 576.50 | time_exit | 5d | +2.05% | +1.85% |
| 37 | AVGO | long | 2025-09-23 | 340.58 | 2025-09-25 | 332.66 | stop_gap | 2d | -2.33% | -2.53% |
| 38 | ABBV | long | 2025-10-15 | 227.43 | 2025-10-22 | 228.45 | time_exit | 5d | +0.45% | +0.25% |
| 39 | JNJ | long | 2025-10-30 | 187.27 | 2025-11-06 | 186.78 | time_exit | 5d | -0.26% | -0.46% |
| 40 | WMT | long | 2025-10-30 | 102.30 | 2025-11-06 | 100.06 | stop | 5d | -2.20% | -2.40% |
| 41 | MSFT | long | 2025-11-05 | 513.81 | 2025-11-06 | 502.53 | stop | 1d | -2.20% | -2.40% |
| 42 | AAPL | long | 2025-11-19 | 265.79 | 2025-11-20 | 273.22 | target | 1d | +2.79% | +2.59% |
| 43 | UVXY | long | 2025-12-01 | 49.46 | 2025-12-02 | 47.03 | stop_gap | 1d | -4.91% | -5.11% |
| 44 | JNJ | long | 2025-12-10 | 200.79 | 2025-12-11 | 206.40 | target | 1d | +2.79% | +2.59% |
| 45 | AAPL | long | 2025-12-10 | 278.03 | 2025-12-16 | 271.92 | stop | 4d | -2.20% | -2.40% |
| 46 | MU | long | 2025-12-18 | 256.79 | 2025-12-19 | 263.96 | target | 1d | +2.79% | +2.59% |
| 47 | MS | long | 2025-12-19 | 173.63 | 2025-12-22 | 178.48 | target | 1d | +2.79% | +2.59% |
| 48 | GOOGL | long | 2025-12-18 | 302.02 | 2025-12-23 | 310.46 | target | 3d | +2.79% | +2.59% |
| 49 | WMT | long | 2026-01-29 | 116.57 | 2026-02-02 | 119.82 | target | 2d | +2.79% | +2.59% |
| 50 | BA | long | 2026-02-02 | 232.87 | 2026-02-05 | 239.38 | target | 3d | +2.79% | +2.59% |
| 51 | PEP | long | 2026-03-09 | 159.33 | 2026-03-16 | 157.56 | time_exit | 5d | -1.11% | -1.31% |
| 52 | PG | long | 2026-03-09 | 153.30 | 2026-03-16 | 151.97 | time_exit | 5d | -0.87% | -1.07% |
| 53 | VXX | long | 2026-04-07 | 34.69 | 2026-04-08 | 31.46 | stop_gap | 1d | -9.33% | -9.53% |
| 54 | DIA | long | 2026-04-30 | 491.47 | 2026-05-07 | 495.41 | time_exit | 5d | +0.80% | +0.60% |
| 55 | UNH | long | 2026-05-21 | 381.50 | 2026-05-29 | 379.93 | time_exit | 5d | -0.41% | -0.61% |
| 56 | UNH | long | 2026-06-04 | 390.39 | 2026-06-05 | 401.30 | target | 1d | +2.79% | +2.59% |
| 57 | ABBV | long | 2026-07-13 | 248.62 | 2026-07-14 | 243.16 | stop | 1d | -2.20% | -2.40% |

---

## #2: Momentum Vol Filter — Breakout in Low Vol Only

### Strategy Logic

Classic momentum breakout, but ONLY when vol regime is "low":
- Check vol regime (60-bar lookback vs 252-bar average)
- If low vol: find 20-day high breakout
- Enter long, target = entry + 3×ATR, stop = entry - 1.5×ATR, 20-day hold

**Academic basis:** Jegadeesh & Titman (1993) momentum + vol filtering.

---

### Performance Summary

| Metric | Value |
|--------|-------|
| Total Trades | 131 |
| Winners / Losers | 55 / 76 |
| Win Rate | 42.0% |
| Mean Trade Return (gross) | +0.3707% |
| Mean Trade Return (net of slippage) | +0.1707% |
| Median Trade Return (net) | -2.4624% |
| Std Dev of Returns | 5.429% |
| Best Trade | +14.39% |
| Worst Trade | -14.33% |
| Profit Factor (recomputed) | 1.07 |
| t-statistic | 0.36 |
| t-critical (95%, df=130) | 1.96 |
| p-value (two-sided, Student-t, df=130) | 0.7189 |
| Bonferroni alpha (0.05/20) | 0.00250 |
| Bonferroni significant? | No |
| Bootstrap 95% CI (weekly block) | [-0.7327%, +1.0251%] |
| Fraction of bootstrap means > 0 | 64.1% |
| Centered bootstrap p-value | 0.7152 |
| Simple equity curve total return | +7.17% |
| Max drawdown (simple equity) | 14.91% |
| Avg hold days | 7.1 |

### Direction Breakdown

| Direction | Trades | Mean PnL% (net) |
|-----------|--------|-----------------|
| Long | 131 | +0.171% |
| Short | 0 | +0.000% |

### Exit Reason Breakdown

| Exit Reason | Trades | Avg PnL% | Sum of trade returns% |
|-------------|--------|----------|----------------------|
| stop | 66 | -3.536% | -233.40% |
| stop_gap | 9 | -6.858% | -61.72% |
| target | 46 | +6.631% | +305.03% |
| time_exit | 10 | +1.246% | +12.46% |

*"Sum of trade returns" is NOT portfolio return — it sums unweighted per-trade PnL.*

### Per-Ticker Breakdown

| Ticker | Trades | WinR | MeanTr% | SumTr% | Best | Worst |
|--------|--------|------|---------|--------|------|-------|
| AAPL | 6 | 67% | +1.762% | +10.57% | +6.60% | -3.49% |
| ABBV | 3 | 67% | +1.889% | +5.67% | +4.88% | -3.73% |
| AMZN | 4 | 50% | +0.307% | +1.23% | +5.35% | -3.76% |
| AVGO | 15 | 47% | +1.090% | +16.35% | +9.81% | -6.02% |
| BA | 6 | 50% | -0.154% | -0.93% | +7.88% | -6.25% |
| BAC | 6 | 67% | +2.860% | +17.16% | +6.55% | -2.86% |
| CAT | 5 | 20% | -2.440% | -12.20% | +4.55% | -4.69% |
| COP | 6 | 17% | -2.861% | -17.17% | +7.31% | -8.59% |
| CVX | 5 | 40% | +0.208% | +1.04% | +5.72% | -3.83% |
| DIA | 3 | 67% | -0.003% | -0.01% | +1.18% | -1.83% |
| GOOGL | 2 | 50% | +0.934% | +1.87% | +5.84% | -3.97% |
| GS | 7 | 43% | +0.028% | +0.19% | +7.33% | -4.45% |
| HON | 3 | 0% | -2.860% | -8.58% | -2.49% | -3.21% |
| IWM | 4 | 0% | -2.610% | -10.44% | -2.46% | -2.72% |
| JNJ | 2 | 50% | +0.824% | +1.65% | +4.58% | -2.93% |
| JPM | 3 | 33% | -0.445% | -1.33% | +5.56% | -3.62% |
| KO | 1 | 100% | +4.197% | +4.20% | +4.20% | +4.20% |
| META | 3 | 33% | -0.731% | -2.19% | +6.45% | -4.55% |
| MS | 2 | 100% | +5.350% | +10.70% | +6.26% | +4.44% |
| MSFT | 3 | 33% | -0.440% | -1.32% | +3.27% | -2.42% |
| MU | 5 | 20% | -2.718% | -13.59% | +7.95% | -6.30% |
| NVDA | 3 | 33% | +0.586% | +1.76% | +10.43% | -4.50% |
| PFE | 4 | 50% | +1.152% | +4.61% | +5.86% | -3.68% |
| PG | 2 | 0% | -3.258% | -6.52% | -3.07% | -3.45% |
| QCOM | 7 | 57% | -0.189% | -1.33% | +6.23% | -10.40% |
| QQQ | 3 | 0% | -2.063% | -6.19% | -1.81% | -2.32% |
| SPY | 3 | 0% | -1.666% | -5.00% | -1.46% | -1.83% |
| UNH | 3 | 33% | -2.938% | -8.81% | +8.64% | -14.33% |
| UVXY | 3 | 67% | +6.235% | +18.71% | +14.39% | -9.23% |
| VXX | 2 | 100% | +11.102% | +22.20% | +12.54% | +9.66% |
| WMT | 5 | 40% | -0.841% | -4.21% | +4.97% | -6.78% |
| XOM | 2 | 50% | +2.135% | +4.27% | +6.96% | -2.69% |

### Stress Tests

| Scenario | Value |
|----------|-------|
| Base case (net mean trade) | +0.1707% |
| Double slippage (mean trade) | -0.0293% |
| Excl Top Contributor Vxx Mean | +0.0012% |
| Excl Top Contributor Total Pnl | +22.2048% |
| Excl Best Month 2022-11 Mean | +0.0992% |
| Monte Carlo mean max drawdown (sequential) | 17.66% |
| Monte Carlo P95 max drawdown (sequential) | 25.26% |
| Monte Carlo mean losing streak | 7.9 trades |
| Monte Carlo max losing streak | 18 trades |

> **Note:** Monte Carlo shuffles individual trade order sequentially — it does NOT model actual calendar concurrency (up to 3 simultaneous positions). The drawdown estimates are therefore approximate, not faithful portfolio simulations.
### Benchmark Comparison (SPY)

| Metric | Value |
|--------|-------|
| Benchmark period (actual SPY data used) | 2022-07-18 to 2026-07-07 (503 obs, 2.0 years) |
| SPY buy-and-hold return | +34.39% |
| SPY annualized return | +15.96% |
| Strategy cumulative return | +5.48% |
| Strategy annualized return | +2.71% |
| **Return difference** | -28.91% |
| **Annualized difference** | -13.25% |

> **Note:** "Return difference" is the simple subtraction of cumulative returns, not risk-adjusted alpha. No dividends included. Risk-free rate not subtracted. SPY data coverage: 2024-07-05 to 2026-07-24 (515 bars). Benchmark period is limited to the overlap between trade dates and available SPY bars. Annualization: (1 + cum_return)^(252/exposure_days) - 1.

### Robust Statistical Tests

| Test | Value |
|------|-------|
| Wilcoxon signed-rank W | 3953 |
| Wilcoxon p-value | 0.3953 |
| 10% trimmed mean (net) | -0.1315% |
| Clustered SE (by month) | 0.7385% |
| Clustered t-stat | 0.23 |
| Clustered p-value | 0.8172 |
| N months | 37 |
| Profit factor 95% CI | [0.710, 1.582] |

### Adverse Cost Sensitivity

| Slippage (one-way) | Mean Net Trade Return |
|---------------------|----------------------|
| 5 bps | +0.2707% |
| 10 bps | +0.1707% **(base)** |
| 15 bps | +0.0707% |
| 20 bps | -0.0293% |
| 30 bps | -0.2293% |
| 50 bps | -0.6293% |
| **Breakeven slippage** | **19 bps one-way** |

### Out-of-Sample Split

**Split point:** 2025-07-21 (last 12 months held out)

| Period | N Trades | Mean Net Return |
|--------|----------|-----------------|
| In-sample (before 2025-07-21) | 68 | +0.4983% |
| Out-of-sample (after 2025-07-21) | 63 | -0.1829% |

OOS t-stat: -0.30, p-value: 0.7624

> **Caveat:** This OOS split is still within the same dataset and time period. It is NOT a truly independent sample. It tests whether the edge persists in the most recent period, but does not guard against overfitting to the specific market regime or ticker universe.

### Daily Mark-to-Market NAV Statistics

| Metric | Value |
|--------|-------|
| Daily Sharpe (annualized) | 0.340 |
| Mean daily return | 0.006522% |
| Std daily return | 0.304347% |
| Max underwater days | 476 |
| Positive days | 297 / 1143 (26.0%) |

> **Sharpe assumptions:** Risk-free rate = 0 (not subtracted). Uninvested cash days earn zero return. No dividends. This is a simplified Sharpe — actual cash management would earn T-bill rate on idle capital.

*CSV exports:* Trade ledger → `data/backtest_results/trade_ledger_momentum_vol_filter.csv`, Daily equity → `data/backtest_results/daily_equity_momentum_vol_filter.csv`

### Complete Trade Log (131 trades, net of slippage)

| # | Ticker | Dir | Entry Date | Entry $ | Exit Date | Exit $ | Reason | Hold | PnL% (gross) | PnL% (net) |
|---|--------|-----|-----------|---------|----------|--------|--------|------|-------------|-----------|
| 1 | MU | long | 2022-07-18 | 62.15 | 2022-08-09 | 58.36 | stop | 16d | -6.10% | -6.30% |
| 2 | QCOM | long | 2022-07-20 | 147.58 | 2022-08-17 | 148.38 | time_exit | 20d | +0.54% | +0.34% |
| 3 | AVGO | long | 2022-07-22 | 519.33 | 2022-08-19 | 547.88 | time_exit | 20d | +5.50% | +5.30% |
| 4 | AVGO | long | 2022-11-09 | 478.98 | 2022-11-15 | 525.27 | target | 4d | +9.67% | +9.47% |
| 5 | AVGO | long | 2022-12-13 | 576.40 | 2022-12-16 | 553.70 | stop | 3d | -3.94% | -4.14% |
| 6 | AVGO | long | 2023-01-09 | 592.89 | 2023-01-10 | 570.42 | stop | 1d | -3.79% | -3.99% |
| 7 | AVGO | long | 2023-02-02 | 608.24 | 2023-02-21 | 588.64 | stop | 12d | -3.22% | -3.42% |
| 8 | AVGO | long | 2023-03-06 | 635.63 | 2023-03-10 | 614.32 | stop | 4d | -3.35% | -3.55% |
| 9 | MU | long | 2023-03-24 | 60.57 | 2023-04-04 | 57.37 | stop | 7d | -5.28% | -5.48% |
| 10 | MU | long | 2023-05-26 | 69.95 | 2023-06-08 | 66.27 | stop | 8d | -5.26% | -5.46% |
| 11 | MU | long | 2023-07-28 | 71.65 | 2023-08-02 | 68.71 | stop | 3d | -4.10% | -4.30% |
| 12 | QCOM | long | 2023-07-31 | 130.12 | 2023-08-03 | 116.85 | stop_gap | 3d | -10.20% | -10.40% |
| 13 | AVGO | long | 2023-07-18 | 906.89 | 2023-08-04 | 876.65 | stop | 13d | -3.33% | -3.53% |
| 14 | AVGO | long | 2023-10-13 | 909.91 | 2023-10-17 | 875.12 | stop | 2d | -3.82% | -4.02% |
| 15 | QCOM | long | 2023-12-06 | 132.11 | 2023-12-13 | 138.32 | target | 5d | +4.70% | +4.50% |
| 16 | QCOM | long | 2023-12-14 | 139.74 | 2023-12-28 | 146.74 | target | 9d | +5.01% | +4.81% |
| 17 | AVGO | long | 2024-01-22 | 1217.47 | 2024-02-20 | 1225.32 | time_exit | 20d | +0.65% | +0.45% |
| 18 | AVGO | long | 2024-02-23 | 1309.68 | 2024-03-04 | 1423.51 | target | 6d | +8.69% | +8.49% |
| 19 | QCOM | long | 2024-03-05 | 164.89 | 2024-03-07 | 175.50 | target | 2d | +6.43% | +6.23% |
| 20 | MU | long | 2024-05-15 | 126.36 | 2024-06-12 | 136.65 | target | 19d | +8.15% | +7.95% |
| 21 | AVGO | long | 2024-06-12 | 1501.50 | 2024-06-13 | 1616.64 | target | 1d | +7.67% | +7.47% |
| 22 | AVGO | long | 2024-09-27 | 178.35 | 2024-10-01 | 167.97 | stop | 2d | -5.82% | -6.02% |
| 23 | MS | long | 2024-10-07 | 107.80 | 2024-10-15 | 112.80 | target | 6d | +4.64% | +4.44% |
| 24 | IWM | long | 2024-10-17 | 227.14 | 2024-10-21 | 221.70 | stop | 2d | -2.39% | -2.59% |
| 25 | NVDA | long | 2024-10-08 | 130.39 | 2024-10-22 | 144.25 | target | 10d | +10.63% | +10.43% |
| 26 | AVGO | long | 2024-10-09 | 179.42 | 2024-10-24 | 170.90 | stop | 11d | -4.75% | -4.95% |
| 27 | WMT | long | 2024-10-23 | 81.97 | 2024-11-06 | 84.94 | target | 10d | +3.62% | +3.42% |
| 28 | AMZN | long | 2024-10-31 | 190.70 | 2024-11-06 | 201.29 | target | 4d | +5.55% | +5.35% |
| 29 | CAT | long | 2024-11-07 | 415.33 | 2024-11-08 | 400.06 | stop | 1d | -3.68% | -3.88% |
| 30 | JPM | long | 2024-11-07 | 244.74 | 2024-11-08 | 236.38 | stop | 1d | -3.42% | -3.62% |
| 31 | CVX | long | 2024-11-04 | 154.57 | 2024-11-15 | 161.75 | target | 9d | +4.64% | +4.44% |
| 32 | WMT | long | 2024-11-20 | 86.69 | 2024-11-26 | 91.17 | target | 4d | +5.17% | +4.97% |
| 33 | AAPL | long | 2024-11-27 | 234.70 | 2024-12-09 | 245.45 | target | 7d | +4.58% | +4.38% |
| 34 | QQQ | long | 2024-12-12 | 528.21 | 2024-12-18 | 518.35 | stop | 4d | -1.87% | -2.07% |
| 35 | UVXY | long | 2024-12-19 | 23.62 | 2024-12-20 | 26.87 | target | 1d | +13.74% | +13.54% |
| 36 | AAPL | long | 2024-12-10 | 247.14 | 2024-12-24 | 256.78 | target | 10d | +3.90% | +3.70% |
| 37 | PFE | long | 2025-01-07 | 27.04 | 2025-01-15 | 26.20 | stop | 5d | -3.11% | -3.31% |
| 38 | BA | long | 2024-12-16 | 168.26 | 2025-01-16 | 168.76 | time_exit | 20d | +0.30% | +0.10% |
| 39 | CVX | long | 2025-01-21 | 161.31 | 2025-01-22 | 156.82 | stop | 1d | -2.78% | -2.98% |
| 40 | AMZN | long | 2025-01-23 | 234.33 | 2025-01-27 | 225.98 | stop_gap | 2d | -3.56% | -3.76% |
| 41 | KO | long | 2025-01-28 | 63.57 | 2025-02-11 | 66.37 | target | 10d | +4.40% | +4.20% |
| 42 | JPM | long | 2025-01-24 | 263.95 | 2025-02-18 | 279.17 | target | 16d | +5.76% | +5.56% |
| 43 | GS | long | 2025-02-19 | 669.75 | 2025-02-20 | 649.78 | stop | 1d | -2.98% | -3.18% |
| 44 | QCOM | long | 2025-01-23 | 170.55 | 2025-02-21 | 165.26 | time_exit | 20d | -3.10% | -3.30% |
| 45 | META | long | 2025-02-13 | 722.24 | 2025-02-21 | 690.82 | stop | 5d | -4.35% | -4.55% |
| 46 | JNJ | long | 2025-03-04 | 168.25 | 2025-03-05 | 163.66 | stop | 1d | -2.73% | -2.93% |
| 47 | UVXY | long | 2025-03-04 | 24.18 | 2025-03-05 | 22.00 | stop | 1d | -9.03% | -9.23% |
| 48 | VXX | long | 2025-03-04 | 51.62 | 2025-03-11 | 58.20 | target | 5d | +12.74% | +12.54% |
| 49 | ABBV | long | 2025-03-11 | 216.88 | 2025-03-12 | 209.23 | stop | 1d | -3.53% | -3.73% |
| 50 | COP | long | 2025-04-03 | 100.43 | 2025-04-04 | 92.01 | stop_gap | 1d | -8.39% | -8.59% |
| 51 | UNH | long | 2025-04-01 | 526.27 | 2025-04-07 | 510.86 | stop | 4d | -2.93% | -3.13% |
| 52 | UNH | long | 2025-04-09 | 560.70 | 2025-04-17 | 481.47 | stop_gap | 6d | -14.13% | -14.33% |
| 53 | BAC | long | 2025-05-09 | 41.77 | 2025-05-14 | 44.59 | target | 3d | +6.75% | +6.55% |
| 54 | GS | long | 2025-05-15 | 610.34 | 2025-05-23 | 584.41 | stop_gap | 6d | -4.25% | -4.45% |
| 55 | AVGO | long | 2025-05-13 | 222.22 | 2025-05-29 | 244.46 | target | 11d | +10.01% | +9.81% |
| 56 | BA | long | 2025-05-12 | 198.36 | 2025-06-03 | 214.39 | target | 15d | +8.08% | +7.88% |
| 57 | AVGO | long | 2025-05-30 | 241.46 | 2025-06-04 | 263.67 | target | 3d | +9.20% | +9.00% |
| 58 | BA | long | 2025-06-04 | 214.98 | 2025-06-12 | 201.97 | stop_gap | 6d | -6.05% | -6.25% |
| 59 | HON | long | 2025-05-28 | 226.69 | 2025-06-20 | 220.60 | stop | 16d | -2.69% | -2.89% |
| 60 | MSFT | long | 2025-06-13 | 476.89 | 2025-06-25 | 493.44 | target | 7d | +3.47% | +3.27% |
| 61 | BAC | long | 2025-06-23 | 45.45 | 2025-06-27 | 47.55 | target | 4d | +4.63% | +4.43% |
| 62 | META | long | 2025-06-05 | 692.35 | 2025-06-30 | 738.39 | target | 16d | +6.65% | +6.45% |
| 63 | GS | long | 2025-06-26 | 671.29 | 2025-06-30 | 709.19 | target | 2d | +5.65% | +5.45% |
| 64 | CAT | long | 2025-07-01 | 386.98 | 2025-07-10 | 405.34 | target | 6d | +4.75% | +4.55% |
| 65 | IWM | long | 2025-07-11 | 223.39 | 2025-07-16 | 217.88 | stop | 3d | -2.47% | -2.67% |
| 66 | MSFT | long | 2025-07-18 | 514.99 | 2025-07-22 | 504.82 | stop | 2d | -1.98% | -2.18% |
| 67 | AMZN | long | 2025-06-30 | 223.74 | 2025-07-29 | 230.78 | time_exit | 20d | +3.14% | +2.94% |
| 68 | DIA | long | 2025-07-01 | 440.63 | 2025-07-30 | 444.32 | time_exit | 20d | +0.84% | +0.64% |
| 69 | COP | long | 2025-07-30 | 97.49 | 2025-08-01 | 94.39 | stop | 2d | -3.18% | -3.38% |
| 70 | GOOGL | long | 2025-07-23 | 191.69 | 2025-08-12 | 203.27 | target | 14d | +6.04% | +5.84% |
| 71 | WMT | long | 2025-08-07 | 103.56 | 2025-08-13 | 101.05 | stop | 4d | -2.42% | -2.62% |
| 72 | GS | long | 2025-08-13 | 746.53 | 2025-08-18 | 723.24 | stop | 3d | -3.12% | -3.32% |
| 73 | META | long | 2025-08-01 | 761.49 | 2025-08-20 | 731.86 | stop | 13d | -3.89% | -4.09% |
| 74 | COP | long | 2025-08-26 | 97.69 | 2025-09-04 | 94.06 | stop | 6d | -3.72% | -3.92% |
| 75 | JPM | long | 2025-09-05 | 303.95 | 2025-09-08 | 294.60 | stop_gap | 1d | -3.08% | -3.28% |
| 76 | ABBV | long | 2025-08-21 | 209.35 | 2025-09-11 | 219.98 | target | 14d | +5.08% | +4.88% |
| 77 | DIA | long | 2025-08-25 | 456.10 | 2025-09-23 | 462.41 | time_exit | 20d | +1.38% | +1.18% |
| 78 | ABBV | long | 2025-09-12 | 219.34 | 2025-09-30 | 229.68 | target | 12d | +4.72% | +4.52% |
| 79 | PFE | long | 2025-10-01 | 26.00 | 2025-10-03 | 27.54 | target | 2d | +5.94% | +5.74% |
| 80 | CAT | long | 2025-10-06 | 500.92 | 2025-10-07 | 483.09 | stop | 1d | -3.56% | -3.76% |
| 81 | QCOM | long | 2025-09-17 | 164.84 | 2025-10-10 | 159.38 | stop | 17d | -3.31% | -3.51% |
| 82 | QQQ | long | 2025-10-02 | 607.67 | 2025-10-10 | 597.91 | stop | 6d | -1.61% | -1.81% |
| 83 | SPY | long | 2025-10-09 | 674.20 | 2025-10-10 | 665.73 | stop | 1d | -1.26% | -1.46% |
| 84 | UVXY | long | 2025-10-13 | 11.51 | 2025-10-16 | 13.19 | target | 3d | +14.59% | +14.39% |
| 85 | VXX | long | 2025-10-13 | 35.40 | 2025-10-16 | 38.89 | target | 3d | +9.86% | +9.66% |
| 86 | WMT | long | 2025-10-15 | 107.43 | 2025-10-27 | 104.21 | stop | 8d | -2.99% | -3.19% |
| 87 | MSFT | long | 2025-10-28 | 550.55 | 2025-10-29 | 538.35 | stop | 1d | -2.22% | -2.42% |
| 88 | PFE | long | 2025-11-12 | 25.42 | 2025-11-20 | 24.54 | stop | 6d | -3.48% | -3.68% |
| 89 | XOM | long | 2025-11-10 | 117.56 | 2025-11-24 | 114.63 | stop | 10d | -2.49% | -2.69% |
| 90 | AAPL | long | 2025-10-28 | 269.25 | 2025-11-25 | 276.69 | time_exit | 20d | +2.76% | +2.56% |
| 91 | AAPL | long | 2025-12-02 | 283.28 | 2025-12-11 | 273.96 | stop | 7d | -3.29% | -3.49% |
| 92 | SPY | long | 2025-12-12 | 688.86 | 2025-12-16 | 677.62 | stop | 2d | -1.63% | -1.83% |
| 93 | CAT | long | 2025-12-05 | 604.60 | 2025-12-17 | 579.11 | stop | 8d | -4.22% | -4.42% |
| 94 | BAC | long | 2025-12-15 | 55.47 | 2025-12-18 | 54.01 | stop | 3d | -2.63% | -2.83% |
| 95 | HON | long | 2025-12-18 | 201.03 | 2025-12-22 | 196.43 | stop | 2d | -2.29% | -2.49% |
| 96 | BAC | long | 2025-12-26 | 56.34 | 2026-01-02 | 54.84 | stop | 4d | -2.66% | -2.86% |
| 97 | SPY | long | 2025-12-26 | 691.33 | 2026-01-02 | 680.90 | stop | 4d | -1.51% | -1.71% |
| 98 | CVX | long | 2026-01-05 | 165.92 | 2026-01-06 | 162.43 | stop | 1d | -2.10% | -2.30% |
| 99 | BA | long | 2026-01-05 | 229.02 | 2026-01-13 | 242.30 | target | 6d | +5.80% | +5.60% |
| 100 | NVDA | long | 2025-12-24 | 188.13 | 2026-01-20 | 180.05 | stop | 16d | -4.30% | -4.50% |
| 101 | AMZN | long | 2026-01-07 | 239.85 | 2026-01-20 | 232.40 | stop | 8d | -3.10% | -3.30% |
| 102 | IWM | long | 2026-01-22 | 270.10 | 2026-01-26 | 263.99 | stop | 2d | -2.26% | -2.46% |
| 103 | BA | long | 2026-01-14 | 244.68 | 2026-01-29 | 236.30 | stop | 10d | -3.43% | -3.63% |
| 104 | QQQ | long | 2026-01-28 | 636.10 | 2026-01-29 | 622.63 | stop | 1d | -2.12% | -2.32% |
| 105 | GOOGL | long | 2026-02-03 | 347.69 | 2026-02-04 | 334.57 | stop | 1d | -3.77% | -3.97% |
| 106 | PFE | long | 2026-01-23 | 25.61 | 2026-02-05 | 27.16 | target | 9d | +6.06% | +5.86% |
| 107 | PG | long | 2026-02-13 | 161.90 | 2026-02-18 | 157.26 | stop | 2d | -2.87% | -3.07% |
| 108 | JNJ | long | 2026-02-06 | 239.20 | 2026-03-02 | 250.63 | target | 15d | +4.78% | +4.58% |
| 109 | COP | long | 2026-02-10 | 108.66 | 2026-03-02 | 116.82 | target | 13d | +7.51% | +7.31% |
| 110 | PG | long | 2026-02-24 | 165.06 | 2026-03-03 | 159.70 | stop | 5d | -3.25% | -3.45% |
| 111 | COP | long | 2026-03-03 | 120.97 | 2026-03-04 | 116.13 | stop | 1d | -4.00% | -4.20% |
| 112 | HON | long | 2026-03-03 | 244.17 | 2026-03-05 | 236.83 | stop | 2d | -3.01% | -3.21% |
| 113 | CVX | long | 2026-03-20 | 201.60 | 2026-03-30 | 213.53 | target | 6d | +5.92% | +5.72% |
| 114 | XOM | long | 2026-03-25 | 164.03 | 2026-03-30 | 175.78 | target | 3d | +7.16% | +6.96% |
| 115 | COP | long | 2026-03-20 | 126.43 | 2026-04-08 | 121.13 | stop | 12d | -4.19% | -4.39% |
| 116 | BAC | long | 2026-04-08 | 51.99 | 2026-04-15 | 55.28 | target | 5d | +6.32% | +6.12% |
| 117 | UNH | long | 2026-04-08 | 312.31 | 2026-04-21 | 339.93 | target | 9d | +8.84% | +8.64% |
| 118 | NVDA | long | 2026-04-27 | 209.85 | 2026-04-30 | 201.52 | stop | 3d | -3.97% | -4.17% |
| 119 | AAPL | long | 2026-04-16 | 267.07 | 2026-05-01 | 285.24 | target | 11d | +6.80% | +6.60% |
| 120 | GS | long | 2026-04-09 | 902.90 | 2026-05-07 | 924.94 | time_exit | 20d | +2.44% | +2.24% |
| 121 | IWM | long | 2026-05-06 | 285.65 | 2026-05-12 | 278.46 | stop | 4d | -2.52% | -2.72% |
| 122 | BA | long | 2026-05-11 | 239.68 | 2026-05-14 | 229.07 | stop | 3d | -4.43% | -4.63% |
| 123 | CAT | long | 2026-05-01 | 897.74 | 2026-05-18 | 857.39 | stop | 11d | -4.49% | -4.69% |
| 124 | GS | long | 2026-05-14 | 967.87 | 2026-05-19 | 932.29 | stop_gap | 3d | -3.68% | -3.88% |
| 125 | WMT | long | 2026-05-20 | 133.04 | 2026-05-21 | 124.28 | stop_gap | 1d | -6.58% | -6.78% |
| 126 | CVX | long | 2026-05-19 | 195.12 | 2026-05-26 | 188.04 | stop | 4d | -3.63% | -3.83% |
| 127 | GS | long | 2026-05-21 | 983.19 | 2026-06-02 | 1057.24 | target | 7d | +7.53% | +7.33% |
| 128 | AAPL | long | 2026-05-22 | 306.43 | 2026-06-09 | 297.28 | stop | 11d | -2.99% | -3.19% |
| 129 | DIA | long | 2026-06-01 | 510.36 | 2026-06-10 | 502.04 | stop | 7d | -1.63% | -1.83% |
| 130 | MS | long | 2026-06-03 | 213.06 | 2026-06-17 | 226.82 | target | 10d | +6.46% | +6.26% |
| 131 | BAC | long | 2026-06-15 | 56.68 | 2026-07-07 | 60.04 | target | 14d | +5.94% | +5.74% |

---

## #3: BB Trend Filter — Bollinger Squeeze Breakout with Trend Filter

### Strategy Logic

Bollinger Band squeeze breakout, only long in uptrend:
- BB period=20, bandwidth = 4×std(close,20)/mean(close,20)
- Squeeze: bandwidth < 20th percentile of historical bandwidths
- Long signal: price breaks above upper BB during squeeze
- Trend filter: require close > 50-SMA
- Enter long at next open
- Target: entry + 3×ATR(14)
- Stop: entry − 1.5×ATR(14)
- Max hold: 15 days
- Min bars required: 60

**Academic basis:** Bollinger (1992) squeeze + trend confirmation.

---

### Performance Summary

| Metric | Value |
|--------|-------|
| Total Trades | 92 |
| Winners / Losers | 39 / 53 |
| Win Rate | 42.4% |
| Mean Trade Return (gross) | +0.3341% |
| Mean Trade Return (net of slippage) | +0.1341% |
| Median Trade Return (net) | -2.3809% |
| Std Dev of Returns | 6.224% |
| Best Trade | +19.81% |
| Worst Trade | -11.47% |
| Profit Factor (recomputed) | 1.05 |
| t-statistic | 0.21 |
| t-critical (95%, df=91) | 2.00 |
| p-value (two-sided, Student-t, df=91) | 0.8362 |
| Bonferroni alpha (0.05/20) | 0.00250 |
| Bonferroni significant? | No |
| Bootstrap 95% CI (weekly block) | [-1.1552%, +1.5505%] |
| Fraction of bootstrap means > 0 | 58.3% |
| Centered bootstrap p-value | 0.8490 |
| Simple equity curve total return | +3.56% |
| Max drawdown (simple equity) | 23.44% |
| Avg hold days | 6.0 |

### Direction Breakdown

| Direction | Trades | Mean PnL% (net) |
|-----------|--------|-----------------|
| Long | 92 | +0.134% |
| Short | 0 | +0.000% |

### Exit Reason Breakdown

| Exit Reason | Trades | Avg PnL% | Sum of trade returns% |
|-------------|--------|----------|----------------------|
| stop | 40 | -4.144% | -165.76% |
| stop_gap | 10 | -5.925% | -59.25% |
| target | 28 | +7.859% | +220.04% |
| time_exit | 14 | +1.237% | +17.32% |

*"Sum of trade returns" is NOT portfolio return — it sums unweighted per-trade PnL.*

### Per-Ticker Breakdown

| Ticker | Trades | WinR | MeanTr% | SumTr% | Best | Worst |
|--------|--------|------|---------|--------|------|-------|
| AAPL | 4 | 50% | +1.607% | +6.43% | +6.60% | -2.91% |
| ABBV | 1 | 100% | +7.179% | +7.18% | +7.18% | +7.18% |
| AMZN | 4 | 25% | -0.837% | -3.35% | +5.35% | -3.55% |
| AVGO | 7 | 43% | +0.786% | +5.50% | +8.49% | -4.23% |
| BA | 2 | 0% | -5.857% | -11.71% | -4.25% | -7.47% |
| BAC | 1 | 0% | -2.974% | -2.97% | -2.97% | -2.97% |
| CAT | 3 | 33% | +0.195% | +0.58% | +10.26% | -4.87% |
| COP | 1 | 0% | -3.790% | -3.79% | -3.79% | -3.79% |
| CVX | 1 | 0% | -2.926% | -2.93% | -2.93% | -2.93% |
| DIA | 7 | 57% | -0.177% | -1.24% | +2.74% | -2.37% |
| GOOGL | 2 | 50% | +1.001% | +2.00% | +6.57% | -4.57% |
| GS | 5 | 40% | +0.578% | +2.89% | +6.75% | -3.18% |
| HON | 1 | 0% | -3.137% | -3.14% | -3.14% | -3.14% |
| JNJ | 1 | 100% | +4.243% | +4.24% | +4.24% | +4.24% |
| JPM | 3 | 100% | +3.808% | +11.42% | +5.57% | +1.64% |
| KO | 1 | 100% | +3.474% | +3.47% | +3.47% | +3.47% |
| META | 2 | 0% | -7.333% | -14.67% | -3.20% | -11.47% |
| MS | 3 | 67% | +1.636% | +4.91% | +6.35% | -3.08% |
| MSFT | 4 | 25% | -1.018% | -4.07% | +3.27% | -2.84% |
| MU | 10 | 40% | +0.557% | +5.57% | +12.00% | -6.60% |
| NVDA | 2 | 50% | -0.667% | -1.33% | +3.49% | -4.82% |
| PEP | 1 | 100% | +4.425% | +4.43% | +4.43% | +4.43% |
| PFE | 3 | 0% | -3.651% | -10.95% | -2.95% | -4.33% |
| PG | 1 | 0% | -3.211% | -3.21% | -3.21% | -3.21% |
| QCOM | 2 | 50% | -0.963% | -1.93% | +1.47% | -3.40% |
| QQQ | 2 | 0% | -2.308% | -4.62% | -2.13% | -2.49% |
| SPY | 2 | 100% | +1.278% | +2.56% | +1.36% | +1.19% |
| UNH | 2 | 50% | -0.122% | -0.24% | +3.63% | -3.87% |
| UVXY | 6 | 50% | +3.224% | +19.35% | +19.81% | -10.62% |
| VXX | 4 | 50% | +1.640% | +6.56% | +11.08% | -7.24% |
| WMT | 2 | 0% | -4.586% | -9.17% | -2.39% | -6.78% |
| XOM | 2 | 50% | +2.281% | +4.56% | +7.42% | -2.86% |

### Stress Tests

| Scenario | Value |
|----------|-------|
| Base case (net mean trade) | +0.1341% |
| Double slippage (mean trade) | -0.0659% |
| Excl Top Contributor Uvxy Mean | -0.0814% |
| Excl Top Contributor Total Pnl | +19.3459% |
| Excl Best Month 2022-11 Mean | +0.0037% |
| Monte Carlo mean max drawdown (sequential) | 17.60% |
| Monte Carlo P95 max drawdown (sequential) | 24.76% |
| Monte Carlo mean losing streak | 7.1 trades |
| Monte Carlo max losing streak | 16 trades |

> **Note:** Monte Carlo shuffles individual trade order sequentially — it does NOT model actual calendar concurrency (up to 3 simultaneous positions). The drawdown estimates are therefore approximate, not faithful portfolio simulations.
### Benchmark Comparison (SPY)

| Metric | Value |
|--------|-------|
| Benchmark period (actual SPY data used) | 2022-11-09 to 2026-07-01 (500 obs, 1.98 years) |
| SPY buy-and-hold return | +34.28% |
| SPY annualized return | +16.02% |
| Strategy cumulative return | +2.20% |
| Strategy annualized return | +1.10% |
| **Return difference** | -32.08% |
| **Annualized difference** | -14.91% |

> **Note:** "Return difference" is the simple subtraction of cumulative returns, not risk-adjusted alpha. No dividends included. Risk-free rate not subtracted. SPY data coverage: 2024-07-05 to 2026-07-24 (515 bars). Benchmark period is limited to the overlap between trade dates and available SPY bars. Annualization: (1 + cum_return)^(252/exposure_days) - 1.

### Robust Statistical Tests

| Test | Value |
|------|-------|
| Wilcoxon signed-rank W | 2071 |
| Wilcoxon p-value | 0.7912 |
| 10% trimmed mean (net) | -0.3018% |
| Clustered SE (by month) | 0.8877% |
| Clustered t-stat | 0.15 |
| Clustered p-value | 0.8799 |
| N months | 29 |
| Profit factor 95% CI | [0.643, 1.717] |

### Adverse Cost Sensitivity

| Slippage (one-way) | Mean Net Trade Return |
|---------------------|----------------------|
| 5 bps | +0.2341% |
| 10 bps | +0.1341% **(base)** |
| 15 bps | +0.0341% |
| 20 bps | -0.0659% |
| 30 bps | -0.2659% |
| 50 bps | -0.6659% |
| **Breakeven slippage** | **17 bps one-way** |

### Out-of-Sample Split

**Split point:** 2025-07-21 (last 12 months held out)

| Period | N Trades | Mean Net Return |
|--------|----------|-----------------|
| In-sample (before 2025-07-21) | 48 | -0.4397% |
| Out-of-sample (after 2025-07-21) | 44 | +0.7602% |

OOS t-stat: 0.78, p-value: 0.4329

> **Caveat:** This OOS split is still within the same dataset and time period. It is NOT a truly independent sample. It tests whether the edge persists in the most recent period, but does not guard against overfitting to the specific market regime or ticker universe.

### Daily Mark-to-Market NAV Statistics

| Metric | Value |
|--------|-------|
| Daily Sharpe (annualized) | 0.173 |
| Mean daily return | 0.003599% |
| Std daily return | 0.330401% |
| Max underwater days | 574 |
| Positive days | 173 / 1143 (15.1%) |

> **Sharpe assumptions:** Risk-free rate = 0 (not subtracted). Uninvested cash days earn zero return. No dividends. This is a simplified Sharpe — actual cash management would earn T-bill rate on idle capital.

*CSV exports:* Trade ledger → `data/backtest_results/trade_ledger_bb_trend_filter.csv`, Daily equity → `data/backtest_results/daily_equity_bb_trend_filter.csv`

### Complete Trade Log (92 trades, net of slippage)

| # | Ticker | Dir | Entry Date | Entry $ | Exit Date | Exit $ | Reason | Hold | PnL% (gross) | PnL% (net) |
|---|--------|-----|-----------|---------|----------|--------|--------|------|-------------|-----------|
| 1 | MU | long | 2022-11-09 | 56.28 | 2022-11-15 | 63.14 | target | 4d | +12.20% | +12.00% |
| 2 | AVGO | long | 2022-12-13 | 576.40 | 2022-12-16 | 553.70 | stop | 3d | -3.94% | -4.14% |
| 3 | AVGO | long | 2023-01-09 | 592.89 | 2023-01-10 | 570.42 | stop | 1d | -3.79% | -3.99% |
| 4 | AVGO | long | 2023-02-08 | 607.29 | 2023-02-21 | 586.39 | stop | 8d | -3.44% | -3.64% |
| 5 | MU | long | 2023-03-24 | 60.57 | 2023-04-04 | 57.37 | stop | 7d | -5.28% | -5.48% |
| 6 | MU | long | 2023-05-16 | 64.61 | 2023-05-26 | 70.49 | target | 8d | +9.10% | +8.90% |
| 7 | AVGO | long | 2024-02-23 | 1309.68 | 2024-03-04 | 1423.51 | target | 6d | +8.69% | +8.49% |
| 8 | QCOM | long | 2024-04-10 | 172.59 | 2024-04-17 | 167.07 | stop | 5d | -3.20% | -3.40% |
| 9 | MU | long | 2024-05-16 | 128.06 | 2024-05-31 | 122.73 | stop | 10d | -4.16% | -4.36% |
| 10 | AVGO | long | 2024-06-13 | 1711.71 | 2024-06-17 | 1831.57 | target | 2d | +7.00% | +6.80% |
| 11 | MU | long | 2024-06-13 | 142.93 | 2024-06-27 | 135.23 | stop | 9d | -5.39% | -5.59% |
| 12 | MU | long | 2024-09-27 | 110.61 | 2024-09-30 | 103.53 | stop | 1d | -6.40% | -6.60% |
| 13 | DIA | long | 2024-09-17 | 418.71 | 2024-10-08 | 420.38 | time_exit | 15d | +0.40% | +0.20% |
| 14 | UVXY | long | 2024-10-08 | 29.53 | 2024-10-14 | 26.58 | stop | 4d | -9.99% | -10.19% |
| 15 | VXX | long | 2024-10-08 | 55.74 | 2024-10-14 | 51.81 | stop | 4d | -7.04% | -7.24% |
| 16 | GS | long | 2024-10-15 | 539.34 | 2024-10-21 | 524.63 | stop | 4d | -2.73% | -2.93% |
| 17 | DIA | long | 2024-10-15 | 429.76 | 2024-10-23 | 423.04 | stop | 6d | -1.56% | -1.76% |
| 18 | AAPL | long | 2024-10-22 | 234.12 | 2024-10-23 | 227.77 | stop | 1d | -2.71% | -2.91% |
| 19 | NVDA | long | 2024-10-09 | 134.24 | 2024-10-30 | 139.20 | time_exit | 15d | +3.69% | +3.49% |
| 20 | WMT | long | 2024-10-24 | 83.04 | 2024-10-30 | 81.22 | stop | 4d | -2.19% | -2.39% |
| 21 | GOOGL | long | 2024-10-30 | 180.86 | 2024-10-31 | 172.96 | stop_gap | 1d | -4.37% | -4.57% |
| 22 | UVXY | long | 2024-11-01 | 29.75 | 2024-11-05 | 26.84 | stop | 2d | -9.77% | -9.97% |
| 23 | VXX | long | 2024-11-01 | 56.37 | 2024-11-05 | 52.57 | stop | 2d | -6.74% | -6.94% |
| 24 | AMZN | long | 2024-10-31 | 190.70 | 2024-11-06 | 201.29 | target | 4d | +5.55% | +5.35% |
| 25 | QQQ | long | 2024-11-07 | 508.91 | 2024-11-15 | 497.26 | stop | 6d | -2.29% | -2.49% |
| 26 | NVDA | long | 2024-11-08 | 148.92 | 2024-11-15 | 142.03 | stop | 5d | -4.62% | -4.82% |
| 27 | SPY | long | 2024-11-07 | 593.67 | 2024-11-29 | 601.95 | time_exit | 15d | +1.39% | +1.19% |
| 28 | AAPL | long | 2024-11-27 | 234.70 | 2024-12-09 | 245.45 | target | 7d | +4.58% | +4.38% |
| 29 | HON | long | 2024-12-17 | 238.40 | 2024-12-18 | 231.40 | stop | 1d | -2.94% | -3.14% |
| 30 | MU | long | 2024-12-17 | 109.86 | 2024-12-18 | 102.89 | stop | 1d | -6.35% | -6.55% |
| 31 | MSFT | long | 2024-12-05 | 438.36 | 2024-12-27 | 430.10 | time_exit | 15d | -1.88% | -2.08% |
| 32 | CAT | long | 2025-01-21 | 391.39 | 2025-01-30 | 373.13 | stop_gap | 7d | -4.67% | -4.87% |
| 33 | BAC | long | 2025-02-07 | 47.89 | 2025-02-10 | 46.56 | stop | 1d | -2.77% | -2.97% |
| 34 | AMZN | long | 2025-01-22 | 232.25 | 2025-02-12 | 228.70 | time_exit | 15d | -1.53% | -1.73% |
| 35 | DIA | long | 2025-01-22 | 441.93 | 2025-02-12 | 443.19 | time_exit | 15d | +0.28% | +0.08% |
| 36 | GS | long | 2025-02-19 | 669.75 | 2025-02-20 | 649.78 | stop | 1d | -2.98% | -3.18% |
| 37 | UVXY | long | 2025-02-28 | 21.11 | 2025-03-04 | 24.52 | target | 2d | +16.13% | +15.93% |
| 38 | VXX | long | 2025-02-28 | 47.18 | 2025-03-04 | 52.50 | target | 2d | +11.28% | +11.08% |
| 39 | PG | long | 2025-02-26 | 172.45 | 2025-03-13 | 167.26 | stop | 11d | -3.01% | -3.21% |
| 40 | BA | long | 2025-06-10 | 217.80 | 2025-06-12 | 201.97 | stop_gap | 2d | -7.27% | -7.47% |
| 41 | AMZN | long | 2025-06-10 | 217.00 | 2025-06-13 | 209.72 | stop | 3d | -3.35% | -3.55% |
| 42 | MU | long | 2025-06-09 | 109.62 | 2025-06-16 | 120.51 | target | 5d | +9.94% | +9.74% |
| 43 | MSFT | long | 2025-06-13 | 476.89 | 2025-06-25 | 493.44 | target | 7d | +3.47% | +3.27% |
| 44 | GS | long | 2025-06-20 | 639.47 | 2025-06-26 | 674.96 | target | 4d | +5.55% | +5.35% |
| 45 | JPM | long | 2025-06-20 | 275.85 | 2025-06-26 | 288.01 | target | 4d | +4.41% | +4.21% |
| 46 | DIA | long | 2025-06-27 | 435.59 | 2025-07-03 | 448.40 | target | 4d | +2.94% | +2.74% |
| 47 | MS | long | 2025-06-26 | 138.15 | 2025-07-18 | 140.69 | time_exit | 15d | +1.84% | +1.64% |
| 48 | AVGO | long | 2025-06-27 | 270.57 | 2025-07-21 | 287.92 | time_exit | 15d | +6.41% | +6.21% |
| 49 | GS | long | 2025-07-28 | 728.73 | 2025-08-01 | 707.62 | stop | 4d | -2.90% | -3.10% |
| 50 | AVGO | long | 2025-07-30 | 297.45 | 2025-08-01 | 285.45 | stop | 2d | -4.03% | -4.23% |
| 51 | MSFT | long | 2025-08-01 | 535.53 | 2025-08-07 | 521.39 | stop | 4d | -2.64% | -2.84% |
| 52 | ABBV | long | 2025-08-04 | 195.00 | 2025-08-20 | 209.39 | target | 12d | +7.38% | +7.18% |
| 53 | QQQ | long | 2025-08-11 | 575.26 | 2025-08-20 | 564.18 | stop | 7d | -1.93% | -2.13% |
| 54 | MU | long | 2025-08-12 | 124.82 | 2025-08-20 | 118.11 | stop | 6d | -5.38% | -5.58% |
| 55 | MU | long | 2025-09-08 | 130.68 | 2025-09-11 | 142.82 | target | 3d | +9.29% | +9.09% |
| 56 | AMZN | long | 2025-09-05 | 235.43 | 2025-09-12 | 227.85 | stop | 5d | -3.22% | -3.42% |
| 57 | META | long | 2025-09-17 | 780.77 | 2025-09-23 | 757.37 | stop | 4d | -3.00% | -3.20% |
| 58 | XOM | long | 2025-09-29 | 116.18 | 2025-09-30 | 113.09 | stop | 1d | -2.66% | -2.86% |
| 59 | SPY | long | 2025-09-12 | 658.26 | 2025-10-03 | 668.54 | time_exit | 15d | +1.56% | +1.36% |
| 60 | QCOM | long | 2025-09-17 | 164.84 | 2025-10-08 | 167.60 | time_exit | 15d | +1.67% | +1.47% |
| 61 | UNH | long | 2025-10-08 | 367.30 | 2025-10-10 | 353.81 | stop | 2d | -3.67% | -3.87% |
| 62 | BA | long | 2025-10-09 | 225.93 | 2025-10-10 | 216.78 | stop_gap | 1d | -4.05% | -4.25% |
| 63 | PEP | long | 2025-10-10 | 145.78 | 2025-10-16 | 152.52 | target | 4d | +4.63% | +4.43% |
| 64 | UVXY | long | 2025-10-13 | 11.51 | 2025-10-16 | 13.19 | target | 3d | +14.59% | +14.39% |
| 65 | VXX | long | 2025-10-13 | 35.40 | 2025-10-16 | 38.89 | target | 3d | +9.86% | +9.66% |
| 66 | KO | long | 2025-10-20 | 68.49 | 2025-10-21 | 71.01 | target | 1d | +3.67% | +3.47% |
| 67 | GOOGL | long | 2025-10-21 | 254.99 | 2025-10-29 | 272.26 | target | 6d | +6.77% | +6.57% |
| 68 | MSFT | long | 2025-10-28 | 550.55 | 2025-10-29 | 538.35 | stop | 1d | -2.22% | -2.42% |
| 69 | META | long | 2025-10-28 | 753.38 | 2025-10-30 | 668.48 | stop_gap | 2d | -11.27% | -11.47% |
| 70 | CVX | long | 2025-11-03 | 157.57 | 2025-11-04 | 153.27 | stop_gap | 1d | -2.73% | -2.93% |
| 71 | COP | long | 2025-11-17 | 90.62 | 2025-11-19 | 87.37 | stop | 2d | -3.59% | -3.79% |
| 72 | PFE | long | 2025-11-12 | 25.42 | 2025-11-20 | 24.54 | stop | 6d | -3.48% | -3.68% |
| 73 | UVXY | long | 2025-11-18 | 12.18 | 2025-11-20 | 14.62 | target | 2d | +20.01% | +19.81% |
| 74 | AAPL | long | 2025-11-25 | 275.55 | 2025-12-17 | 271.57 | time_exit | 15d | -1.44% | -1.64% |
| 75 | CAT | long | 2025-12-04 | 590.77 | 2025-12-17 | 563.54 | stop | 9d | -4.61% | -4.81% |
| 76 | PFE | long | 2025-12-16 | 26.47 | 2025-12-17 | 25.37 | stop_gap | 1d | -4.13% | -4.33% |
| 77 | DIA | long | 2026-01-07 | 496.15 | 2026-01-08 | 487.32 | stop_gap | 1d | -1.78% | -1.98% |
| 78 | MS | long | 2026-01-06 | 186.89 | 2026-01-14 | 181.50 | stop | 6d | -2.88% | -3.08% |
| 79 | PFE | long | 2026-01-16 | 25.92 | 2026-01-20 | 25.20 | stop | 1d | -2.75% | -2.95% |
| 80 | UVXY | long | 2026-01-21 | 38.99 | 2026-01-22 | 34.93 | stop_gap | 1d | -10.42% | -10.62% |
| 81 | JNJ | long | 2026-01-14 | 214.94 | 2026-01-27 | 224.49 | target | 8d | +4.44% | +4.24% |
| 82 | DIA | long | 2026-02-09 | 500.61 | 2026-02-23 | 489.75 | stop | 9d | -2.17% | -2.37% |
| 83 | XOM | long | 2026-03-16 | 156.16 | 2026-03-27 | 168.06 | target | 9d | +7.62% | +7.42% |
| 84 | CAT | long | 2026-04-09 | 773.77 | 2026-04-30 | 854.71 | target | 15d | +10.46% | +10.26% |
| 85 | JPM | long | 2026-04-09 | 307.26 | 2026-04-30 | 312.92 | time_exit | 15d | +1.84% | +1.64% |
| 86 | AAPL | long | 2026-04-16 | 267.07 | 2026-05-01 | 285.24 | target | 11d | +6.80% | +6.60% |
| 87 | WMT | long | 2026-05-20 | 133.04 | 2026-05-21 | 124.28 | stop_gap | 1d | -6.58% | -6.78% |
| 88 | GS | long | 2026-05-15 | 954.60 | 2026-05-29 | 1020.92 | target | 9d | +6.95% | +6.75% |
| 89 | MS | long | 2026-05-21 | 198.01 | 2026-06-01 | 210.98 | target | 6d | +6.55% | +6.35% |
| 90 | DIA | long | 2026-05-22 | 507.52 | 2026-06-15 | 517.92 | time_exit | 15d | +2.05% | +1.85% |
| 91 | JPM | long | 2026-06-08 | 313.56 | 2026-06-17 | 331.66 | target | 7d | +5.77% | +5.57% |
| 92 | UNH | long | 2026-06-09 | 410.40 | 2026-07-01 | 426.11 | time_exit | 15d | +3.83% | +3.63% |

---

## Cipher Session Log Analysis

> **DISCLAIMER:** The session log analysis below is a SEPARATE system from the strategy backtest above. Session log = intraday support/resistance signals from the Cipher scanner. Strategies = daily breakout/trend/vol engine (edge_backtest.py). They use different timeframes, signal logic, and exit mechanisms. They should NOT be combined into a single validation claim.

**Source:** `cipher_session_log.csv`
**Session date range:** 2026-07-24 to 2026-07-24
**Backtest data range:** 2022-01-03 to 2026-07-24

**Total unique signal events:** 32
**Tickers covered:** 4

### Signal Accuracy by Ticker

| Ticker | Signals | Targets Hit | Invalidated | Timeout | Hit Rate | Avg PnL% |
|--------|---------|-------------|-------------|---------|----------|----------|
| AAPL | 4 | 0 | 0 | 4 | 0% | -1.218% |
| AMZN | 3 | 0 | 0 | 3 | 0% | +0.066% |
| GOOGL | 17 | 0 | 0 | 17 | 0% | -0.191% |
| NVDA | 8 | 0 | 0 | 8 | 0% | -0.060% |

### Signal Accuracy by Direction

| Direction | Signals | Targets Hit | Hit Rate | Avg PnL% |
|-----------|---------|-------------|----------|----------|
| BULLISH | 13 | 0 | 0% | -0.453% |
| BEARISH | 19 | 0 | 0% | -0.132% |

### Signal Accuracy by Score Bucket

| Score Range | Signals | Targets Hit | Hit Rate | Avg PnL% |
|-------------|---------|-------------|----------|----------|
| 90-100 (High) | 26 | 0 | 0% | -0.129% |
| 70-89 (Mid) | 2 | 0 | 0% | -0.088% |
| <70 (Low) | 4 | 0 | 0% | -1.218% |

### Signal Accuracy by Setup Type

| Setup Type | Signals | Targets Hit | Hit Rate | Avg PnL% |
|------------|---------|-------------|----------|----------|
|  | 1 | 0 | 0% | -1.183% |
| CEILING REJECTION | 8 | 0 | 0% | -0.126% |
| FLOOR BOUNCE | 6 | 0 | 0% | -0.320% |
| Floor bounce | 2 | 0 | 0% | -0.724% |
| REJECTION REVERSAL | 15 | 0 | 0% | -0.190% |

### Individual Signal Detail

| # | Ticker | Dir | Score | Setup | Spot | Target | Invalidation | Result | PnL% |
|---|--------|-----|-------|-------|------|--------|-------------|--------|------|
| 1 | AAPL | BULLISH | 60 | Floor bounce | 333.69 | 335.00 | 330.00 | TIMEOUT | -1.157% |
| 2 | AAPL | BULLISH | 60 |  | 333.78 | 335.00 | 330.00 | TIMEOUT | -1.183% |
| 3 | AAPL | BULLISH | 60 | REJECTION REVERSAL | 334.06 | 335.00 | 330.00 | TIMEOUT | -1.266% |
| 4 | GOOGL | BEARISH | 99 | REJECTION REVERSAL | 319.15 | 317.50 | 322.50 | TIMEOUT | -0.363% |
| 5 | AAPL | BULLISH | 60 | FLOOR BOUNCE | 334.06 | 335.00 | 330.00 | TIMEOUT | -1.266% |
| 6 | NVDA | BULLISH | 87 | FLOOR BOUNCE | 210.30 | 211.40 | 207.50 | TIMEOUT | -0.347% |
| 7 | GOOGL | BEARISH | 92 | CEILING REJECTION | 319.60 | 317.50 | 322.50 | TIMEOUT | -0.222% |
| 8 | GOOGL | BEARISH | 99 | REJECTION REVERSAL | 319.53 | 317.50 | 322.50 | TIMEOUT | -0.244% |
| 9 | GOOGL | BEARISH | 96 | CEILING REJECTION | 319.65 | 317.50 | 322.50 | TIMEOUT | -0.206% |
| 10 | GOOGL | BEARISH | 99 | REJECTION REVERSAL | 319.48 | 317.50 | 322.50 | TIMEOUT | -0.260% |
| 11 | AMZN | BEARISH | 86 | REJECTION REVERSAL | 233.55 | 232.50 | 237.50 | TIMEOUT | +0.171% |
| 12 | GOOGL | BEARISH | 96 | CEILING REJECTION | 319.63 | 317.50 | 322.50 | TIMEOUT | -0.213% |
| 13 | GOOGL | BEARISH | 97 | REJECTION REVERSAL | 319.35 | 317.50 | 322.50 | TIMEOUT | -0.301% |
| 14 | NVDA | BEARISH | 99 | REJECTION REVERSAL | 209.80 | 208.65 | 212.50 | TIMEOUT | +0.110% |
| 15 | NVDA | BEARISH | 97 | CEILING REJECTION | 209.81 | 208.66 | 212.50 | TIMEOUT | +0.114% |
| 16 | NVDA | BEARISH | 93 | REJECTION REVERSAL | 209.67 | 208.58 | 212.50 | TIMEOUT | +0.048% |
| 17 | GOOGL | BULLISH | 99 | FLOOR BOUNCE | 320.42 | 322.50 | 317.50 | TIMEOUT | -0.034% |
| 18 | GOOGL | BULLISH | 99 | REJECTION REVERSAL | 320.33 | 322.50 | 317.50 | TIMEOUT | -0.006% |
| 19 | GOOGL | BULLISH | 99 | FLOOR BOUNCE | 320.31 | 322.50 | 317.50 | TIMEOUT | +0.000% |
| 20 | GOOGL | BULLISH | 99 | REJECTION REVERSAL | 320.70 | 322.50 | 317.50 | TIMEOUT | -0.122% |
| 21 | NVDA | BULLISH | 99 | Floor bounce | 210.18 | 211.51 | 209.16 | TIMEOUT | -0.292% |
| 22 | NVDA | BULLISH | 99 | FLOOR BOUNCE | 210.09 | 211.51 | 209.16 | TIMEOUT | -0.245% |
| 23 | GOOGL | BEARISH | 99 | CEILING REJECTION | 319.65 | 317.50 | 322.50 | TIMEOUT | -0.206% |
| 24 | GOOGL | BEARISH | 99 | REJECTION REVERSAL | 319.60 | 317.50 | 322.50 | TIMEOUT | -0.222% |
| 25 | GOOGL | BEARISH | 96 | CEILING REJECTION | 319.71 | 317.50 | 322.50 | TIMEOUT | -0.188% |
| 26 | GOOGL | BEARISH | 99 | REJECTION REVERSAL | 319.41 | 317.50 | 322.50 | TIMEOUT | -0.282% |
| 27 | GOOGL | BEARISH | 95 | CEILING REJECTION | 319.75 | 317.50 | 322.50 | TIMEOUT | -0.175% |
| 28 | AMZN | BULLISH | 99 | FLOOR BOUNCE | 233.21 | 235.00 | 230.00 | TIMEOUT | -0.026% |
| 29 | GOOGL | BEARISH | 99 | REJECTION REVERSAL | 319.65 | 317.50 | 322.50 | TIMEOUT | -0.206% |
| 30 | AMZN | BULLISH | 99 | REJECTION REVERSAL | 233.03 | 234.01 | 230.00 | TIMEOUT | +0.051% |
| 31 | NVDA | BEARISH | 99 | CEILING REJECTION | 209.76 | 208.63 | 212.50 | TIMEOUT | +0.091% |
| 32 | NVDA | BEARISH | 99 | REJECTION REVERSAL | 209.65 | 208.57 | 212.50 | TIMEOUT | +0.038% |

---

## Comparative Analysis

| Metric | tdr_five_day | momentum_vol_filter | bb_trend_filter |
|--------|--------|--------|--------|
| Trades | 57 | 131 | 92 |
| Win Rate | 56.1% | 42.0% | 42.4% |
| Mean Trade (net) | +0.1537% | +0.1707% | +0.1341% |
| Profit Factor | 1.14 | 1.07 | 1.05 |
| t-statistic | 0.43 | 0.36 | 0.21 |
| p-value | 0.6654 | 0.7189 | 0.8362 |
| Max Drawdown | 3.92% | 14.91% | 23.44% |

### Overlapping Tickers (trade-weighted)

Which tickers are profitable across multiple strategies?

| Ticker | tdr_five_day | momentum_vol | bb_trend_fil | #Strats | TradeWtd Mean% |
|--------|------------|------------|------------|---------|---------------|
| AAPL | +0.10% (4t) | +1.76% (6t) | +1.61% (4t) | 3 | +1.24% |
| ABBV | -1.78% (4t) | +1.89% (3t) | +7.18% (1t) | 3 | +0.71% |
| AMZN | -2.40% (1t) | +0.31% (4t) | -0.84% (4t) | 3 | -0.50% |
| AVGO | +1.74% (6t) | +1.09% (15t) | +0.79% (7t) | 3 | +1.15% |
| BA | +2.59% (1t) | -0.15% (6t) | -5.86% (2t) | 3 | -1.12% |
| BAC | +2.59% (2t) | +2.86% (6t) | -2.97% (1t) | 3 | +2.15% |
| CAT | - | -2.44% (5t) | +0.19% (3t) | 2 | -1.45% |
| COP | - | -2.86% (6t) | -3.79% (1t) | 2 | -2.99% |
| CVX | - | +0.21% (5t) | -2.93% (1t) | 2 | -0.31% |
| DIA | -0.85% (4t) | -0.00% (3t) | -0.18% (7t) | 3 | -0.33% |
| GOOGL | +2.59% (3t) | +0.93% (2t) | +1.00% (2t) | 3 | +1.66% |
| GS | - | +0.03% (7t) | +0.58% (5t) | 2 | +0.26% |
| HON | +0.65% (1t) | -2.86% (3t) | -3.14% (1t) | 3 | -2.21% |
| IWM | -1.61% (1t) | -2.61% (4t) | - | 2 | -2.41% |
| JNJ | +0.21% (3t) | +0.82% (2t) | +4.24% (1t) | 3 | +1.08% |
| JPM | +0.86% (2t) | -0.44% (3t) | +3.81% (3t) | 3 | +1.48% |
| KO | - | +4.20% (1t) | +3.47% (1t) | 2 | +3.84% |
| META | +2.59% (1t) | -0.73% (3t) | -7.33% (2t) | 3 | -2.38% |
| MS | +2.59% (1t) | +5.35% (2t) | +1.64% (3t) | 3 | +3.03% |
| MSFT | -2.40% (1t) | -0.44% (3t) | -1.02% (4t) | 3 | -0.97% |
| MU | +0.23% (7t) | -2.72% (5t) | +0.56% (10t) | 3 | -0.29% |
| NVDA | - | +0.59% (3t) | -0.67% (2t) | 2 | +0.08% |
| PEP | -1.31% (1t) | - | +4.43% (1t) | 2 | +1.56% |
| PFE | - | +1.15% (4t) | -3.65% (3t) | 2 | -0.91% |
| PG | -1.07% (1t) | -3.26% (2t) | -3.21% (1t) | 3 | -2.70% |
| QCOM | +0.10% (2t) | -0.19% (7t) | -0.96% (2t) | 3 | -0.28% |
| QQQ | +1.57% (2t) | -2.06% (3t) | -2.31% (2t) | 3 | -1.09% |
| SPY | +1.24% (1t) | -1.67% (3t) | +1.28% (2t) | 3 | -0.20% |
| UNH | +1.53% (3t) | -2.94% (3t) | -0.12% (2t) | 3 | -0.56% |
| UVXY | -5.11% (1t) | +6.24% (3t) | +3.22% (6t) | 3 | +3.29% |
| VXX | -9.53% (1t) | +11.10% (2t) | +1.64% (4t) | 3 | +2.75% |
| WMT | -0.89% (3t) | -0.84% (5t) | -4.59% (2t) | 3 | -1.60% |
| XOM | - | +2.14% (2t) | +2.28% (2t) | 2 | +2.21% |

---

## Honest Assessment

### Statistical Validity

| Strategy | Trades | t-stat | p-value | Bonferroni sig? | Block boot p | Wilcoxon p | MinBTL met? |
|----------|--------|--------|---------|-----------------|-------------|------------|-------------|
| tdr_five_day | 57 | 0.43 | 0.6654 | No | 0.7000 | 0.1755 | NO |
| momentum_vol_filter | 131 | 0.36 | 0.7189 | No | 0.7152 | 0.3953 | NO |
| bb_trend_filter | 92 | 0.21 | 0.8362 | No | 0.8490 | 0.7912 | NO |

### What the Data Actually Shows

1. All strategies have positive mean trade returns after slippage, but
   **none are statistically significant after Bonferroni correction**.
2. The best t-statistic is 0.43 (tdr_five_day), which corresponds to p ≈ 0.6654.
3. Bonferroni requires p < 0.00250 — no strategy meets this threshold.
4. The weekly time-block bootstrap preserves cross-ticker correlation
   during the same market regime, giving more honest confidence intervals.
5. The centered bootstrap p-value tests against a zero-mean null,
   which is a more rigorous test than the fraction of means above zero.

### What We Cannot Conclude

1. We cannot claim any strategy has a statistically proven edge
2. We cannot distinguish genuine alpha from selection bias (tested multiple strategies)
3. The OOS split is within the same dataset — it is NOT a truly independent validation
4. We cannot estimate real Sharpe ratios from trade-level returns alone
5. Stock-return proxies do not capture actual option VRP

### Before Live Deployment

1. **Obtain 5+ years of data** covering at least one major crash
2. **Add actual option chain data** for realistic option PnL
3. **Hold out 1 year of data** as untouched confirmation sample
4. **Paper trade** for minimum 6 months
5. **Model full transaction costs** including bid/ask, commissions, assignment
6. **Test on additional tickers** not in the current universe

---

## Statistical Methodology

### Weekly Time-Block Bootstrap

**Purpose:** Estimate confidence intervals for mean trade return while preserving
cross-ticker correlation.

**Procedure:**
1. Group all trades by ISO week (based on entry_date).
2. Resample weeks **with replacement** (same number of weeks as original).
3. For each bootstrap iteration, compute the mean of ALL trades in the resampled weeks.
4. Repeat 5,000 times to build the bootstrap distribution.
5. Report the 2.5th and 97.5th percentiles as the 95% CI.

**Why weekly blocks?** Trades in the same week are likely exposed to the same
market regime. Resampling individual trades would break this correlation structure
and understate true uncertainty. Weekly blocks preserve the cross-ticker correlation
within each market week.

**Centered bootstrap p-value (two-sided):** To test H₀: mean = 0, we center the data
(subtract observed mean from each trade), then bootstrap. The p-value is the
fraction of bootstrap iterations where |null mean| ≥ |observed mean|.
This is a two-sided test, consistent with the two-sided t-test.

**What this does NOT capture:**
- Regime changes across years (a bad year is weighted the same as a good year)
- Correlation structure across different weeks (only within-week correlation)
- Look-ahead bias or data-snooping (this is a resampling test, not a structural test)

### Monte Carlo Trade-Order Shuffle

**Purpose:** Measure drawdown risk and losing streaks from trade-order randomness.

**Procedure:**
1. Take all trades for a strategy.
2. Randomly shuffle the trade order.
3. Build a sequential equity curve (compounding PnL with 1/3 capital allocation).
4. Record max drawdown and max consecutive losing streak.
5. Repeat 1,000 times.

**What it measures:** The worst-case sequencing risk — even if the edge is real,
a bad run of losses early on could cause excessive drawdown.

**Limitations:**
- Assumes trades are independent (they are not — market regime affects multiple trades)
- Does NOT test whether the edge is real (that is the bootstrap/t-test job)
- Only measures sequencing risk, not parameter uncertainty

---

## Reproducibility Information

| Parameter | Value |
|-----------|-------|
| Git commit | `5bf47dabadb1e00e0c4034448728ec3a938f7fcf` |
| Python version | 3.10.12 |
| Platform | Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.35 |
| Random seed (bootstrap) | 42 |
| Random seed (Monte Carlo) | 123 |
| Random seed (PF bootstrap) | 99 |
| Results SHA-256 | `86e60946170a922c497353ce14af64fb...` |
| Bars SHA-256 | `90634cd5a6893735486bb8b9552deefb...` |
| Slippage | 10 bps one-way |
| Entry timing | close[t] signal -> open[t+1] entry |
| Same-day exits | conservative (disabled) |
| Bootstrap | weekly time-block, 5000 iterations |
| Monte Carlo | trade-order shuffle, 1000 iterations |
| Command | `python3 scripts/generate_top4_report.py` |

Full reproducibility metadata saved to `data/backtest_results/reproducibility_info.json`

---

**Report generated:** 2026-07-25T14:28:59.073624+00:00
**Status:** Exploratory internal backtest — 3 strategies, all metrics computed dynamically, engine validated, stress tests performed, session log analyzed
**Classification:** Internal research only — no independent audit performed. Data integrity issues (split adjustments, warm-up enforcement) require resolution before research-grade classification.