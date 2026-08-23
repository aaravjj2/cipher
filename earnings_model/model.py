"""Machine Learning predictive modeling engine for earnings events.

Trains lookahead-free predictive models combining historical fundamentals,
momentum drift, and pre-earnings news sentiment to forecast:
  1. Directional move probability (Day 1 and Day 5)
  2. Expected move magnitude (Absolute Gap and Day 5 range)
  3. Gap reversal / mean-reversion risk
"""
import os
import joblib
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

from sklearn.base import clone
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score, mean_absolute_error
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from .config import DB_PATH, DATA_DIR
from .db import init_db


MODEL_ARTIFACT_PATH = os.path.join(DATA_DIR, 'earnings_models.joblib')
MODEL_VERSION = 'earnings-v2.0-2026-08-23'
TRADE_CONFIDENCE = 0.58
MIN_TRADE_HOLDOUT = 100
MIN_DIRECTION_ACCURACY = 0.55
MIN_DIRECTION_LIFT = 0.02
MIN_GAP_MAE_IMPROVEMENT = 0.05


def build_feature_dataset(conn=None, symbol=None) -> pd.DataFrame:
    """Build a point-in-time, lookahead-free dataset for ML modeling.

    For each earnings event, calculates rolling features strictly using
    data available PRIOR to that report.
    """
    own_connection = conn is None
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
    if own_connection:
        conn.close()
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

    # SQLite may return all-null joined columns as object dtype. Normalize once
    # at the boundary so fillna remains stable across pandas releases.
    for col in (
        'pre_news_count', 'pre_news_sentiment_avg', 'pre_news_pos_ratio',
        'pre_news_neg_ratio', 'pre_news_unc_ratio',
    ):
        feat_df[col] = pd.to_numeric(feat_df[col], errors='coerce').fillna(0.0)

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


def _classifier_candidates() -> Dict[str, Any]:
    """Small candidate set; validation chooses, untouched holdout judges."""
    return {
        'logistic': Pipeline([
            ('scaler', StandardScaler()),
            ('model', LogisticRegression(C=0.1, max_iter=2000, random_state=42)),
        ]),
        'gradient_boosting': GradientBoostingClassifier(
            n_estimators=50, max_depth=2, random_state=42,
        ),
        'random_forest': RandomForestClassifier(
            n_estimators=150, max_depth=4, min_samples_leaf=20, random_state=42,
        ),
    }


def _fit_classifier(
    train_df: pd.DataFrame,
    validation_df: pd.DataFrame,
    holdout_df: pd.DataFrame,
    target: str,
) -> tuple[Any, Dict[str, Any]]:
    """Select on validation Brier score, then report one untouched holdout."""
    fitted = {}
    validation = {}
    for name, candidate in _classifier_candidates().items():
        model = clone(candidate).fit(train_df[FEATURE_COLS], train_df[target])
        probabilities = model.predict_proba(validation_df[FEATURE_COLS])[:, 1]
        validation[name] = {
            'brier': float(brier_score_loss(validation_df[target], probabilities)),
            'accuracy': float(accuracy_score(validation_df[target], probabilities >= 0.5)),
        }
        fitted[name] = model
    selected_name = min(validation, key=lambda name: (validation[name]['brier'], name))
    selected = fitted[selected_name]
    probabilities = selected.predict_proba(holdout_df[FEATURE_COLS])[:, 1]
    predictions = probabilities >= 0.5
    train_rate = float(train_df[target].mean())
    baseline_label = train_rate >= 0.5
    baseline_probabilities = np.full(len(holdout_df), train_rate)
    confidence_mask = (probabilities >= TRADE_CONFIDENCE) | (probabilities <= 1 - TRADE_CONFIDENCE)
    gated_n = int(confidence_mask.sum())
    gated_accuracy = (
        float(accuracy_score(holdout_df.loc[confidence_mask, target], predictions[confidence_mask]))
        if gated_n else None
    )
    gated_baseline = (
        float(accuracy_score(
            holdout_df.loc[confidence_mask, target],
            np.full(gated_n, baseline_label),
        )) if gated_n else None
    )
    brier = float(brier_score_loss(holdout_df[target], probabilities))
    baseline_brier = float(brier_score_loss(holdout_df[target], baseline_probabilities))
    eligible = bool(
        gated_n >= MIN_TRADE_HOLDOUT
        and gated_accuracy is not None
        and gated_baseline is not None
        and gated_accuracy >= MIN_DIRECTION_ACCURACY
        and gated_accuracy - gated_baseline >= MIN_DIRECTION_LIFT
        and brier < baseline_brier
    )
    metrics = {
        'selected_candidate': selected_name,
        'accuracy': round(float(accuracy_score(holdout_df[target], predictions)), 4),
        'roc_auc': round(
            float(roc_auc_score(holdout_df[target], probabilities))
            if holdout_df[target].nunique() > 1 else 0.5,
            4,
        ),
        'brier': round(brier, 4),
        'baseline_accuracy': round(float(accuracy_score(
            holdout_df[target], np.full(len(holdout_df), baseline_label)
        )), 4),
        'baseline_brier': round(baseline_brier, 4),
        'gated_samples': gated_n,
        'gated_accuracy': round(gated_accuracy, 4) if gated_accuracy is not None else None,
        'gated_baseline_accuracy': round(gated_baseline, 4) if gated_baseline is not None else None,
        'trade_eligible': eligible,
        'validation_candidates': {
            name: {key: round(value, 4) for key, value in values.items()}
            for name, values in validation.items()
        },
    }
    # Holdout stays untouched: only after recording it do we fit the production
    # copy on the combined development window.
    development = pd.concat([train_df, validation_df], ignore_index=True)
    production = clone(_classifier_candidates()[selected_name]).fit(
        development[FEATURE_COLS], development[target]
    )
    return production, metrics


def _fit_gap_regressor(
    train_df: pd.DataFrame,
    validation_df: pd.DataFrame,
    holdout_df: pd.DataFrame,
) -> tuple[Any, Dict[str, Any]]:
    candidates = {
        'ridge': Pipeline([('scaler', StandardScaler()), ('model', Ridge(alpha=10.0))]),
        'gradient_boosting': GradientBoostingRegressor(
            n_estimators=50, max_depth=2, loss='huber', random_state=42,
        ),
    }
    validation_mae = {}
    for name, candidate in candidates.items():
        model = clone(candidate).fit(train_df[FEATURE_COLS], train_df['target_abs_gap'])
        prediction = np.maximum(0.0, model.predict(validation_df[FEATURE_COLS]))
        validation_mae[name] = float(mean_absolute_error(validation_df['target_abs_gap'], prediction))
    selected_name = min(validation_mae, key=lambda name: (validation_mae[name], name))
    selected = clone(candidates[selected_name]).fit(
        train_df[FEATURE_COLS], train_df['target_abs_gap']
    )
    prediction = np.maximum(0.0, selected.predict(holdout_df[FEATURE_COLS]))
    mae = float(mean_absolute_error(holdout_df['target_abs_gap'], prediction))
    baseline_value = float(train_df['target_abs_gap'].median())
    baseline_mae = float(mean_absolute_error(
        holdout_df['target_abs_gap'], np.full(len(holdout_df), baseline_value)
    ))
    improvement = (baseline_mae - mae) / baseline_mae if baseline_mae else 0.0
    development = pd.concat([train_df, validation_df], ignore_index=True)
    production = clone(candidates[selected_name]).fit(
        development[FEATURE_COLS], development['target_abs_gap']
    )
    return production, {
        'selected_candidate': selected_name,
        'mae_pct': round(mae, 4),
        'baseline_mae_pct': round(baseline_mae, 4),
        'mae_improvement_pct': round(improvement * 100.0, 2),
        'trade_eligible': improvement >= MIN_GAP_MAE_IMPROVEMENT,
        'validation_candidates': {
            name: round(value, 4) for name, value in validation_mae.items()
        },
    }


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

    # Chronological 60/20/20: candidate selection never touches the final holdout.
    train_end = int(len(valid) * 0.6)
    validation_end = int(len(valid) * 0.8)
    train_df = valid.iloc[:train_end]
    validation_df = valid.iloc[train_end:validation_end]
    test_df = valid.iloc[validation_end:]

    results = {
        'total_samples': len(valid),
        'model_version': MODEL_VERSION,
        'train_samples': len(train_df),
        'validation_samples': len(validation_df),
        'test_samples': len(test_df),
        'models': {}
    }

    trained_artifacts = {}

    clf_day1, results['models']['day1_direction'] = _fit_classifier(
        train_df, validation_df, test_df, 'target_day1_up'
    )
    trained_artifacts['day1_direction'] = clf_day1

    clf_day5, results['models']['day5_direction'] = _fit_classifier(
        train_df, validation_df, test_df, 'target_day5_up'
    )
    trained_artifacts['day5_direction'] = clf_day5

    clf_rev, results['models']['gap_reversal'] = _fit_classifier(
        train_df, validation_df, test_df, 'target_reversal'
    )
    trained_artifacts['gap_reversal'] = clf_rev

    reg_gap, results['models']['expected_abs_gap'] = _fit_gap_regressor(
        train_df, validation_df, test_df
    )
    trained_artifacts['expected_abs_gap'] = reg_gap

    results['strategy_gate'] = {
        'status': (
            'ELIGIBLE_FOR_PROSPECTIVE_PAPER'
            if results['models']['day5_direction']['trade_eligible']
            else 'NO_TRADE_DIRECTION_FAILED_HOLDOUT'
        ),
        'paper_entry_allowed': results['models']['day5_direction']['trade_eligible'],
        'live_options_pnl_validated': False,
    }

    if save_artifacts:
        os.makedirs(DATA_DIR, exist_ok=True)
        joblib.dump({
            'artifacts': trained_artifacts,
            'feature_cols': FEATURE_COLS,
            'model_version': MODEL_VERSION,
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


def predict_for_symbol(
    symbol: str,
    conn=None,
    feature_overrides: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Generate predictive signals and option strategy recommendation for a symbol."""
    own_connection = conn is None
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
    if own_connection:
        conn.close()
    if sym_df.empty:
        return {'error': f'No historical data found for {symbol}'}

    # Latest record as input features
    latest = sym_df.iloc[-1].copy()
    for name, value in (feature_overrides or {}).items():
        if name in cols and value is not None:
            latest[name] = float(value)
    input_features = pd.DataFrame([latest[cols]])

    # Generate predictions
    prob_day1_up = float(artifacts['day1_direction'].predict_proba(input_features)[0, 1])
    prob_day5_up = float(artifacts['day5_direction'].predict_proba(input_features)[0, 1])
    prob_reversal = float(artifacts['gap_reversal'].predict_proba(input_features)[0, 1])
    pred_abs_gap = float(artifacts['expected_abs_gap'].predict(input_features)[0])

    validation = models_data.get('results', {})
    strategy_gate = validation.get('strategy_gate', {})
    direction_eligible = bool(strategy_gate.get('paper_entry_allowed'))

    # Direction is descriptive unless the untouched holdout gate passed.
    if direction_eligible and prob_day5_up >= TRADE_CONFIDENCE:
        direction = 'BULLISH'
        confidence = prob_day5_up
    elif direction_eligible and prob_day5_up <= 1 - TRADE_CONFIDENCE:
        direction = 'BEARISH'
        confidence = 1.0 - prob_day5_up
    else:
        direction = 'NEUTRAL / MIXED'
        confidence = 0.50

    # Strategy Selection Matrix
    if not direction_eligible:
        primary_strategy = 'NO TRADE — direction model failed independent holdout gate'
        rationale = 'Continue collecting prospective observations; expected gap is descriptive only.'
    elif direction == 'BULLISH' and prob_reversal < 0.45:
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
        'model_version': models_data.get('model_version', 'legacy-unversioned'),
        'validation_status': strategy_gate.get('status', 'UNVALIDATED'),
        'strategy_eligible': direction_eligible,
        'inputs_snapshot': {
            'prior_beat_rate': round(float(latest['prior_beat_rate']), 3),
            'prior_streak': int(latest['prior_streak']),
            'pre_5d_drift_pct': round(float(latest['pre_5d_return_pct']), 2),
            'pre_20d_drift_pct': round(float(latest['pre_20d_return_pct']), 2),
            'pre_news_sentiment': round(float(latest['pre_news_sentiment_avg']), 3),
            'pre_news_count': int(latest['pre_news_count']),
            'market_drift_source': 'current' if feature_overrides else 'latest_historical_event',
        }
    }
