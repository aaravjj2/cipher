# 🚀 Cipher News Integration - Setup Guide

## Quick Start

### 1. Install Dependencies

```bash
pip install vaderSentiment textblob scikit-learn joblib
python -m textblob.download_corpora
```

### 2. Configure API Keys

**Option A: Environment Variables (Recommended)**
```bash
# Add to ~/.bashrc or ~/.bash_profile
export ALPACA_API_KEY="your_alpaca_key"
export ALPACA_API_SECRET="your_alpaca_secret"
export FINNHUB_API_KEY="your_finnhub_key"
export ALPHA_VANTAGE_KEY="your_alpha_vantage_key"
export TELEGRAM_BOT_TOKEN="your_telegram_token"
export TELEGRAM_CHAT_ID="your_telegram_chat_id"
```

**Option B: .env File**
Create `.env` in project root:
```env
ALPACA_API_KEY=your_alpaca_key
ALPACA_API_SECRET=your_alpaca_secret
FINNHUB_API_KEY=your_finnhub_key
ALPHA_VANTAGE_KEY=your_alpha_vantage_key
TELEGRAM_BOT_TOKEN=your_telegram_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

### 3. Get API Keys (Free Tiers)

**Alpaca** (Market Data):
1. Sign up: https://alpaca.markets/
2. Go to Dashboard → API Keys
3. Copy Key ID and Secret Key
4. Free tier: 200 requests/minute

**Finnhub** (Company News):
1. Sign up: https://finnhub.io/
2. Go to Dashboard → API Key
3. Copy API key
4. Free tier: 60 calls/minute

**Alpha Vantage** (News + Sentiment):
1. Sign up: https://www.alphavantage.co/support/#api-key
2. Get free API key
3. Free tier: 5 calls/minute, 500 calls/day

**Telegram** (Alerts):
1. Message @BotFather on Telegram
2. Send: `/newbot`
3. Follow prompts to create bot
4. Copy bot token
5. Message your bot
6. Visit: https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
7. Find your chat_id in response

### 4. Test News Fetcher

```bash
# Test with SPY
python3 scripts/news_fetcher.py fetch SPY

# Check results
python3 scripts/news_fetcher.py list
```

### 5. Test Sentiment Analysis

```bash
# Analyze fetched news
python3 scripts/news_sentiment.py analyze

# View results
python3 scripts/news_sentiment.py show

# Check trend
python3 scripts/news_sentiment.py trend SPY 7
```

### 6. Test Enhanced Signals

```bash
# Run enhanced scan
python3 scripts/enhanced_signal_system.py SPY QQQ NVDA TSLA
```

### 7. Test Alerts

```bash
# Send test message
python3 scripts/alert_system.py test

# Check history
python3 scripts/alert_system.py history
```

### 8. Train ML Model

```bash
# After collecting outcomes (1+ week)
python3 scripts/ml_signal_model.py generate
python3 scripts/ml_signal_model.py train

# Check model info
python3 scripts/ml_signal_model.py info
```

---

## Daily Workflow

### Morning (9:00 AM ET)
```bash
# 1. Fetch overnight news
python3 scripts/news_fetcher.py fetch SPY QQQ NVDA TSLA

# 2. Analyze sentiment
python3 scripts/news_sentiment.py analyze

# 3. Run enhanced scan
python3 scripts/enhanced_signal_system.py SPY QQQ NVDA TSLA

# 4. Check alerts
python3 scripts/alert_system.py history
```

### Midday (12:00 PM ET)
```bash
# Refresh news and signals
python3 scripts/news_fetcher.py fetch SPY QQQ NVDA TSLA
python3 scripts/news_sentiment.py analyze
python3 scripts/enhanced_signal_system.py SPY QQQ NVDA TSLA
```

### Evening (4:00 PM ET)
```bash
# Update outcomes
python3 scripts/outcome_tracker.py update

# Generate report
python3 scripts/outcome_tracker.py report

# Check AO automation
tail -20 ao_automation/automation.log
```

### Weekly (Sunday)
```bash
# Retrain ML model
python3 scripts/ml_signal_model.py train

# Review performance
python3 scripts/outcome_tracker.py report

# Plan next week
cat NEXT_WAVE_BRAINSTORM.md
```

---

## Monitoring

### Check AO Automation
```bash
# View log
tail -f ao_automation/automation.log

# Check results
cat ao_automation/scan_results.json | jq '.[-1]'

# Count scans
cat ao_automation/scan_results.json | jq 'length'
```

### Check News Data
```bash
# List news files
ls -lh data/news/

# Count articles
cat data/news/news_*.json | jq 'length'

# Check sentiment
cat data/news/sentiment.json | jq 'length'
```

### Check Alerts
```bash
# View alert history
cat data/alerts.json | jq '.[-10:]'

# Count alerts by type
cat data/alerts.json | jq '[.[].alert_types[]] | group_by(.) | map({type: .[0], count: length})'
```

---

## Troubleshooting

### News Fetcher Returns 0 Articles
- Check API keys are set: `echo $ALPACA_API_KEY`
- Verify API keys are valid
- Check rate limits (wait 1 minute between requests)
- Check internet connection

### Sentiment Analysis Fails
- Install dependencies: `pip install vaderSentiment textblob`
- Download corpora: `python -m textblob.download_corpora`
- Check news data exists: `ls data/news/`

### Alerts Not Sending
- Verify Telegram credentials
- Test bot: Message @BotFather, then message your bot
- Check chat_id: Visit https://api.telegram.org/bot<TOKEN>/getUpdates
- Test: `python3 scripts/alert_system.py test`

### ML Model Training Fails
- Install dependencies: `pip install scikit-learn joblib`
- Check training data exists: `ls data/ml/`
- Need at least 50 samples with outcomes
- Check outcome_tracker.py has been run

---

## Performance Tips

### Speed Up News Fetching
- Reduce tickers: `fetch SPY QQQ` instead of 10 tickers
- Reduce limit: `fetch SPY --limit 20`
- Use fewer sources (disable Yahoo, FRED)

### Improve ML Accuracy
- Collect more training data (2+ weeks)
- Balance positive/negative samples
- Add more features (options flow, volume)
- Retrain weekly with new data

### Reduce Alert Fatigue
- Increase threshold: `score >= 80` instead of 70
- Only alert on QUAD clusters
- Only alert on major catalysts
- Set quiet hours (no alerts 10 PM - 8 AM)

---

## Cost Analysis

### Free Tier Limits
- **Alpaca**: 200 requests/min = ~288,000/day
- **Finnhub**: 60 calls/min = ~86,400/day
- **Alpha Vantage**: 500 calls/day
- **FRED**: Unlimited (free)

### Daily Usage (Estimated)
- Fetch news for 10 tickers: ~50 API calls
- Analyze sentiment: Local (no API)
- Run signals: Local (no API)
- Send alerts: Local (no API)

### Conclusion
**All free tiers are sufficient for daily use.** No paid upgrades needed.

---

## Data Storage

### News Articles
- Location: `data/news/news_YYYYMMDD_HHMMSS.json`
- Format: JSON array of articles
- Size: ~100 KB per fetch
- Retention: Keep all (small files)

### Sentiment Data
- Location: `data/news/sentiment.json`
- Format: JSON array of analyses
- Size: ~50 KB per analysis
- Retention: Keep all (small files)

### ML Models
- Location: `models/signal_model.json`
- Format: Pickle (joblib)
- Size: ~5 MB per model
- Retention: Keep last 5 models

### Alerts
- Location: `data/alerts.json`
- Format: JSON array
- Size: ~10 KB per 100 alerts
- Retention: Keep all (small files)

---

## Security

### API Keys
- Never commit `.env` to git
- Add `.env` to `.gitignore`
- Use environment variables in production
- Rotate keys every 90 days

### Telegram
- Keep bot token private
- Don't share chat_id
- Use bot only for personal alerts
- Revoke token if compromised

### Data
- All data is local (no cloud)
- No sensitive information stored
- Safe to commit data files
- Backup weekly

---

## Support

### Documentation
- `NEWS_INTEGRATION_COMPLETE.md` - Full feature list
- `NEXT_WAVE_BRAINSTORM.md` - Future roadmap
- `SESSION_COMPLETE.md` - Previous session summary

### Code
- `scripts/news_fetcher.py` - News fetching
- `scripts/news_sentiment.py` - Sentiment analysis
- `scripts/enhanced_signal_system.py` - Signal integration
- `scripts/ml_signal_model.py` - ML training
- `scripts/alert_system.py` - Telegram alerts

### Logs
- `ao_automation/automation.log` - AO automation
- `data/news/` - News data
- `data/alerts.json` - Alert history

---

**Ready to go!** Start with Step 1 and work through the setup guide.
