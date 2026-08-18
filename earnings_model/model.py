"""Machine Learning predictive modeling engine for earnings events.

Trains lookahead-free predictive models combining historical fundamentals,
momentum drift, and pre-earnings news sentiment to forecast:
  1. Directional move probability (Day 1 and Day 5)
  2. Expected move magnitude (Absolute Gap and Day 5 range)
  3. Gap reversal / mean-reversion risk
"""
import os
import joblib
import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, roc_auc_score, mean_absolute_error, classification_report
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from .config import DB_PATH, DATA_DIR
from .db import init_db


MODEL_ARTIFACT_PATH = os.path.join(DATA_DIR, 'earnings_models.joblib')


def build_feature_dataset(conn=None, symbol=None) -> pd.DataFrame:
    """Build a point-in-time, lookahead-free dataset for ML modeling.

    For each earnings event, calculates rolling features strictly using
    data available PRIOR to that report.
    """
    if conn is None:
        conn = init_db()

    params = []
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
            e.net_income,
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
            p.volume_ratio,
            nm.pre_news_count,
            nm.pre_news_sentiment_avg,
            nm.pre_news_pos_ratio,
            nm.pre_news_neg_ratio,
            nm.pre_news_unc_ratio,
            nm.post_news_sentiment_avg,
            nm.sentiment_shift
        FROM earnings_events e
        INNER JOIN price_impact p
            ON e.symbol = p.symbol AND e.earnings_date = p.earnings_date
        LEFT JOIN earnings_news_metrics nm
            ON e.symbol = nm.symbol AND e.earnings_date = nm.earnings_date
    """
    if symbol:
        query += " WHERE e.symbol = ?"
        params.append(symbol.upper())

    query += " ORDER BY e.symbol, e.earnings_date ASC"

    df = pd.read_sql_query(query, conn, params=params or None)
    if df.empty:
        return pd.DataFrame()

    # Targets
    df['target_gap_up'] = (df['gap_pct'] > 0).astype(int)
    df['target_day1_up'] = (df['day1_return_pct'] > 0).astype(int)
    df['target_day5_up'] = (df['day5_return_pct'] > 0).astype(int)
    df['target_abs_gap'] = df['gap_pct'].abs()
    df['target_abs_day5'] = df['day5_return_pct'].abs()
    df['target_reversal'] = (
        (np.sign(df['gap_pct']) != np.sign(df['day5_return_pct'])) &
        (df['target_abs_gap'] >= 0.5)
    ).astype(int)

    # Point-in-time rolling features per symbol (strictly lookahead-free)
    df['is_beat_num'] = (df['eps_actual'] > df['eps_estimate']).astype(float)
    df['reversal_num'] = df['target_reversal'].astype(float)

    grouped = df.groupby('symbol', group_keys=False)

    # Vectorized prior calculations with 1-shift so current event is never included
    df['prior_beat_rate'] = grouped['is_beat_num'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.5)
    df['prior_avg_surprise'] = grouped['eps_surprise_pct'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.0)
    df['prior_avg_abs_gap'] = grouped['target_abs_gap'].apply(lambda x: x.shift(1).expanding().mean()).fillna(3.0)
    df['prior_avg_day5'] = grouped['day5_return_pct'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.0)
    df['prior_reversal_rate'] = grouped['reversal_num'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.4)

    # Streak calculation
    def calc_streaks(s):
        streaks = []
        cur = 0
        for val in s.tolist():
            streaks.append(cur)
            if val == 1.0:
                cur = cur + 1 if cur > 0 else 1
            elif val == 0.0:
                cur = cur - 1 if cur < 0 else -1
            else:
                cur = 0
        return pd.Series(streaks, index=s.index)

    df['prior_streak'] = grouped['is_beat_num'].apply(calc_streaks).fillna(0)

    feat_df = df.copy()

    # Clean / fill missing news metrics with neutral values
    feat_df['pre_news_count'] = feat_df['pre_news_count'].fillna(0)
    feat_df['pre_news_sentiment_avg'] = feat_df['pre_news_sentiment_avg'].fillna(0.0)
    feat_df['pre_news_pos_ratio'] = feat_df['pre_news_pos_ratio'].fillna(0.0)
    feat_df['pre_news_neg_ratio'] = feat_df['pre_news_neg_ratio'].fillna(0.0)
    feat_df['pre_news_unc_ratio'] = feat_df['pre_news_unc_ratio'].fillna(0.0)

    # Pre-earnings drift fills
    feat_df['pre_5d_return_pct'] = feat_df['pre_5d_return_pct'].fillna(0.0)
    feat_df['pre_20d_return_pct'] = feat_df['pre_20d_return_pct'].fillna(0.0)

    return feat_df


FEATURE_COLS = [
    'prior_beat_rate',
    'prior_avg_surprise',
    'prior_avg_abs_gap',
    'prior_avg_day5',
    'prior_reversal_rate',
    'prior_streak',
    'pre_5d_return_pct',
    'pre_20d_return_pct',
    'pre_news_count',
    'pre_news_sentiment_avg',
    'pre_news_pos_ratio',
    'pre_news_neg_ratio',
    'pre_news_unc_ratio'
]


def train_earnings_models(
    df: Optional[pd.DataFrame] = None,
    save_artifacts: bool = True
) -> Dict[str, Any]:
    """Train predictive models for direction, magnitude, and reversal.

    Uses chronological cross-validation / train-test split.
    """
    if df is None:
        df = build_feature_dataset()

    if df.empty or len(df) < 50:
        return {'error': 'Insufficient training data (need >= 50 events)'}

    # Filter complete cases for features
    valid = df.dropna(subset=FEATURE_COLS + ['target_day1_up', 'target_day5_up', 'target_abs_gap']).copy()
    valid = valid.sort_values('earnings_date').reset_index(drop=True)

    # Chronological 80/20 split
    split_idx = int(len(valid) * 0.8)
    train_df = valid.iloc[:split_idx]
    test_df = valid.iloc[split_idx:]

    X_train = train_df[FEATURE_COLS]
    X_test = test_df[FEATURE_COLS]

    results = {
        'total_samples': len(valid),
        'train_samples': len(train_df),
        'test_samples': len(test_df),
        'models': {}
    }

    trained_artifacts = {}

    # 1. Day-1 Direction Classifier
    clf_day1 = Pipeline([
        ('scaler', StandardScaler()),
        ('model', GradientBoostingClassifier(n_estimators=80, max_depth=3, random_state=42))
    ])
    clf_day1.fit(X_train, train_df['target_day1_up'])
    pred_d1 = clf_day1.predict(X_test)
    prob_d1 = clf_day1.predict_proba(X_test)[:, 1]
    acc_d1 = accuracy_score(test_df['target_day1_up'], pred_d1)
    auc_d1 = roc_auc_score(test_df['target_day1_up'], prob_d1) if len(np.unique(test_df['target_day1_up'])) > 1 else 0.5
    results['models']['day1_direction'] = {'accuracy': round(acc_d1, 4), 'roc_auc': round(auc_d1, 4)}
    trained_artifacts['day1_direction'] = clf_day1

    # 2. Day-5 Direction Classifier
    clf_day5 = Pipeline([
        ('scaler', StandardScaler()),
        ('model', GradientBoostingClassifier(n_estimators=80, max_depth=3, random_state=42))
    ])
    clf_day5.fit(X_train, train_df['target_day5_up'])
    pred_d5 = clf_day5.predict(X_test)
    prob_d5 = clf_day5.predict_proba(X_test)[:, 1]
    acc_d5 = accuracy_score(test_df['target_day5_up'], pred_d5)
    auc_d5 = roc_auc_score(test_df['target_day5_up'], prob_d5) if len(np.unique(test_df['target_day5_up'])) > 1 else 0.5
    results['models']['day5_direction'] = {'accuracy': round(acc_d5, 4), 'roc_auc': round(auc_d5, 4)}
    trained_artifacts['day5_direction'] = clf_day5

    # 3. Gap Reversal Classifier
    clf_rev = Pipeline([
        ('scaler', StandardScaler()),
        ('model', RandomForestClassifier(n_estimators=100, max_depth=4, random_state=42))
    ])
    clf_rev.fit(X_train, train_df['target_reversal'])
    pred_rev = clf_rev.predict(X_test)
    prob_rev = clf_rev.predict_proba(X_test)[:, 1]
    acc_rev = accuracy_score(test_df['target_reversal'], pred_rev)
    results['models']['gap_reversal'] = {'accuracy': round(acc_rev, 4)}
    trained_artifacts['gap_reversal'] = clf_rev

    # 4. Expected Move Regressor (Absolute Gap %)
    reg_gap = Pipeline([
        ('scaler', StandardScaler()),
        ('model', GradientBoostingRegressor(n_estimators=80, max_depth=3, random_state=42))
    ])
    reg_gap.fit(X_train, train_df['target_abs_gap'])
    pred_gap = reg_gap.predict(X_test)
    mae_gap = mean_absolute_error(test_df['target_abs_gap'], pred_gap)
    results['models']['expected_abs_gap'] = {'mae_pct': round(mae_gap, 4)}
    trained_artifacts['expected_abs_gap'] = reg_gap

    # Feature Importance Summary from Day-5 Gradient Booster
    gb_model = clf_day5.named_steps['model']
    importances = dict(zip(FEATURE_COLS, [round(float(v), 4) for v in gb_model.feature_importances_]))
    results['feature_importances'] = dict(sorted(importances.items(), key=lambda x: x[1], reverse=True))

    if save_artifacts:
        os.makedirs(DATA_DIR, exist_ok=True)
        joblib.dump({
            'artifacts': trained_artifacts,
            'feature_cols': FEATURE_COLS,
            'results': results
        }, MODEL_ARTIFACT_PATH)

    return results


def load_trained_models() -> Optional[Dict[str, Any]]:
    """Load cached model artifacts from disk."""
    if os.path.exists(MODEL_ARTIFACT_PATH):
        try:
            return joblib.load(MODEL_ARTIFACT_PATH)
        except Exception:
            return None
    return None


def predict_for_symbol(symbol: str, conn=None) -> Dict[str, Any]:
    """Generate predictive signals and option strategy recommendation for a symbol."""
    if conn is None:
        conn = init_db()

    models_data = load_trained_models()
    if not models_data:
        # Train on the fly if needed
        train_earnings_models()
        models_data = load_trained_models()

    if not models_data:
        return {'error': 'Models could not be loaded or trained'}

    artifacts = models_data['artifacts']
    cols = models_data['feature_cols']

    # Get symbol historical data
    sym_df = build_feature_dataset(conn, symbol=symbol)
    if sym_df.empty:
        return {'error': f'No historical data found for {symbol}'}

    # Latest record as input features
    latest = sym_df.iloc[-1]
    input_features = pd.DataFrame([latest[cols]])

    # Generate predictions
    prob_day1_up = float(artifacts['day1_direction'].predict_proba(input_features)[0, 1])
    prob_day5_up = float(artifacts['day5_direction'].predict_proba(input_features)[0, 1])
    prob_reversal = float(artifacts['gap_reversal'].predict_proba(input_features)[0, 1])
    pred_abs_gap = float(artifacts['expected_abs_gap'].predict(input_features)[0])

    # Direction classification
    if prob_day5_up >= 0.58:
        direction = 'BULLISH'
        confidence = prob_day5_up
    elif prob_day5_up <= 0.42:
        direction = 'BEARISH'
        confidence = 1.0 - prob_day5_up
    else:
        direction = 'NEUTRAL / MIXED'
        confidence = 0.50

    # Strategy Selection Matrix
    if direction == 'BULLISH' and prob_reversal < 0.45:
        primary_strategy = 'Debit Bull Call Spread (Next-Week Expiry)'
        rationale = f"Strong bullish continuation probability ({prob_day5_up:.1%}) with low gap-reversal risk ({prob_reversal:.1%})."
    elif direction == 'BEARISH' and prob_reversal < 0.45:
        primary_strategy = 'Debit Bear Put Spread (Next-Week Expiry)'
        rationale = f"Strong bearish continuation probability ({(1-prob_day5_up):.1%}) with low gap-reversal risk ({prob_reversal:.1%})."
    elif prob_reversal >= 0.55:
        primary_strategy = 'Post-Earnings Gap-Fade / Reversal Play'
        rationale = f"High probability of post-earnings gap reversal ({prob_reversal:.1%}). Monitor 10:30 AM ET first-hour retracement."
    elif pred_abs_gap >= 5.0 and direction == 'NEUTRAL / MIXED':
        primary_strategy = 'Long Straddle / Strangle (Pre-Earnings Expiry Move)'
        rationale = f"Large expected gap ({pred_abs_gap:.1f}%) with ambiguous directionality."
    else:
        primary_strategy = 'Iron Condor / Short Premium Outside Implied Range'
        rationale = f"Moderate expected move ({pred_abs_gap:.1f}%) with neutral drift."

    return {
        'symbol': symbol.upper(),
        'direction': direction,
        'confidence': round(confidence, 4),
        'prob_day1_up': round(prob_day1_up, 4),
        'prob_day5_up': round(prob_day5_up, 4),
        'prob_reversal': round(prob_reversal, 4),
        'expected_gap_pct': round(pred_abs_gap, 2),
        'primary_strategy': primary_strategy,
        'rationale': rationale,
        'inputs_snapshot': {
            'prior_beat_rate': round(float(latest['prior_beat_rate']), 3),
            'prior_streak': int(latest['prior_streak']),
            'pre_5d_drift_pct': round(float(latest['pre_5d_return_pct']), 2),
            'pre_news_sentiment': round(float(latest['pre_news_sentiment_avg']), 3),
            'pre_news_count': int(latest['pre_news_count'])
        }
    }
