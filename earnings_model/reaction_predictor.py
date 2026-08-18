"""Advanced Hierarchical Stock & Earnings Reaction Prediction Engine.

Implements a 2-stage quantitative reaction model:
  Stage 1 (Fundamental Forecast):
    - Predicts EPS Beat Probability P(Beat) and Expected Surprise %
  Stage 2 (Market Reaction Forecast):
    - Predicts Opening Gap Direction & Magnitude
    - Predicts Day-1 Gap-Hold vs Gap-Fade Intraday Direction
    - Predicts Day-5 Post-Earnings Announcement Drift (PEAD) Continuation
    - Computes Expectation Tension (Priced-In vs Surprise divergence)
"""
import os
import joblib
import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor, RandomForestClassifier
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import accuracy_score, roc_auc_score, mean_absolute_error, r2_score

from .config import DB_PATH, DATA_DIR
from .db import init_db

REACTION_ARTIFACT_PATH = os.path.join(DATA_DIR, 'reaction_engine_models.joblib')


def build_reaction_dataset(conn=None, symbol=None) -> pd.DataFrame:
    """Build a comprehensive lookahead-free dataset for stock & earnings reaction modeling.

    Computes:
      - Point-in-time prior fundamental stats (beat rate, streak, surprise mean/volatility)
      - Priced-in Expectation Tension (run-up vs historical norm)
      - Ticker Reaction Archetypes (gap fade tendency, surprise sensitivity beta)
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
            e.diluted_eps,
            e.cap_tier,
            p.pre_close,
            p.pre_5d_return_pct,
            p.pre_20d_return_pct,
            p.post_open,
            p.post_close,
            p.post_high,
            p.post_low,
            p.post_volume,
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

    # Fundamental targets
    df['target_beat'] = (df['eps_actual'] > df['eps_estimate']).astype(int)
    df['target_surprise_pct'] = df['eps_surprise_pct'].fillna(0.0)

    # Market reaction targets
    df['target_gap_up'] = (df['gap_pct'] > 0).astype(int)
    df['target_gap_pct'] = df['gap_pct'].fillna(0.0)
    df['target_abs_gap'] = df['gap_pct'].abs().fillna(0.0)

    df['target_day1_up'] = (df['day1_return_pct'] > 0).astype(int)
    df['target_day1_pct'] = df['day1_return_pct'].fillna(0.0)

    df['target_day5_up'] = (df['day5_return_pct'] > 0).astype(int)
    df['target_day5_pct'] = df['day5_return_pct'].fillna(0.0)

    # Reaction dynamics: Gap Continuation vs Fade
    # Gap-Hold: Day 1 return has the same sign and is >= 50% of the opening gap
    df['target_gap_held'] = (
        (np.sign(df['gap_pct']) == np.sign(df['day1_return_pct'])) &
        (df['gap_pct'].abs() >= 0.5)
    ).astype(int)

    # PEAD Continuation: Day 5 return continues in the direction of the opening gap
    df['target_pead_continuation'] = (
        (np.sign(df['gap_pct']) == np.sign(df['day5_return_pct'])) &
        (df['gap_pct'].abs() >= 0.5)
    ).astype(int)

    # Gap Reversal by Day 5
    df['target_reversal'] = (
        (np.sign(df['gap_pct']) != np.sign(df['day5_return_pct'])) &
        (df['gap_pct'].abs() >= 0.5)
    ).astype(int)

    # Point-in-time expanding rolling features strictly using PRIOR events
    df['is_beat_num'] = (df['eps_actual'] > df['eps_estimate']).astype(float)
    df['gap_held_num'] = df['target_gap_held'].astype(float)
    df['reversal_num'] = df['target_reversal'].astype(float)

    grouped = df.groupby('symbol', group_keys=False)

    # 1. Rolling Fundamental Priors
    df['prior_beat_rate'] = grouped['is_beat_num'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.5)
    df['prior_avg_surprise'] = grouped['eps_surprise_pct'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.0)
    df['prior_surprise_std'] = grouped['eps_surprise_pct'].apply(lambda x: x.shift(1).expanding().std()).fillna(3.0)

    # 2. Rolling Market Reaction Archetype (Ticker Personality)
    df['prior_avg_abs_gap'] = grouped['target_abs_gap'].apply(lambda x: x.shift(1).expanding().mean()).fillna(3.0)
    df['prior_avg_day1'] = grouped['day1_return_pct'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.0)
    df['prior_avg_day5'] = grouped['day5_return_pct'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.0)
    df['prior_gap_hold_rate'] = grouped['gap_held_num'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.5)
    df['prior_reversal_rate'] = grouped['reversal_num'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.4)

    # 3. Rolling Pre-Drift Baselines
    df['prior_avg_pre_5d'] = grouped['pre_5d_return_pct'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.0)
    df['prior_avg_pre_20d'] = grouped['pre_20d_return_pct'].apply(lambda x: x.shift(1).expanding().mean()).fillna(0.0)

    # 4. Priced-in Expectation Tension (Run-up / Sell-off relative to normal)
    df['pre_5d_return_pct'] = df['pre_5d_return_pct'].fillna(0.0)
    df['pre_20d_return_pct'] = df['pre_20d_return_pct'].fillna(0.0)

    df['expectation_tension_5d'] = df['pre_5d_return_pct'] - df['prior_avg_pre_5d']
    df['expectation_tension_20d'] = df['pre_20d_return_pct'] - df['prior_avg_pre_20d']

    # 5. Beat Streak
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

    # Clean / neutral news metrics
    df['pre_news_count'] = df['pre_news_count'].fillna(0)
    df['pre_news_sentiment_avg'] = df['pre_news_sentiment_avg'].fillna(0.0)
    df['pre_news_pos_ratio'] = df['pre_news_pos_ratio'].fillna(0.0)
    df['pre_news_neg_ratio'] = df['pre_news_neg_ratio'].fillna(0.0)
    df['pre_news_unc_ratio'] = df['pre_news_unc_ratio'].fillna(0.0)

    return df


# Feature definitions for each stage
STAGE1_FEATURES = [
    'prior_beat_rate',
    'prior_streak',
    'prior_avg_surprise',
    'prior_surprise_std',
    'pre_5d_return_pct',
    'pre_20d_return_pct',
    'pre_news_count',
    'pre_news_sentiment_avg',
    'pre_news_pos_ratio',
    'pre_news_neg_ratio',
    'pre_news_unc_ratio'
]

STAGE2_FEATURES = [
    'pred_beat_prob',               # Injected from Stage 1
    'pred_surprise_pct',            # Injected from Stage 1
    'prior_avg_abs_gap',
    'prior_avg_day1',
    'prior_avg_day5',
    'prior_gap_hold_rate',
    'prior_reversal_rate',
    'pre_5d_return_pct',
    'pre_20d_return_pct',
    'expectation_tension_5d',
    'expectation_tension_20d',
    'pre_news_sentiment_avg',
    'pre_news_unc_ratio'
]


def train_reaction_pipeline(df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Train the full 2-stage hierarchical reaction models."""
    if df is None:
        df = build_reaction_dataset()

    if df.empty or len(df) < 200:
        return {'error': 'Insufficient training data'}

    df = df.sort_values('earnings_date').reset_index(drop=True)

    # 80/20 chronological split
    split_idx = int(len(df) * 0.8)
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()

    # -------------------------------------------------------------
    # STAGE 1: FUNDAMENTAL MODELS (Beat Probability & Surprise Mag)
    # -------------------------------------------------------------
    X1_train = train_df[STAGE1_FEATURES]
    X1_test = test_df[STAGE1_FEATURES]

    # Model 1A: Calibrated Beat Classifier
    base_clf_beat = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
    clf_beat = CalibratedClassifierCV(base_clf_beat, cv=3)
    clf_beat.fit(X1_train, train_df['target_beat'])

    beat_prob_train = clf_beat.predict_proba(X1_train)[:, 1]
    beat_prob_test = clf_beat.predict_proba(X1_test)[:, 1]

    # Model 1B: Surprise Regressor
    reg_surprise = Pipeline([
        ('scaler', StandardScaler()),
        ('model', Ridge(alpha=10.0))
    ])
    reg_surprise.fit(X1_train, train_df['target_surprise_pct'])

    pred_surp_train = reg_surprise.predict(X1_train)
    pred_surp_test = reg_surprise.predict(X1_test)

    # Inject Stage 1 predictions into Stage 2 feature matrices
    train_df['pred_beat_prob'] = beat_prob_train
    train_df['pred_surprise_pct'] = pred_surp_train

    test_df['pred_beat_prob'] = beat_prob_test
    test_df['pred_surprise_pct'] = pred_surp_test

    # -------------------------------------------------------------
    # STAGE 2: MARKET REACTION MODELS (Gap, Day 1, Day 5, Reversal)
    # -------------------------------------------------------------
    X2_train = train_df[STAGE2_FEATURES]
    X2_test = test_df[STAGE2_FEATURES]

    # Model 2A: Gap Direction Classifier (Calibrated)
    base_gap_dir = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
    clf_gap_dir = CalibratedClassifierCV(base_gap_dir, cv=3)
    clf_gap_dir.fit(X2_train, train_df['target_gap_up'])
    gap_prob_test = clf_gap_dir.predict_proba(X2_test)[:, 1]

    # Model 2B: Gap Magnitude Regressor (Expected Opening Gap %)
    reg_gap_pct = Pipeline([
        ('scaler', StandardScaler()),
        ('model', GradientBoostingRegressor(n_estimators=100, max_depth=3, random_state=42))
    ])
    reg_gap_pct.fit(X2_train, train_df['target_gap_pct'])
    pred_gap_pct_test = reg_gap_pct.predict(X2_test)

    # Model 2C: Day 5 Direction Classifier (Calibrated)
    base_d5_dir = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
    clf_d5_dir = CalibratedClassifierCV(base_d5_dir, cv=3)
    clf_d5_dir.fit(X2_train, train_df['target_day5_up'])
    d5_prob_test = clf_d5_dir.predict_proba(X2_test)[:, 1]

    # Model 2D: Gap Reversal Classifier (Calibrated)
    base_rev = RandomForestClassifier(n_estimators=120, max_depth=4, random_state=42)
    clf_reversal = CalibratedClassifierCV(base_rev, cv=3)
    clf_reversal.fit(X2_train, train_df['target_reversal'])
    rev_prob_test = clf_reversal.predict_proba(X2_test)[:, 1]

    # -------------------------------------------------------------
    # EVALUATION METRICS & HIGH-CONVICTION ANALYSIS
    # -------------------------------------------------------------
    # Beat accuracy
    beat_pred = (beat_prob_test >= 0.50).astype(int)
    beat_acc = accuracy_score(test_df['target_beat'], beat_pred)
    beat_auc = roc_auc_score(test_df['target_beat'], beat_prob_test)

    # Gap direction accuracy
    gap_pred = (gap_prob_test >= 0.50).astype(int)
    gap_acc = accuracy_score(test_df['target_gap_up'], gap_pred)
    gap_mae = mean_absolute_error(test_df['target_gap_pct'], pred_gap_pct_test)

    # Day 5 direction accuracy
    d5_pred = (d5_prob_test >= 0.50).astype(int)
    d5_acc = accuracy_score(test_df['target_day5_up'], d5_pred)

    # Reversal accuracy
    rev_pred = (rev_prob_test >= 0.45).astype(int)
    rev_acc = accuracy_score(test_df['target_reversal'], rev_pred)

    # High-Conviction Gating Evaluation
    # When model is >= 65% confident on Day 5 direction:
    high_conv_mask = (d5_prob_test >= 0.65) | (d5_prob_test <= 0.35)
    if high_conv_mask.sum() > 0:
        high_conv_pred = (d5_prob_test[high_conv_mask] >= 0.50).astype(int)
        high_conv_acc = accuracy_score(test_df['target_day5_up'].iloc[high_conv_mask], high_conv_pred)
        high_conv_n = int(high_conv_mask.sum())
    else:
        high_conv_acc = d5_acc
        high_conv_n = 0

    results = {
        'total_samples': len(df),
        'train_samples': len(train_df),
        'test_samples': len(test_df),
        'stage1_fundamentals': {
            'eps_beat_accuracy': round(beat_acc * 100, 2),
            'eps_beat_roc_auc': round(beat_auc, 3),
            'surprise_mae_pct': round(mean_absolute_error(test_df['target_surprise_pct'], pred_surp_test), 2)
        },
        'stage2_market_reaction': {
            'opening_gap_direction_accuracy': round(gap_acc * 100, 2),
            'expected_gap_mae_pct': round(gap_mae, 2),
            'day5_direction_accuracy': round(d5_acc * 100, 2),
            'gap_reversal_accuracy': round(rev_acc * 100, 2)
        },
        'high_conviction_gating': {
            'high_conviction_accuracy_pct': round(high_conv_acc * 100, 2),
            'high_conviction_trades_count': high_conv_n,
            'alpha_lift_pct': round((high_conv_acc - d5_acc) * 100, 2)
        }
    }

    # Save artifacts
    artifacts = {
        'clf_beat': clf_beat,
        'reg_surprise': reg_surprise,
        'clf_gap_dir': clf_gap_dir,
        'reg_gap_pct': reg_gap_pct,
        'clf_d5_dir': clf_d5_dir,
        'clf_reversal': clf_reversal,
        'stage1_cols': STAGE1_FEATURES,
        'stage2_cols': STAGE2_FEATURES,
        'results': results
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    joblib.dump(artifacts, REACTION_ARTIFACT_PATH)

    return results


def load_reaction_artifacts() -> Optional[Dict[str, Any]]:
    """Load serialized reaction engine models."""
    if os.path.exists(REACTION_ARTIFACT_PATH):
        try:
            return joblib.load(REACTION_ARTIFACT_PATH)
        except Exception:
            return None
    return None


def predict_stock_reaction(symbol: str, conn=None) -> Dict[str, Any]:
    """Run full 2-stage reaction prediction for a stock.

    Outputs structured forecast:
      1. Fundamental Forecast (Beat Probability & Expected Surprise)
      2. Market Reaction (Expected Opening Gap, Day-1 Intraday Bias, Day-5 PEAD Drift)
      3. Expectation Tension (Priced-In Run-Up vs Neutrality)
      4. Actionable Stock Catalyst Summary
    """
    if conn is None:
        conn = init_db()

    artifacts = load_reaction_artifacts()
    if not artifacts:
        train_reaction_pipeline()
        artifacts = load_reaction_artifacts()

    if not artifacts:
        return {'error': 'Could not load reaction prediction models'}

    # Get symbol features
    df = build_reaction_dataset(conn, symbol=symbol)
    if df.empty:
        return {'error': f'No historical reaction data found for {symbol}'}

    latest = df.iloc[-1]

    # Stage 1 Prediction
    X1 = pd.DataFrame([latest[artifacts['stage1_cols']]])
    pred_beat_prob = float(artifacts['clf_beat'].predict_proba(X1)[0, 1])
    pred_surprise_pct = float(artifacts['reg_surprise'].predict(X1)[0])

    # Stage 2 Prediction
    latest_s2 = latest.to_dict()
    latest_s2['pred_beat_prob'] = pred_beat_prob
    latest_s2['pred_surprise_pct'] = pred_surprise_pct

    X2 = pd.DataFrame([latest_s2])[artifacts['stage2_cols']]

    prob_gap_up = float(artifacts['clf_gap_dir'].predict_proba(X2)[0, 1])
    pred_gap_pct = float(artifacts['reg_gap_pct'].predict(X2)[0])
    prob_day5_up = float(artifacts['clf_d5_dir'].predict_proba(X2)[0, 1])
    prob_reversal = float(artifacts['clf_reversal'].predict_proba(X2)[0, 1])

    # Expectation tension analysis
    tension_5d = float(latest['expectation_tension_5d'])
    tension_20d = float(latest['expectation_tension_20d'])

    if tension_20d >= 8.0:
        expectation_state = 'OVERHEATED (Run-up priced in; vulnerable to sell-the-news on modest beat)'
    elif tension_20d <= -8.0:
        expectation_state = 'OVERSOLD (Pessimism priced in; primed for sharp relief rally on in-line/beat)'
    else:
        expectation_state = 'BALANCED (Trading in line with historical baseline)'

    # Qualitative synthesis
    if prob_day5_up >= 0.60:
        reaction_bias = 'BULLISH CONTINUATION (PEAD Uptrend)'
        reaction_conf = prob_day5_up
    elif prob_day5_up <= 0.40:
        reaction_bias = 'BEARISH DRIFT (Post-Earnings Weakness)'
        reaction_conf = 1.0 - prob_day5_up
    else:
        reaction_bias = 'CHOP / MEAN REVERTING'
        reaction_conf = 0.50

    return {
        'symbol': symbol.upper(),
        'fundamental_forecast': {
            'beat_probability_pct': round(pred_beat_prob * 100, 1),
            'expected_eps_surprise_pct': round(pred_surprise_pct, 2),
            'historical_beat_rate_pct': round(float(latest['prior_beat_rate']) * 100, 1),
            'current_beat_streak': int(latest['prior_streak'])
        },
        'market_reaction_forecast': {
            'expected_opening_gap_pct': round(pred_gap_pct, 2),
            'opening_gap_up_probability_pct': round(prob_gap_up * 100, 1),
            'day5_continuation_bias': reaction_bias,
            'day5_up_probability_pct': round(prob_day5_up * 100, 1),
            'gap_reversal_risk_pct': round(prob_reversal * 100, 1)
        },
        'expectation_tension': {
            'state': expectation_state,
            'pre_5d_drift_pct': round(float(latest['pre_5d_return_pct']), 2),
            'pre_20d_drift_pct': round(float(latest['pre_20d_return_pct']), 2),
            'tension_vs_hist_20d_pct': round(tension_20d, 2)
        },
        'ticker_archetype': {
            'avg_historical_gap_pct': round(float(latest['prior_avg_abs_gap']), 2),
            'historical_gap_fade_rate_pct': round((1.0 - float(latest['prior_gap_hold_rate'])) * 100, 1),
            'historical_reversal_rate_pct': round(float(latest['prior_reversal_rate']) * 100, 1)
        }
    }
