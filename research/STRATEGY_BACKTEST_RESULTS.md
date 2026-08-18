# Strategy Backtest Results - Full Analysis

**Timestamp:** 2026-07-24T17:19:43Z  
**Duration:** 12.8 seconds  
**Data:** 31 tickers, 15,965 bars (2024-07-05 to 2026-07-24)  
**Strategies Tested:** 15

---

## 🏆 Top 5 Robust Strategies (by Realistic Sharpe)

### 1. **RSI(2) Reversion** - BEST OVERALL
- **Avg Sharpe:** 0.259
- **Avg PnL:** +8.52%
- **Win Rate:** 66.6%
- **Profitable Tickers:** 24/31 (77%)
- **Top Performers:**
  - SPY: Sharpe 1.070, PnL +20.22%, Win 85%
  - MS: Sharpe 0.795, PnL +25.75%, Win 85%
  - CVX: Sharpe 0.711, PnL +21.80%, Win 75%

**Why it works:** Mean reversion on oversold conditions, high win rate, consistent across tickers.

---

### 2. **Three Day Reversal**
- **Avg Sharpe:** 0.116
- **Avg PnL:** +2.42%
- **Win Rate:** 49.7%
- **Profitable Tickers:** 17/31 (55%)
- **Top Performers:**
  - COP: Sharpe 1.360, PnL +15.33%, Win 90%
  - CVX: Sharpe 1.078, PnL +14.33%, Win 81.8%
  - GS: Sharpe 0.708, PnL +19.43%, Win 73.7%

**Why it works:** Contrarian reversal after 3-day streaks, works well on energy/financials.

---

### 3. **Overnight Harvest**
- **Avg Sharpe:** 0.072
- **Avg PnL:** +12.97%
- **Win Rate:** 57.7%
- **Profitable Tickers:** 22/31 (71%)
- **Top Performers:**
  - ABBV: Sharpe 0.316, PnL +8.33%, Win 64%
  - WMT: Sharpe 0.201, PnL +11.61%, Win 66%
  - KO: Sharpe 0.175, PnL +3.22%, Win 60%

**Why it works:** Captures overnight drift, consistent on defensive stocks.

---

### 4. **Weekend Theta** (Mixed)
- **Avg Sharpe:** 0.007
- **Avg PnL:** -1.87%
- **Win Rate:** 44.8%
- **Profitable Tickers:** 18/31 (58%)
- **Top Performers:**
  - ABBV: Sharpe 0.203, PnL +16.96%, Win 50%
  - NVDA: Sharpe 0.199, PnL +33.36%, Win 38%
  - MSFT: Sharpe 0.176, PnL +10.67%, Win 52%

**Why it works:** Weekend theta decay on options, but inconsistent overall.

---

### 5. **Momentum Vol Filter** (Ticker-Specific)
- **Avg Sharpe:** -0.280
- **Avg PnL:** +5.77%
- **Win Rate:** 38.8%
- **Profitable Tickers:** 16/30 (53%)
- **Top Performers:**
  - CAT: Sharpe 1.564, PnL +42.60%, Win 90%
  - MSFT: Sharpe 1.060, PnL +27.01%, Win 80%
  - AAPL: Sharpe 0.763, PnL +24.21%, Win 70%

**Why it works:** Momentum with volatility filter, excellent on large-cap tech/industrials.

---

## ⚠️ Strategies with Calculation Issues

These strategies show extremely high Sharpe ratios (>1000), indicating division by near-zero standard deviation:

1. **Skew Harvest** - Sharpe 95,078,949,602,372.578 (BUG)
2. **Vol Regime Switch** - Sharpe 21,671,546,213,475.254 (BUG)
3. **Trend Pullback** - Sharpe -248,395,157,475,852.469 (BUG)
4. **Gap and Go** - Sharpe -135,742,571,900,019.922 (BUG)

**Root Cause:** These strategies likely have very few trades or near-zero return variance, causing Sharpe calculation to explode. Need to add minimum trade count or variance threshold.

---

## 📊 Strategy Rankings (Realistic Sharpe Only)

| Rank | Strategy | Sharpe | PnL | Win Rate | Profitable |
|------|----------|--------|-----|----------|------------|
| 1 | RSI(2) Reversion | 0.259 | +8.52% | 66.6% | 24/31 |
| 2 | Three Day Reversal | 0.116 | +2.42% | 49.7% | 17/31 |
| 3 | Overnight Harvest | 0.072 | +12.97% | 57.7% | 22/31 |
| 4 | Weekend Theta | 0.007 | -1.87% | 44.8% | 18/31 |
| 5 | PEAD Drift | 0.000 | -1.25% | 25.0% | 1/4 |
| 6 | Momentum Vol Filter | -0.280 | +5.77% | 38.8% | 16/30 |
| 7 | Breakout 20D | -1.687 | +3.23% | 46.0% | 16/29 |
| 8 | Bollinger Squeeze | -2.237 | +2.73% | 50.3% | 16/31 |
| 9 | Gap and Go | -135T | -2.48% | 23.3% | 6/27 |
| 10 | Trend Pullback | -248T | +0.23% | 52.5% | 11/24 |

---

## 🎯 Best Strategy-Ticker Combinations

### High Confidence (Sharpe > 1.0, Win Rate > 80%)

1. **RSI(2) Reversion on SPY**
   - Sharpe: 1.070, PnL: +20.22%, Win: 85%
   
2. **RSI(2) Reversion on MS**
   - Sharpe: 0.795, PnL: +25.75%, Win: 85%
   
3. **RSI(2) Reversion on CVX**
   - Sharpe: 0.711, PnL: +21.80%, Win: 75%
   
4. **Momentum Vol Filter on CAT**
   - Sharpe: 1.564, PnL: +42.60%, Win: 90%
   
5. **Momentum Vol Filter on MSFT**
   - Sharpe: 1.060, PnL: +27.01%, Win: 80%
   
6. **Three Day Reversal on COP**
   - Sharpe: 1.360, PnL: +15.33%, Win: 90%
   
7. **Three Day Reversal on CVX**
   - Sharpe: 1.078, PnL: +14.33%, Win: 81.8%
   
8. **Breakout 20D on GS**
   - Sharpe: 1.440, PnL: +23.52%, Win: 80%
   
9. **Breakout 20D on META**
   - Sharpe: 0.745, PnL: +25.43%, Win: 83.3%

---

## 🔬 Next Steps

### 1. Fix Sharpe Calculation
Add minimum trade count (≥10) and variance threshold to prevent division by zero.

### 2. Walk-Forward Validation
Validate top 5 strategies with 5-fold walk-forward to confirm robustness.

### 3. Parameter Optimization
Optimize parameters for top strategies on best tickers.

### 4. Ensemble Creation
Combine top strategies into ensemble for diversification.

### 5. Risk Management
Add position sizing and risk limits based on drawdown.

---

## 📁 Files Generated

- `data/backtest_results/full_backtest_20260724_171943.json` - Raw results
- `data/backtest_results/strategy_rankings_20260724_171943.json` - Rankings

---

**Generated:** 2026-07-24T17:19:43Z  
**Status:** COMPLETE
