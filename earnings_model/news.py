"""News collection and sentiment aggregation engine for historical earnings.

Queries Alpaca's Market Data News API around historical earnings dates,
analyzes headline and summary sentiment using the financial sentiment analyzer,
and aggregates pre- and post-earnings narrative metrics.
"""
import os
import time
import requests
import datetime
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime as dt, timedelta, timezone

from .config import DB_PATH
from .sentiment import score_text
from .db import (
    init_db,
    upsert_earnings_news,
    upsert_news_metrics,
    log_fetch,
    get_earnings_for_symbol,
    get_symbols_needing_fetch
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _get_alpaca_credentials() -> tuple[Optional[str], Optional[str]]:
    """Load Alpaca credentials from cipher-system .env or OS environment."""
    env_paths = [
        '/home/aarav/Aarav/cipher/cipher-system/app/.env',
        '/home/aarav/Aarav/cipher/runtime/config/cipher.env',
        '/home/aarav/Aarav/cipher/.env'
    ]
    env = {}
    for path in env_paths:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if '=' in line and not line.startswith('#'):
                        k, v = line.strip().split('=', 1)
                        env[k] = v.strip().strip('"').strip("'")
            break

    key = (
        env.get('ALPACA_ALGO_PLUS_KEY') or
        env.get('ALPACA_ALGO_KEY') or
        env.get('ALPACA_API_KEY') or
        os.environ.get('ALPACA_API_KEY')
    )
    secret = (
        env.get('ALPACA_ALGO_PLUS_SECRET') or
        env.get('ALPACA_ALGO_SECRET') or
        env.get('ALPACA_API_SECRET') or
        os.environ.get('ALPACA_API_SECRET')
    )
    return key, secret


def fetch_news_window(
    symbol: str,
    start_iso: str,
    end_iso: str,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Fetch raw news articles from Alpaca within a specific time window."""
    key, secret = _get_alpaca_credentials()
    if not key or not secret:
        logging.warning("Alpaca credentials not found; skipping news fetch.")
        return []

    headers = {
        'APCA-API-KEY-ID': key,
        'APCA-API-SECRET-KEY': secret,
        'Accept': 'application/json'
    }
    url = 'https://data.alpaca.markets/v1beta1/news'
    params = {
        'symbols': symbol.upper(),
        'start': start_iso,
        'end': end_iso,
        'limit': limit,
        'sort': 'asc',
        'include_content': False
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=12)
        if res.status_code == 200:
            return res.json().get('news', [])
        elif res.status_code == 429:
            logging.warning("Alpaca rate limit hit. Backing off 5 seconds...")
            time.sleep(5)
            res = requests.get(url, headers=headers, params=params, timeout=15)
            if res.status_code == 200:
                return res.json().get('news', [])
        logging.debug(f"Alpaca news error {res.status_code} for {symbol}: {res.text[:100]}")
    except Exception as e:
        logging.error(f"Failed to fetch news for {symbol}: {e}")

    return []


def process_event_news(
    symbol: str,
    earnings_event: Dict[str, Any],
    pre_days: int = 4,
    post_days: int = 4
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Fetch and score news for a single earnings event.

    Categorizes articles into 'pre' (before the announcement) and
    'post' (after the announcement).
    """
    raw_date = earnings_event['earnings_date']
    # Parse earnings timestamp
    try:
        # ISO string with or without timezone
        if 'T' in raw_date:
            event_dt = dt.fromisoformat(raw_date.replace('Z', '+00:00'))
        else:
            event_dt = dt.strptime(raw_date[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except Exception:
        event_dt = dt.strptime(raw_date[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)

    # Calculate search boundary
    start_dt = event_dt - timedelta(days=pre_days)
    end_dt = event_dt + timedelta(days=post_days)

    start_iso = start_dt.strftime('%Y-%m-%dT00:00:00Z')
    end_iso = end_dt.strftime('%Y-%m-%dT23:59:59Z')

    raw_articles = fetch_news_window(symbol, start_iso, end_iso, limit=50)
    if not raw_articles:
        return [], None

    now_iso = dt.now(timezone.utc).isoformat()
    scored_articles = []

    pre_scores = []
    pre_pos = 0
    pre_neg = 0
    pre_unc = []

    post_scores = []
    post_pos = 0
    post_neg = 0
    post_unc = []

    for art in raw_articles:
        headline = art.get('headline', '')
        summary = art.get('summary', '')
        full_text = f"{headline}. {summary}" if summary else headline
        sent = score_text(full_text)

        created_str = art.get('created_at', '')
        try:
            art_dt = dt.fromisoformat(created_str.replace('Z', '+00:00'))
            is_pre = art_dt <= event_dt
        except Exception:
            is_pre = created_str[:10] <= raw_date[:10]

        timing_rel = 'pre' if is_pre else 'post'

        if is_pre:
            pre_scores.append(sent['sentiment_score'])
            if sent['sentiment_label'] == 'positive':
                pre_pos += 1
            elif sent['sentiment_label'] == 'negative':
                pre_neg += 1
            pre_unc.append(sent['uncertainty_ratio'])
        else:
            post_scores.append(sent['sentiment_score'])
            if sent['sentiment_label'] == 'positive':
                post_pos += 1
            elif sent['sentiment_label'] == 'negative':
                post_neg += 1
            post_unc.append(sent['uncertainty_ratio'])

        scored_articles.append({
            'symbol': symbol,
            'earnings_date': raw_date,
            'news_id': art.get('id'),
            'headline': headline,
            'summary': summary,
            'created_at': created_str,
            'timing_rel': timing_rel,
            'sentiment_score': sent['sentiment_score'],
            'sentiment_label': sent['sentiment_label'],
            'uncertainty_ratio': sent['uncertainty_ratio'],
            'source': art.get('source'),
            'url': art.get('url'),
            'fetched_at': now_iso
        })

    # Compute aggregate metrics
    pre_count = len(pre_scores)
    post_count = len(post_scores)

    pre_avg = sum(pre_scores) / pre_count if pre_count > 0 else 0.0
    post_avg = sum(post_scores) / post_count if post_count > 0 else 0.0

    metrics = {
        'symbol': symbol,
        'earnings_date': raw_date,
        'pre_news_count': pre_count,
        'pre_news_sentiment_avg': round(pre_avg, 4),
        'pre_news_pos_ratio': round(pre_pos / pre_count, 4) if pre_count > 0 else 0.0,
        'pre_news_neg_ratio': round(pre_neg / pre_count, 4) if pre_count > 0 else 0.0,
        'pre_news_unc_ratio': round(sum(pre_unc) / pre_count, 4) if pre_count > 0 else 0.0,
        'post_news_count': post_count,
        'post_news_sentiment_avg': round(post_avg, 4),
        'post_news_pos_ratio': round(post_pos / post_count, 4) if post_count > 0 else 0.0,
        'post_news_neg_ratio': round(post_neg / post_count, 4) if post_count > 0 else 0.0,
        'post_news_unc_ratio': round(sum(post_unc) / post_count, 4) if post_count > 0 else 0.0,
        'sentiment_shift': round(post_avg - pre_avg, 4),
        'fetched_at': now_iso
    }

    return scored_articles, metrics


def collect_news_for_symbol(
    symbol: str,
    conn=None,
    max_events: int = 16
) -> int:
    """Fetch and score news for up to `max_events` recent earnings events of a symbol."""
    if conn is None:
        conn = init_db()

    events = get_earnings_for_symbol(conn, symbol)
    if not events:
        return 0

    # Limit to most recent N events where news is available
    target_events = events[:max_events]
    total_articles = 0

    for ev in target_events:
        articles, metrics = process_event_news(symbol, ev)
        for art in articles:
            try:
                upsert_earnings_news(conn, art)
                total_articles += 1
            except Exception:
                pass

        if metrics:
            upsert_news_metrics(conn, metrics)

        time.sleep(0.3)  # Gentle rate limiting for Alpaca

    return total_articles


def run_news_collection(
    symbols: Optional[List[str]] = None,
    tiers: Optional[List[str]] = None,
    max_events_per_symbol: int = 12
) -> Dict[str, Any]:
    """Run batch news collection across target symbols."""
    conn = init_db()
    from .universe import load_universe

    if symbols is None:
        symbols = load_universe(tiers)

    summary = {'success': 0, 'error': 0, 'total_articles': 0, 'symbols_count': len(symbols)}
    logging.info(f"Starting news collection for {len(symbols)} symbols...")

    for i, sym in enumerate(symbols):
        try:
            logging.info(f"[{i+1}/{len(symbols)}] Collecting news for {sym}...")
            count = collect_news_for_symbol(sym, conn=conn, max_events=max_events_per_symbol)
            log_fetch(conn, sym, 'news', 'success', count)
            summary['success'] += 1
            summary['total_articles'] += count
            time.sleep(0.5)
        except Exception as e:
            logging.error(f"Error collecting news for {sym}: {e}")
            log_fetch(conn, sym, 'news', 'error', 0, str(e))
            summary['error'] += 1

    logging.info(f"News collection complete: {summary}")
    return summary
