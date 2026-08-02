# Expanded Strategy Backtest Report

**Generated:** 2026-07-24T21:48:49.357134Z
**Universe:** 33 tickers, 18450 bars
**Period:** 2024-07 to 2026-07 (~2 years)
**Buy-and-Hold avg:** +42.90%

---

## Executive Summary

Tested 15 variants of the top 5 original strategies on an expanded 33-ticker universe.

### Key Findings

| Finding | Detail |
|---------|--------|
| **vrs_both_regimes is the best new strategy** | MeanTr +0.78%, WinR 80.6%, PF 2.05, P(profit) 96.8% |
| **Edge is concentrated in semis** | QCOM +2.14%, MU +1.52%, AVGO +1.90% (all 100% win rate) |
| **Only 36 trades total** | Statistically underpowered — MinBTL not met |
| **Walk-forward: 1/4 folds** | Only fold 3 had trades (18 trades, MeanTr +0.56%) |
| **Original momentum_vol_filter still solid** | MeanTr +0.30%, 129 trades, PF 1.16 |
| **Most variants underperform originals** | Tighter filters don't help — they overfit |

---

## Strategy Rankings by Trade Quality (Mean Trade Return)

Portfolio Sharpe is misleading for these strategies. Mean trade return,
win rate, and profit factor are the honest metrics.

| # | Strategy | Type | MeanTr% | WinR | PF | Trades | t-stat | P(profit) |
|---|----------|------|---------|------|----|--------|--------|-----------|
| 1 | vrs_atr_levels | VAR | +1.3725% | 55.0% | 1.78 | 20 | 1.11 | - |
| 2 | vrs_both_regimes | VAR | +0.7763% | 80.6% | 2.05 | 36 | 1.88 | - |
| 3 | momentum_vol_filter | ORIG | +0.3006% | 39.5% | 1.16 | 129 | 0.72 | - |
| 4 | breakout_20d | ORIG | +0.2262% | 47.4% | 1.10 | 95 | 0.37 | - |
| 5 | breakout_10d | VAR | -0.0737% | 51.7% | 0.96 | 120 | -0.15 | - |
| 6 | three_day_reversal | ORIG | -0.3141% | 38.2% | 0.61 | 314 | -3.70 | - |
| 7 | mvf_normal_allowed | VAR | -0.4734% | 39.6% | 0.83 | 169 | -1.00 | - |
| 8 | bollinger_squeeze | ORIG | -0.5347% | 40.2% | 0.79 | 102 | -0.97 | - |
| 9 | vol_regime_switch | ORIG | -0.6034% | 45.5% | 0.67 | 33 | -1.09 | - |
| 10 | breakout_50d | VAR | -0.6111% | 42.6% | 0.81 | 94 | -0.81 | - |
| 11 | bb_volume_confirm | VAR | -0.9837% | 38.7% | 0.68 | 62 | -1.25 | - |

---

## Best New Strategy: vrs_both_regimes — Deep Dive

### Strategy Logic

A vol_regime_switch variant that ALWAYS trades (both low and high vol):
- **Low vol regime:** Go long with 4% target, 2.5% stop, 15-day hold
- **High vol regime:** Go short with 2.5% target, 5% stop, 12-day hold

The key insight: in high vol, short-selling premium works because
vol mean-reverts. In low vol, momentum works because trends are clean.

### Full-Sample Results

| Metric | Value |
|--------|-------|
| Total Trades | 36 |
| Mean Trade Return | +0.7763% |
| Std Dev | 2.483% |
| Win Rate | 80.6% |
| Profit Factor | 2.05 |
| t-statistic | 1.88 |
| Bootstrap P(profit) | 96.8% |
| Bootstrap 95% CI | [-0.04%, +1.54%] |

### Per-Ticker Breakdown

| Ticker | Trades | MeanTr% | WinR |
|--------|--------|---------|------|
| AAPL | 5 | -1.028% | 60% |
| ABBV | 4 | +0.489% | 75% |
| AMZN | 5 | +0.535% | 80% |
| AVGO | 6 | +1.902% | 100% |
| BA | 2 | -0.630% | 50% |
| BAC | 2 | -1.809% | 0% |
| MU | 6 | +1.519% | 100% |
| QCOM | 6 | +2.136% | 100% |

**Key insight:** Edge is concentrated in semiconductors (QCOM, MU, AVGO).
Financials (BAC) and tech (AAPL) are losers.

---

## Original vs Variants Comparison

### vol_regime_switch family

| Strategy | MeanTr% | WinR | PF | Trades |
|----------|---------|------|----|--------|
| vrs_atr_levels | +1.3725% | 55.0% | 1.78 | 20 |
| vrs_both_regimes | +0.7763% | 80.6% | 2.05 | 36 |
| vol_regime_switch | -0.6034% | 45.5% | 0.67 | 33 |

### three_day_reversal family

| Strategy | MeanTr% | WinR | PF | Trades |
|----------|---------|------|----|--------|
| three_day_reversal | -0.3141% | 38.2% | 0.61 | 314 |

All TDR variants generated 0 trades (filters too strict).

### breakout family

| Strategy | MeanTr% | WinR | PF | Trades |
|----------|---------|------|----|--------|
| breakout_20d | +0.2262% | 47.4% | 1.10 | 95 |
| breakout_10d | -0.0737% | 51.7% | 0.96 | 120 |
| breakout_50d | -0.6111% | 42.6% | 0.81 | 94 |

The original 20d breakout remains the best in this family.

---

## Honest Assessment

### What Works

1. **vrs_both_regimes** — Best trade-level quality (80.6% WR, 2.05 PF, P(profit) 96.8%)
   - BUT: only 36 trades, concentrated in 8 tickers (mostly semis)
   - BUT: walk-forward shows only 1/4 folds with trades
   - **Verdict:** Promising but NOT validated for live deployment

2. **momentum_vol_filter** — Most consistent original strategy
   - MeanTr +0.30%, 129 trades, PF 1.16
   - Positive mean return across many tickers
   - **Verdict:** Modest but genuine edge

3. **breakout_20d** — Classic Donchian with positive edge
   - MeanTr +0.23%, 95 trades, PF 1.10
   - Well-documented academic basis
   - **Verdict:** Modest edge, needs improvement

### What Doesn't Work

1. **three_day_reversal** — Negative mean return (-0.31%), t-stat -3.70
2. **bollinger_squeeze** — Negative mean return (-0.53%)
3. **vol_regime_switch** (original) — Negative mean return (-0.60%)
4. **Most variants** — Tighter filters reduce trades to 0 without improving quality
5. **trend filters** — Consistently eliminate all trades (too restrictive)

### The Core Problem

With 2 years of data on 33 tickers, we have ~500 bars per ticker.
Strategies generate 20-130 trades total across all tickers.
The MinBTL concept from the ORATS research says we need 5000+ trades.

**We are statistically underpowered for every strategy.**

The only strategy that approaches significance is vrs_both_regimes
(t-stat 1.88, p < 0.10), and even that has only 36 trades.

### Recommendations

1. **Expand data to 5+ years** — Need multiple market regimes
2. **Focus on semis for vrs_both_regimes** — That's where the edge lives
3. **Keep momentum_vol_filter as baseline** — Most consistent edge
4. **Add actual option chain data** — Stock proxies miss the real VRP
5. **Paper trade vrs_both_regimes** — 6 months live validation before capital

---

**Report generated:** 2026-07-24T21:48:49.357373Z
**Status:** EXPANDED — 15 variants of top 5 original strategies on 33-ticker universe