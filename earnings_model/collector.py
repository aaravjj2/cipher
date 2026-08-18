"""Historical earnings and price impact data collection engine.

Ingests quarterly earnings reports (EPS, surprises, revenue, net income, timing)
and computes historical market price impact (gap %, multi-day returns, volume ratio)
for optionable equities in the Cipher universe.
"""
import yfinance as yf
import pandas as pd
import numpy as np
import time
import datetime
import logging
from typing import List, Dict, Any, Optional

from .config import MAX_EARNINGS_LOOKBACK, YFINANCE_DELAY, YFINANCE_BATCH_SIZE
from .universe import tier_for_ticker, load_universe
from .db import (
    init_db,
    upsert_earnings_event,
    upsert_price_impact,
    log_fetch,
    get_symbols_needing_fetch
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

KNOWN_ETFS = {
    'SPY', 'QQQ', 'IWM', 'DIA', 'IVV', 'VOO', 'VTI', 'VEA', 'VWO', 'IEFA',
    'XLF', 'XLE', 'XLK', 'XLV', 'XLI', 'XLP', 'XLY', 'XLU', 'XLRE', 'XLB', 'XLC',
    'XBI', 'ARKK', 'SMH', 'SOXX', 'GLD', 'SLV', 'TLT', 'HYG', 'LQD', 'EEM', 'EFA',
    'FXI', 'EWZ', 'GDX', 'GDXJ', 'KRE', 'XRT', 'UNG', 'USO', 'UVXY', 'VXX', 'SQQQ',
    'TQQQ', 'SOXL', 'SOXS', 'SPXU', 'UPRO', 'TNA', 'TZA', 'LABU', 'LABD', 'BIL', 'SHY',
    'IEF', 'BND', 'AGG', 'JNK', 'EMB', 'VT', 'SCHD', 'JEPI', 'JEPQ', 'RSP', 'IWF', 'IWD'
}


def is_etf(symbol: str) -> bool:
    """Determine if a symbol is an ETF/fund rather than an equity."""
    sym = symbol.upper()
    if sym in KNOWN_ETFS:
        return True
    try:
        t = yf.Ticker(sym)
        qtype = getattr(t.fast_info, 'quote_type', None)
        if qtype and qtype.upper() == 'ETF':
            return True
    except Exception:
        pass
    return False


def collect_earnings(symbol: str) -> List[Dict[str, Any]]:
    """Fetch quarterly earnings data and financials for a symbol."""
    if is_etf(symbol):
        logging.debug(f"{symbol} is an ETF/fund; skipping earnings collection.")
        return []

    ticker = yf.Ticker(symbol)
    events = []
    try:
        dates_df = ticker.get_earnings_dates(limit=MAX_EARNINGS_LOOKBACK)
        if dates_df is None or dates_df.empty:
            return events

        # Filter out rows without reported EPS
        dates_df = dates_df.dropna(subset=['Reported EPS'])
        if dates_df.empty:
            return events

        # Get quarterly income statement for revenue/net income matching
        try:
            income_stmt = ticker.quarterly_income_stmt
        except Exception:
            income_stmt = pd.DataFrame()

        cap_tier = tier_for_ticker(symbol)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        for dt_idx, row in dates_df.iterrows():
            earnings_date_iso = dt_idx.isoformat()
            hour = dt_idx.hour
            timing = 'BMO' if hour < 12 else 'AMC'

            eps_est = row.get('EPS Estimate', None)
            eps_act = row.get('Reported EPS', None)
            eps_surp = row.get('Surprise(%)', None)

            if pd.isna(eps_est): eps_est = None
            if pd.isna(eps_act): eps_act = None
            if pd.isna(eps_surp): eps_surp = None

            rev = None
            net_inc = None
            dil_eps = None
            fiscal_q = None

            # Match with closest quarterly income statement column
            if not income_stmt.empty:
                dt_naive = dt_idx.tz_localize(None) if hasattr(dt_idx, 'tz_localize') and dt_idx.tzinfo else dt_idx
                for col in income_stmt.columns:
                    col_naive = col.tz_localize(None) if hasattr(col, 'tz_localize') and col.tzinfo else col
                    diff = abs((dt_naive - col_naive).days)
                    if diff < 55:  # Within ~7 weeks of quarter-end
                        rev = income_stmt[col].get('Total Revenue', None)
                        net_inc = income_stmt[col].get('Net Income', None)
                        dil_eps = income_stmt[col].get('Diluted EPS', None)
                        fiscal_q = f"{col.year}-Q{(col.month - 1) // 3 + 1}"
                        if pd.isna(rev): rev = None
                        if pd.isna(net_inc): net_inc = None
                        if pd.isna(dil_eps): dil_eps = None
                        break

            events.append({
                'symbol': symbol,
                'earnings_date': earnings_date_iso,
                'fiscal_quarter': fiscal_q,
                'timing': timing,
                'eps_estimate': float(eps_est) if eps_est is not None else None,
                'eps_actual': float(eps_act) if eps_act is not None else None,
                'eps_surprise_pct': float(eps_surp) if eps_surp is not None else None,
                'revenue': float(rev) if rev is not None else None,
                'revenue_estimate': None,
                'net_income': float(net_inc) if net_inc is not None else None,
                'diluted_eps': float(dil_eps) if dil_eps is not None else None,
                'cap_tier': cap_tier,
                'fetched_at': now
            })
    except Exception as e:
        logging.error(f"Error collecting earnings for {symbol}: {e}")

    return events


def collect_price_impact(symbol: str, earnings_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute pre- and post-earnings price impact metrics from OHLCV bars."""
    if not earnings_events:
        return []

    ticker = yf.Ticker(symbol)
    impacts = []

    try:
        # Determine total date window needed
        event_dts = [pd.to_datetime(e['earnings_date'], utc=True) for e in earnings_events]
        start_date = (min(event_dts) - pd.Timedelta(days=70)).strftime('%Y-%m-%d')
        end_date = (max(event_dts) + pd.Timedelta(days=70)).strftime('%Y-%m-%d')

        hist = ticker.history(start=start_date, end=end_date)
        if hist.empty:
            return impacts

        # Normalize index to timezone-aware UTC dates
        if hist.index.tz is None:
            hist.index = hist.index.tz_localize('UTC')
        else:
            hist.index = hist.index.tz_convert('UTC')

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        for event in earnings_events:
            edate = pd.to_datetime(event['earnings_date'], utc=True)
            timing = event.get('timing', 'AMC')

            try:
                # Find the closest trading day to the report
                idx = hist.index.get_indexer([edate], method='nearest')[0]
                bar_date = hist.index[idx]

                # If AMC and report was late in day, post day is the next trading day
                if timing == 'AMC' and edate.hour >= 12:
                    post_idx = min(idx + 1, len(hist) - 1)
                    pre_idx = idx
                elif timing == 'BMO':
                    post_idx = idx
                    pre_idx = max(0, idx - 1)
                else:
                    post_idx = min(idx + 1, len(hist) - 1) if edate.hour >= 12 else idx
                    pre_idx = max(0, post_idx - 1)

                pre_close = hist['Close'].iloc[pre_idx]
                post_open = hist['Open'].iloc[post_idx]
                post_close = hist['Close'].iloc[post_idx]
                post_high = hist['High'].iloc[post_idx]
                post_low = hist['Low'].iloc[post_idx]
                post_vol = hist['Volume'].iloc[post_idx]

                # Pre-earnings returns
                pre_5d_idx = max(0, pre_idx - 5)
                pre_20d_idx = max(0, pre_idx - 20)

                pre_5d_ret = (pre_close - hist['Close'].iloc[pre_5d_idx]) / hist['Close'].iloc[pre_5d_idx] * 100 if pre_5d_idx < pre_idx else None
                pre_20d_ret = (pre_close - hist['Close'].iloc[pre_20d_idx]) / hist['Close'].iloc[pre_20d_idx] * 100 if pre_20d_idx < pre_idx else None

                # Earnings reaction metrics
                gap_pct = (post_open - pre_close) / pre_close * 100 if pre_close > 0 else None
                day1_ret = (post_close - pre_close) / pre_close * 100 if pre_close > 0 else None
                day1_rng = (post_high - post_low) / pre_close * 100 if pre_close > 0 else None

                # Forward multi-day impact
                day5_idx = min(len(hist) - 1, post_idx + 4)
                day10_idx = min(len(hist) - 1, post_idx + 9)
                day20_idx = min(len(hist) - 1, post_idx + 19)

                d5_c = hist['Close'].iloc[day5_idx] if day5_idx > post_idx else None
                d5_r = (d5_c - pre_close) / pre_close * 100 if (d5_c is not None and pre_close > 0) else None

                d10_c = hist['Close'].iloc[day10_idx] if day10_idx > post_idx else None
                d10_r = (d10_c - pre_close) / pre_close * 100 if (d10_c is not None and pre_close > 0) else None

                d20_c = hist['Close'].iloc[day20_idx] if day20_idx > post_idx else None
                d20_r = (d20_c - pre_close) / pre_close * 100 if (d20_c is not None and pre_close > 0) else None

                # 20-day baseline average volume
                avg_vol = hist['Volume'].iloc[max(0, pre_idx - 20):pre_idx].mean() if pre_idx > 0 else None
                vol_ratio = post_vol / avg_vol if (avg_vol is not None and avg_vol > 0) else None

                impacts.append({
                    'symbol': symbol,
                    'earnings_date': event['earnings_date'],
                    'pre_close': round(float(pre_close), 4),
                    'pre_5d_return_pct': round(float(pre_5d_ret), 4) if pre_5d_ret is not None else None,
                    'pre_20d_return_pct': round(float(pre_20d_ret), 4) if pre_20d_ret is not None else None,
                    'post_open': round(float(post_open), 4),
                    'post_close': round(float(post_close), 4),
                    'post_high': round(float(post_high), 4),
                    'post_low': round(float(post_low), 4),
                    'post_volume': float(post_vol),
                    'gap_pct': round(float(gap_pct), 4) if gap_pct is not None else None,
                    'day1_return_pct': round(float(day1_ret), 4) if day1_ret is not None else None,
                    'day1_range_pct': round(float(day1_rng), 4) if day1_rng is not None else None,
                    'day5_close': round(float(d5_c), 4) if d5_c is not None else None,
                    'day5_return_pct': round(float(d5_r), 4) if d5_r is not None else None,
                    'day10_close': round(float(d10_c), 4) if d10_c is not None else None,
                    'day10_return_pct': round(float(d10_r), 4) if d10_r is not None else None,
                    'day20_close': round(float(d20_c), 4) if d20_c is not None else None,
                    'day20_return_pct': round(float(d20_r), 4) if d20_r is not None else None,
                    'avg_volume_20d': round(float(avg_vol), 2) if avg_vol is not None else None,
                    'volume_ratio': round(float(vol_ratio), 4) if vol_ratio is not None else None,
                    'fetched_at': now
                })
            except Exception as e:
                logging.debug(f"Skipping impact calculation for {symbol} on {event['earnings_date']}: {e}")

    except Exception as e:
        logging.error(f"Error computing price impact for {symbol}: {e}")

    return impacts


def run_collection(
    tiers: Optional[List[str]] = None,
    symbols: Optional[List[str]] = None,
    skip_existing: bool = True
) -> Dict[str, Any]:
    """Run data ingestion across specified tiers or symbols."""
    conn = init_db()

    if symbols is None:
        symbols = load_universe(tiers)

    # Filter out ETFs from the target list upfront
    equities = [s for s in symbols if not is_etf(s)]
    skipped_etfs = len(symbols) - len(equities)

    if skip_existing:
        target_symbols = get_symbols_needing_fetch(conn, equities, fetch_type='earnings')
    else:
        target_symbols = equities

    summary = {
        'success': 0,
        'error': 0,
        'skipped_existing': len(equities) - len(target_symbols),
        'skipped_etfs': skipped_etfs,
        'total': len(target_symbols)
    }

    logging.info(
        f"Starting collection for {len(target_symbols)} symbols "
        f"({skipped_etfs} ETFs skipped, {summary['skipped_existing']} already collected)..."
    )

    for i, symbol in enumerate(target_symbols):
        try:
            logging.info(f"[{i + 1}/{len(target_symbols)}] Fetching {symbol}...")

            events = collect_earnings(symbol)
            if not events:
                log_fetch(conn, symbol, 'earnings', 'error', 0, 'No earnings events found')
                summary['error'] += 1
                continue

            for ev in events:
                upsert_earnings_event(conn, ev)
            log_fetch(conn, symbol, 'earnings', 'success', len(events))

            impacts = collect_price_impact(symbol, events)
            for imp in impacts:
                upsert_price_impact(conn, imp)
            log_fetch(conn, symbol, 'price_impact', 'success', len(impacts))

            summary['success'] += 1

            # Rate limiting
            time.sleep(YFINANCE_DELAY)
            if (i + 1) % YFINANCE_BATCH_SIZE == 0:
                logging.info(f"Batch checkpoint reached. Pausing 3s...")
                time.sleep(3)

        except Exception as e:
            logging.error(f"Failed processing {symbol}: {e}")
            log_fetch(conn, symbol, 'earnings', 'error', 0, str(e))
            summary['error'] += 1

    logging.info(f"Collection finished: {summary}")
    return summary
