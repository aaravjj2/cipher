"""Earnings pattern analysis engine.

Reads from the SQLite database (earnings_events + price_impact tables)
and computes analytical metrics for individual stocks and the full universe.

All table/column references match the schema defined in db.py:
  - earnings_events: symbol, earnings_date, fiscal_quarter, timing,
    eps_estimate, eps_actual, eps_surprise_pct, revenue, revenue_estimate,
    net_income, diluted_eps, cap_tier, fetched_at
  - price_impact: symbol, earnings_date, pre_close, pre_5d_return_pct,
    pre_20d_return_pct, post_open, post_close, post_high, post_low,
    post_volume, gap_pct, day1_return_pct, day1_range_pct, day5_close,
    day5_return_pct, day10_close, day10_return_pct, day20_close,
    day20_return_pct, avg_volume_20d, volume_ratio, fetched_at
"""
import sqlite3
import numpy as np
import pandas as pd
from . import db


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_connection(conn):
    """Return (connection, should_close) tuple."""
    if conn is not None:
        return conn, False
    import os
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'earnings.sqlite')
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c, True


def _get_merged_df(symbol=None, conn=None):
    """Fetch earnings_events LEFT JOIN price_impact into a single DataFrame.

    Joining on (symbol, earnings_date) gives us EPS + price-impact in one
    frame.  Columns are renamed to short aliases for convenience inside
    analysis functions but the underlying SQL always references the real
    schema columns.
    """
    c, close_conn = _get_connection(conn)
    try:
        query = """
            SELECT
                e.symbol,
                e.earnings_date,
                e.fiscal_quarter,
                e.timing,
                e.eps_estimate,
                e.eps_actual,
                e.eps_surprise_pct,
                e.revenue,
                e.revenue_estimate,
                e.net_income,
                e.diluted_eps,
                e.cap_tier,
                p.pre_close,
                p.pre_5d_return_pct,
                p.pre_20d_return_pct,
                p.post_open,
                p.post_close,
                p.gap_pct,
                p.day1_return_pct,
                p.day1_range_pct,
                p.day5_return_pct,
                p.day10_return_pct,
                p.day20_return_pct,
                p.post_volume,
                p.avg_volume_20d,
                p.volume_ratio
            FROM earnings_events e
            LEFT JOIN price_impact p
              ON e.symbol = p.symbol AND e.earnings_date = p.earnings_date
        """
        params = []
        if symbol:
            query += " WHERE e.symbol = ?"
            params.append(symbol)
        query += " ORDER BY e.earnings_date ASC"

        df = pd.read_sql_query(query, c, params=params or None)
        return df
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        # DB or tables might not exist yet
        return pd.DataFrame()
    finally:
        if close_conn:
            c.close()


# ---------------------------------------------------------------------------
# Public analysis functions
# ---------------------------------------------------------------------------

def earnings_profile(symbol, conn=None):
    """Build a complete earnings profile for a ticker.

    Returns dict with beat_rate, miss_rate, surprise stats, streak,
    revenue growth trends, gap/return averages, and volume ratio.
    """
    df = _get_merged_df(symbol, conn)
    if df.empty:
        return {}

    df = df.dropna(subset=['eps_actual', 'eps_estimate'])
    if df.empty:
        return {}

    df['is_beat'] = df['eps_actual'] > df['eps_estimate']
    df['is_miss'] = df['eps_actual'] < df['eps_estimate']

    total_events = len(df)
    beats = int(df['is_beat'].sum())
    misses = int(df['is_miss'].sum())

    beat_rate = beats / total_events if total_events > 0 else 0
    miss_rate = misses / total_events if total_events > 0 else 0

    avg_surprise = df['eps_surprise_pct'].mean()
    median_surprise = df['eps_surprise_pct'].median()

    # Calculate current streak (from most recent backward)
    streak = 0
    if total_events > 0:
        recent_first = df.iloc[::-1]
        first_beat = recent_first.iloc[0]['is_beat']
        first_miss = recent_first.iloc[0]['is_miss']

        for _, row in recent_first.iterrows():
            if first_beat and row['is_beat']:
                streak += 1
            elif first_miss and row['is_miss']:
                streak -= 1
            else:
                break

    # Revenue growth trends (QoQ and YoY)
    rev_series = df['revenue'].dropna()
    rev_qoq_trend = rev_series.pct_change().dropna().tolist() if len(rev_series) > 1 else []
    rev_yoy_trend = rev_series.pct_change(periods=4).dropna().tolist() if len(rev_series) > 4 else []

    # Average gap and returns
    avg_gap = df['gap_pct'].mean()
    avg_day1 = df['day1_return_pct'].mean()
    avg_day5 = df['day5_return_pct'].mean()

    # Gap direction accuracy (% times gap direction matches day5 direction)
    valid_gaps = df.dropna(subset=['gap_pct', 'day5_return_pct'])
    if len(valid_gaps) > 0:
        gap_matches = (
            ((valid_gaps['gap_pct'] > 0) & (valid_gaps['day5_return_pct'] > 0)) |
            ((valid_gaps['gap_pct'] < 0) & (valid_gaps['day5_return_pct'] < 0))
        )
        gap_dir_accuracy = float(gap_matches.mean())
    else:
        gap_dir_accuracy = None

    avg_vol_ratio = df['volume_ratio'].mean()

    return {
        'total_events': total_events,
        'beat_rate': float(beat_rate),
        'miss_rate': float(miss_rate),
        'avg_surprise_pct': float(avg_surprise) if pd.notnull(avg_surprise) else None,
        'median_surprise_pct': float(median_surprise) if pd.notnull(median_surprise) else None,
        'streak': streak,
        'rev_qoq_trend': rev_qoq_trend,
        'rev_yoy_trend': rev_yoy_trend,
        'avg_gap_pct': float(avg_gap) if pd.notnull(avg_gap) else None,
        'avg_day1_return_pct': float(avg_day1) if pd.notnull(avg_day1) else None,
        'avg_day5_return_pct': float(avg_day5) if pd.notnull(avg_day5) else None,
        'gap_dir_accuracy': gap_dir_accuracy,
        'avg_volume_ratio': float(avg_vol_ratio) if pd.notnull(avg_vol_ratio) else None,
    }


def sector_patterns(conn=None):
    """Group stats by cap_tier.

    Returns dict keyed by tier with average surprise %, beat rate,
    average gap, day1/day5 returns, and event count.
    """
    df = _get_merged_df(None, conn)
    if df.empty or 'cap_tier' not in df.columns:
        return {}

    df['is_beat'] = df['eps_actual'] > df['eps_estimate']

    grouped = df.groupby('cap_tier').agg(
        avg_surprise_pct=('eps_surprise_pct', 'mean'),
        beat_rate=('is_beat', 'mean'),
        avg_gap_pct=('gap_pct', 'mean'),
        avg_day1_return_pct=('day1_return_pct', 'mean'),
        avg_day5_return_pct=('day5_return_pct', 'mean'),
        count=('symbol', 'count'),
    ).reset_index()

    return grouped.set_index('cap_tier').to_dict(orient='index')


def surprise_impact_correlation(conn=None):
    """Analyze the relationship between EPS surprise magnitude and price reaction.

    Bins surprises into big_miss / miss / inline / beat / big_beat and
    computes per-bin average gap and returns, plus the overall Pearson
    correlation between surprise % and day-1 return.

    Note: eps_surprise_pct from yfinance is already in percentage form
    (e.g. 6.74 means +6.74%).  Bin edges are in the same units.
    """
    df = _get_merged_df(None, conn)
    if df.empty:
        return {}

    df = df.dropna(subset=['eps_surprise_pct'])
    if df.empty:
        return {}

    # Bins in percentage-point units matching yfinance Surprise(%) column
    bins = [-np.inf, -5, 0, 2, 5, np.inf]
    labels = ['big_miss', 'miss', 'inline', 'beat', 'big_beat']
    df['surprise_bin'] = pd.cut(df['eps_surprise_pct'], bins=bins, labels=labels)

    grouped = df.groupby('surprise_bin', observed=False).agg(
        avg_gap_pct=('gap_pct', 'mean'),
        avg_day1_return_pct=('day1_return_pct', 'mean'),
        avg_day5_return_pct=('day5_return_pct', 'mean'),
        count=('symbol', 'count'),
    ).reset_index()

    corr_df = df.dropna(subset=['eps_surprise_pct', 'day1_return_pct'])
    if len(corr_df) > 1:
        correlation = float(corr_df['eps_surprise_pct'].corr(corr_df['day1_return_pct']))
    else:
        correlation = None

    return {
        'bins': grouped.set_index('surprise_bin').to_dict(orient='index'),
        'correlation': correlation,
    }


def historical_pattern(symbol, conn=None):
    """Detailed historical pattern for a single stock.

    Returns list of events (with running beat/miss streak), gap-vs-day5
    comparison, seasonality by fiscal quarter, and pre-earnings drift stats.
    """
    df = _get_merged_df(symbol, conn)
    if df.empty:
        return {}

    df['date_str'] = pd.to_datetime(df['earnings_date'], utc=True).dt.strftime('%Y-%m-%d')
    df['is_beat'] = df['eps_actual'] > df['eps_estimate']
    df['is_miss'] = df['eps_actual'] < df['eps_estimate']

    # Running streak
    running_streak = []
    current = 0
    for _, row in df.iterrows():
        if row['is_beat']:
            current = current + 1 if current > 0 else 1
        elif row['is_miss']:
            current = current - 1 if current < 0 else -1
        else:
            current = 0
        running_streak.append(current)
    df['running_streak'] = running_streak

    # Gap held vs reversed
    df['gap_reversed'] = (
        (np.sign(df['gap_pct']) != np.sign(df['day5_return_pct'])) &
        (df['gap_pct'] != 0) &
        df['gap_pct'].notnull() &
        df['day5_return_pct'].notnull()
    )

    event_cols = [
        'date_str', 'eps_actual', 'eps_estimate', 'eps_surprise_pct',
        'gap_pct', 'day1_return_pct', 'day5_return_pct', 'gap_reversed',
        'running_streak',
    ]
    events = df[[c for c in event_cols if c in df.columns]].to_dict(orient='records')

    # Seasonality by fiscal quarter
    df['date_dt'] = pd.to_datetime(df['earnings_date'], utc=True)
    df['fiscal_q'] = 'Q' + df['date_dt'].dt.quarter.astype(str)
    seasonality = df.groupby('fiscal_q')['day5_return_pct'].mean().to_dict()
    seasonality = {k: float(v) if pd.notnull(v) else None for k, v in seasonality.items()}

    # Pre-earnings drift
    pre_drift = {
        'avg_pre_5d_pct': float(df['pre_5d_return_pct'].mean()) if df['pre_5d_return_pct'].notnull().any() else None,
        'avg_pre_20d_pct': float(df['pre_20d_return_pct'].mean()) if df['pre_20d_return_pct'].notnull().any() else None,
    }

    return {
        'events': events,
        'seasonality': seasonality,
        'pre_drift': pre_drift,
    }


def predictive_features(symbol, conn=None):
    """Extract features that could be predictive of post-earnings moves.

    Returns dict with historical beat_rate, avg_surprise, pre-momentum,
    avg absolute gap, gap reversal rate, volume ratio, and revenue growth
    acceleration.
    """
    df = _get_merged_df(symbol, conn)
    if df.empty:
        return {}

    df['is_beat'] = df['eps_actual'] > df['eps_estimate']
    beat_rate = df['is_beat'].mean()
    avg_surprise = df['eps_surprise_pct'].mean()

    # Most recent pre-5d momentum
    pre5 = df['pre_5d_return_pct'].dropna()
    pre_5d_momentum = float(pre5.iloc[-1]) if len(pre5) > 0 else None

    # Average absolute gap (expected move magnitude)
    df['abs_gap'] = df['gap_pct'].abs()
    avg_abs_gap = df['abs_gap'].mean()

    # Gap reversal rate
    valid = df.dropna(subset=['gap_pct', 'day5_return_pct'])
    if len(valid) > 0:
        reversals = (np.sign(valid['gap_pct']) != np.sign(valid['day5_return_pct'])) & (valid['gap_pct'] != 0)
        gap_reversal_rate = float(reversals.mean())
    else:
        gap_reversal_rate = None

    # Volume ratio on most recent earnings
    vr = df['volume_ratio'].dropna()
    vol_ratio_last = float(vr.iloc[-1]) if len(vr) > 0 else None

    # Revenue growth acceleration
    rev_qoq = df['revenue'].pct_change()
    rev_valid = rev_qoq.dropna()
    if len(rev_valid) >= 2:
        rev_growth_accel = float(rev_valid.iloc[-1] - rev_valid.iloc[-2])
    else:
        rev_growth_accel = None

    return {
        'beat_rate': float(beat_rate) if pd.notnull(beat_rate) else None,
        'avg_surprise_pct': float(avg_surprise) if pd.notnull(avg_surprise) else None,
        'pre_5d_momentum': pre_5d_momentum,
        'avg_abs_gap_pct': float(avg_abs_gap) if pd.notnull(avg_abs_gap) else None,
        'gap_reversal_rate': gap_reversal_rate,
        'vol_ratio_last': vol_ratio_last,
        'rev_growth_accel': rev_growth_accel,
    }


def upcoming_earnings_score(symbol, conn=None):
    """Generate a composite score for upcoming earnings.

    Components:
      - beat_probability: historical beat rate weighted toward recent quarters
      - expected_move: average of last 4 absolute gaps
      - direction_confidence: blend of pre-drift correlation and streak
      - risk_score: based on gap reversal rate + worst historical adverse move
      - composite_score: 0-100 overall score (higher = more bullish / predictable)
    """
    features = predictive_features(symbol, conn)
    profile = earnings_profile(symbol, conn)
    df = _get_merged_df(symbol, conn)

    if not features or df.empty:
        return {}

    # Beat probability (weighted towards recent)
    df['is_beat'] = df['eps_actual'] > df['eps_estimate']
    weights = np.linspace(0.5, 1.5, len(df))
    if len(df) > 0:
        beat_prob = float(np.average(df['is_beat'].astype(float), weights=weights))
    else:
        beat_prob = 0.5

    # Expected move magnitude (avg of last 4 abs gaps)
    last_4 = df.tail(4).copy()
    last_4['abs_gap'] = last_4['gap_pct'].abs()
    expected_move = float(last_4['abs_gap'].mean()) if last_4['abs_gap'].notnull().any() else None

    # Direction confidence from pre-drift correlation
    corr_df = df.dropna(subset=['pre_5d_return_pct', 'day5_return_pct'])
    if len(corr_df) > 1:
        drift_corr = corr_df['pre_5d_return_pct'].corr(corr_df['day5_return_pct'])
    else:
        drift_corr = 0

    streak = profile.get('streak', 0)
    dir_confidence = min(1.0, max(0.0, 0.5 + (drift_corr * 0.25) + (streak * 0.05)))

    # Risk score (gap reversal rate + worst historical move)
    gap_rev_rate = features.get('gap_reversal_rate') or 0.5
    min_day5 = df['day5_return_pct'].min()
    min_val = abs(float(min_day5)) if pd.notnull(min_day5) else 0
    risk_score = min(100.0, max(0.0, (gap_rev_rate * 50) + (min_val * 2)))

    # Composite 0-100 (higher = more bullish and predictable)
    composite = (beat_prob * 40) + (dir_confidence * 40) + ((100 - risk_score) * 0.2)
    composite = min(100.0, max(0.0, composite))

    return {
        'beat_probability': beat_prob,
        'expected_move_pct': expected_move,
        'direction_confidence': float(dir_confidence),
        'risk_score': float(risk_score),
        'composite_score': float(composite),
    }
