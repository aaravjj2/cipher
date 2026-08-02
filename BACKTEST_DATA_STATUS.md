# 📊 Backtest Data Download & Execution - Status Report

**Date:** July 24, 2026  
**Status:** ⚠️ **REQUIRES CONFIGURATION**

---

## 🎯 **What Was Attempted**

### **1. Download Historical Bar Data**
**Command:**
```bash
python3 scripts/bar_data_downloader.py download SPY QQQ NVDA TSLA AAPL MSFT 200
```

**Result:** ❌ Failed - Alpaca API key not configured

**Error:**
```
Downloading SPY bars (200 days, 1Day)...
  Alpaca API key not configured
  ✓ Downloaded 0 bars
```

---

## 🔧 **What's Needed**

### **Required: Alpaca API Credentials**

The bar data downloader requires Alpaca API credentials to fetch historical data.

**Option A: Environment Variables (Recommended)**
```bash
# Add to ~/.bashrc or ~/.bash_profile
export ALPACA_API_KEY="your_alpaca_key"
export ALPACA_API_SECRET="your_alpaca_secret"

# Then reload
source ~/.bashrc
```

**Option B: .env File**
Create `.env` in project root:
```env
ALPACA_API_KEY=your_alpaca_key
ALPACA_API_SECRET=your_alpaca_secret
```

**Get API Keys:**
1. Sign up at https://alpaca.markets/
2. Go to Dashboard → API Keys
3. Copy Key ID and Secret Key
4. Free tier: 200 requests/minute (sufficient)

---

## 📊 **Alternative: Use Existing Data**

### **Check Core API**

If the Cipher core API is running, it can provide bar data:

```bash
# Check if core is running
curl http://127.0.0.1:8282/api/bars?ticker=SPY&timeframe=1D&limit=200

# If running, start core:
cd cipher-system
python3 core/app.py
```

### **Check Existing Data**

Look for existing bar data:
```bash
# Check data directories
ls -la data/bars/
ls -la cipher-system/data/

# Check for parquet files
find . -name "*.parquet" | head -10
```

---

## 🚀 **Once Configured: Full Workflow**

### **Step 1: Download Bar Data**
```bash
# Download 200 days for major tickers
python3 scripts/bar_data_downloader.py download SPY QQQ NVDA TSLA AAPL MSFT 200

# Verify
python3 scripts/bar_data_downloader.py show
```

**Expected Output:**
```
================================================================================
DOWNLOADING HISTORICAL BARS
Tickers: SPY, QQQ, NVDA, TSLA, AAPL, MSFT
Period: 200 days
================================================================================

Downloading SPY bars (200 days, 1Day)...
  ✓ Downloaded 200 bars

Downloading QQQ bars (200 days, 1Day)...
  ✓ Downloaded 200 bars

...

================================================================================
DOWNLOAD COMPLETE
Total tickers: 6
Total bars: 1200
================================================================================
```

---

### **Step 2: Run Edge Strategy Backtests**

The edge backtester is ready in `cipher-system/core/edge_backtest.py`.

**Available Strategies (15 total):**
1. overnight_harvest
2. vol_risk_premium
3. gap_and_go
4. rsi2_reversion
5. bollinger_squeeze
6. breakout_20d
7. trend_pullback
8. three_day_reversal
9. momentum_vol_filter
10. vol_mean_reversion
11. skew_harvest
12. pead_drift
13. weekend_theta
14. vol_regime_switch
15. iv_rv_spread

**Run Backtest:**
```python
# Python script
from edge_backtest import run_edge_backtest, EDGE_STRATEGIES
from bar_data_downloader import get_bars_function

# Run top 5 strategies
strategies = ["overnight_harvest", "vol_risk_premium", "gap_and_go", "rsi2_reversion", "bollinger_squeeze"]

results = run_edge_backtest(
    bars_fn=get_bars_function,
    tickers=["SPY", "QQQ", "NVDA"],
    strategies=strategies,
    bars_limit=200,
)

# Print results
for strategy, data in results.items():
    metrics = data.get("metrics", {})
    print(f"{strategy}:")
    print(f"  Total PnL: ${metrics.get('total_pnl', 0):.2f}")
    print(f"  Win Rate: {metrics.get('win_rate', 0):.1%}")
    print(f"  Sharpe: {metrics.get('sharpe_ratio', 0):.2f}")
```

---

### **Step 3: Compare With/Without News**

```python
from comprehensive_strategy_backtest import compare_strategies

strategies = ["overnight_harvest", "rsi2_reversion", "bollinger_squeeze"]
tickers = ["SPY", "QQQ", "NVDA"]

results = compare_strategies(strategies, tickers, days=90)

# Identify best strategy
best = identify_best_strategy(results)
print(f"Best strategy: {best}")
```

---

## 📁 **Data Storage**

### **Bar Data Location:**
```
data/bars/
├── bars_20260724_132716.json  (empty - needs API keys)
└── bars_YYYYMMDD_HHMMSS.json  (will be created)
```

### **Expected File Size:**
- 6 tickers × 200 bars = 1,200 bars
- ~100 KB per ticker
- **Total: ~600 KB**

### **Data Format:**
```json
{
  "SPY": {
    "bars": [
      {
        "t": "2026-01-02T00:00:00Z",
        "o": 450.50,
        "h": 452.30,
        "l": 449.80,
        "c": 451.20,
        "v": 50000000,
        "vw": 451.00
      },
      ...
    ],
    "ticker": "SPY",
    "timeframe": "1Day"
  },
  ...
}
```

---

## 🎯 **Quick Start (After Configuration)**

```bash
# 1. Set API keys
export ALPACA_API_KEY="your_key"
export ALPACA_API_SECRET="your_secret"

# 2. Download data
python3 scripts/bar_data_downloader.py download SPY QQQ NVDA TSLA AAPL MSFT 200

# 3. Verify
python3 scripts/bar_data_downloader.py show

# 4. Run backtests
python3 scripts/comprehensive_strategy_backtest.py SPY QQQ NVDA

# 5. Start streaming
nohup python3 scripts/ao_signal_streamer.py start > /dev/null 2>&1 &

# 6. Monitor
python3 scripts/ao_signal_streamer.py status
```

---

## 🔍 **Troubleshooting**

### **Issue: "Alpaca API key not configured"**
**Solution:**
```bash
# Check if set
echo $ALPACA_API_KEY

# If empty, set it
export ALPACA_API_KEY="your_key"
export ALPACA_API_SECRET="your_secret"
```

### **Issue: "Core API not responding"**
**Solution:**
```bash
# Start core API
cd cipher-system
python3 core/app.py

# Or check if already running
curl http://127.0.0.1:8282/api/health
```

### **Issue: "No bar data found"**
**Solution:**
- Download data first (requires API keys)
- Or use existing data from `cipher-system/data/`

---

## 📊 **Expected Results**

### **After Download:**
- 1,200 bars across 6 tickers
- ~600 KB storage
- Ready for backtesting

### **After Backtest:**
- 15 strategies tested
- Performance metrics (PnL, win rate, Sharpe)
- Best strategy identified
- Comparison with/without news

### **After Streaming:**
- Continuous signal capture (every minute)
- ~7,200 signals/day
- ~720 KB/day storage
- Real-time signal database

---

## 🎉 **Summary**

### **Current Status:**
- ✅ Bar downloader created
- ✅ Signal streamer created
- ✅ Backtest infrastructure ready
- ⚠️ **Requires Alpaca API keys**

### **What's Blocked:**
- ❌ Cannot download bar data without API keys
- ❌ Cannot run backtests without data
- ✅ Signal streamer works (uses browser automation)

### **What's Working:**
- ✅ AO signal streamer (no API keys needed)
- ✅ News fetcher (needs API keys for full functionality)
- ✅ All analysis modules
- ✅ UI dashboard

---

## 🚀 **Next Steps**

1. **Configure Alpaca API keys**
2. **Download bar data**
3. **Run backtests**
4. **Compare strategies**
5. **Start signal streaming**
6. **Analyze results**

---

**Status:** ⚠️ **REQUIRES API CONFIGURATION**  
**Action Needed:** Set `ALPACA_API_KEY` and `ALPACA_API_SECRET` environment variables

---

*Built with ❤️ for Mars by Cipher Research Team*
