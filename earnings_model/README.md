# Earnings Model

Local-only, read-only earnings research and forecasting terminal for Cipher.
Ingests quarterly earnings reports (EPS actual/estimate, revenue, surprises, timing), computes post-earnings price reactions (gap %, 1-day, 1-week, 2-week returns, volume ratios), extracts pre/post earnings news sentiment via Alpaca & financial NLP, and trains lookahead-free ML predictive models to recommend defined-risk options strategies.

## Architecture

```
earnings_model/
├── __init__.py          # Package marker
├── __main__.py          # Entry point (python3 -m earnings_model)
├── cli.py               # Comprehensive CLI (collect, collect-news, train, predict, status, report)
├── config.py            # Constants, paths, batch sizes, and rate limits
├── universe.py          # Universe management (559 optionable tickers by cap tier)
├── db.py                # SQLite schema & CRUD (earnings_events, price_impact, news, metrics)
├── collector.py         # yfinance data ingestion + ETF detection + price impact engine
├── sentiment.py         # Loughran-McDonald inspired financial sentiment analyzer
├── news.py              # Alpaca news ingestion & pre/post earnings narrative scoring
├── model.py             # Machine learning predictive engine (Direction, Gap, Reversal models)
├── analyzer.py          # Statistical pattern profiling and composite scoring
├── report.py            # Markdown report generation & CSV dataset export
└── data/
    ├── earnings.sqlite  # Main local SQLite database
    └── earnings_models.joblib # Serialized ML pipeline artifacts
```

## Data Sources

| Source | Data Extracted | Coverage & Notes |
|---|---|---|
| **yfinance** | EPS actual & estimate, surprise %, report timing (BMO/AMC) | Up to 40 quarters per ticker |
| **yfinance** | Quarterly income statement (Revenue, Net income, Diluted EPS) | Point-in-time quarter matching |
| **yfinance / Alpaca** | Historical OHLCV bars for pre-drift & post-impact calculations | 5-day / 20-day pre & post returns |
| **Alpaca News API** | Historical news headlines, summaries, and timestamps | Pre-earnings window & post-reaction |

## Database Schema

- `earnings_events`: symbol, earnings_date, fiscal_quarter, timing (BMO/AMC), eps_estimate, eps_actual, eps_surprise_pct, revenue, net_income, diluted_eps, cap_tier.
- `price_impact`: symbol, earnings_date, pre_close, pre_5d_return_pct, pre_20d_return_pct, post_open, post_close, post_high, post_low, post_volume, gap_pct, day1_return_pct, day1_range_pct, day5_return_pct, day10_return_pct, day20_return_pct, volume_ratio.
- `earnings_news`: symbol, earnings_date, news_id, headline, summary, created_at, timing_rel (pre/post), sentiment_score (-1.0 to +1.0), sentiment_label, uncertainty_ratio.
- `earnings_news_metrics`: symbol, earnings_date, pre_news_count, pre_news_sentiment_avg, pre_news_pos_ratio, pre_news_neg_ratio, post_news_count, post_news_sentiment_avg, sentiment_shift.

## CLI Usage

### 1. Ingest Earnings & Price Impact
```bash
# Ingest mega-cap tier (61 tickers)
python3 -m earnings_model collect --tiers mega

# Ingest large-cap tier (415 tickers)
python3 -m earnings_model collect --tiers large

# Ingest full universe (559 optionable tickers)
python3 -m earnings_model collect --tiers mega,large,medium,small

# Ingest specific symbol
python3 -m earnings_model collect --symbol NVDA
```

### 2. Ingest Pre & Post Earnings News
```bash
# Ingest news for mega-cap stocks (up to 12 quarters back)
python3 -m earnings_model collect-news --tiers mega

# Ingest news for a single stock
python3 -m earnings_model collect-news --symbol AAPL --max-events 16
```

### 3. Train Machine Learning Models
```bash
python3 -m earnings_model train
```

### 4. Forecast Upcoming Move & Strategy Recommendation
```bash
python3 -m earnings_model predict --symbol NVDA
python3 -m earnings_model predict --symbol TSLA
```

### 5. Check Database Status
```bash
python3 -m earnings_model status
```

### 6. View Universe Summary or Symbol Report
```bash
python3 -m earnings_model summary
python3 -m earnings_model report --symbol MSFT
```

### 7. Export Dataset for Machine Learning
```bash
python3 -m earnings_model export --output earnings_model/data/earnings_dataset.csv
```

## Strategy Recommendation Engine

The predictive engine maps forecasted probabilities and expected move magnitudes to defined-risk options structures:
- **Bullish Bias & Low Reversal Risk** $\rightarrow$ `Debit Bull Call Spread (Next-Week Expiry)`
- **Bearish Bias & Low Reversal Risk** $\rightarrow$ `Debit Bear Put Spread (Next-Week Expiry)`
- **High Expected Move & Neutral Drift** $\rightarrow$ `Long Straddle / Strangle`
- **Moderate Expected Move & High Reversal Risk** $\rightarrow$ `Post-Earnings Gap-Fade / Reversal Setup`
- **Low Volatility & Neutral Drift** $\rightarrow$ `Iron Condor / Short Premium Outside Implied Range`
