"""Time-travel holdout backtesting and strategy evaluation harness.

Evaluates predictive model accuracy and options strategy performance by:
  1. Splitting the dataset into a strict historical TRAIN set and a HOLDOUT test set
     (e.g., holding out the last 1, 2, or 3 months).
  2. Training models strictly on data prior to the cutoff date (no lookahead bias).
  3. Generating forecasts on the holdout period and evaluating directional accuracy,
     expected move error, gap reversal detection, and simulated options P&L.
"""
import os
import sqlite3
import datetime
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, mean_absolute_error, roc_auc_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from .config import DB_PATH, DATA_DIR
from .db import init_db
from .model import FEATURE_COLS, build_feature_dataset


def run_holdout_backtest(
    holdout_months: int = 1,
    cutoff_date: Optional[str] = None,
    conn=None,
    report_output: Optional[str] = None
) -> Dict[str, Any]:
    """Execute a strict time-travel holdout backtest.

    Args:
        holdout_months: Number of recent months to hold out (default 1).
        cutoff_date: Optional explicit ISO cutoff date (e.g. '2026-07-15').
        conn: SQLite connection.
        report_output: Optional path to write markdown scorecard.

    Returns:
        Dictionary with backtest metrics, strategy results, and event logs.
    """
    if conn is None:
        conn = init_db()

    # Load full dataset
    df = build_feature_dataset(conn)
    if df.empty or len(df) < 200:
        return {'error': 'Insufficient data for backtesting (need >= 200 events)'}

    # Normalize date for chronological sorting
    df['earnings_dt'] = pd.to_datetime(df['earnings_date'], utc=True)
    df = df.sort_values('earnings_dt').reset_index(drop=True)

    max_dt = df['earnings_dt'].max()

    if cutoff_date:
        cutoff_dt = pd.to_datetime(cutoff_date, utc=True)
    else:
        # Subtract approx days for holdout months (30.5 days per month)
        cutoff_dt = max_dt - pd.Timedelta(days=int(holdout_months * 30.5))

    cutoff_str = cutoff_dt.strftime('%Y-%m-%d')

    train_df = df[df['earnings_dt'] <= cutoff_dt].copy()
    holdout_df = df[df['earnings_dt'] > cutoff_dt].copy()

    if len(holdout_df) < 10:
        return {
            'error': f'Holdout period has too few events ({len(holdout_df)}). '
                     f'Max date is {max_dt.strftime("%Y-%m-%d")}. Try adjusting holdout months.'
        }

    # Prepare training features
    valid_train = train_df.dropna(subset=FEATURE_COLS + ['target_day1_up', 'target_day5_up', 'target_abs_gap']).copy()
    X_train = valid_train[FEATURE_COLS]

    # Train 1: Day-1 Direction Classifier
    clf_d1 = Pipeline([
        ('scaler', StandardScaler()),
        ('model', GradientBoostingClassifier(n_estimators=80, max_depth=3, random_state=42))
    ])
    clf_d1.fit(X_train, valid_train['target_day1_up'])

    # Train 2: Day-5 Direction Classifier
    clf_d5 = Pipeline([
        ('scaler', StandardScaler()),
        ('model', GradientBoostingClassifier(n_estimators=80, max_depth=3, random_state=42))
    ])
    clf_d5.fit(X_train, valid_train['target_day5_up'])

    # Train 3: Gap Reversal Classifier
    clf_rev = Pipeline([
        ('scaler', StandardScaler()),
        ('model', RandomForestClassifier(n_estimators=100, max_depth=4, random_state=42))
    ])
    clf_rev.fit(X_train, valid_train['target_reversal'])

    # Train 4: Expected Move Regressor (Absolute Gap %)
    reg_gap = Pipeline([
        ('scaler', StandardScaler()),
        ('model', GradientBoostingRegressor(n_estimators=80, max_depth=3, random_state=42))
    ])
    reg_gap.fit(X_train, valid_train['target_abs_gap'])

    # Out-of-sample forward evaluation on holdout set
    X_holdout = holdout_df[FEATURE_COLS]
    probs_d1 = clf_d1.predict_proba(X_holdout)[:, 1]
    probs_d5 = clf_d5.predict_proba(X_holdout)[:, 1]
    probs_rev = clf_rev.predict_proba(X_holdout)[:, 1]
    preds_gap = reg_gap.predict(X_holdout)

    events_evaluated = []

    for i, (idx, row) in enumerate(holdout_df.iterrows()):
        prob_d1_up = float(probs_d1[i])
        prob_d5_up = float(probs_d5[i])
        prob_rev = float(probs_rev[i])
        pred_gap = float(preds_gap[i])

        actual_gap = float(row['gap_pct']) if pd.notnull(row['gap_pct']) else 0.0
        actual_day1 = float(row['day1_return_pct']) if pd.notnull(row['day1_return_pct']) else 0.0
        actual_day5 = float(row['day5_return_pct']) if pd.notnull(row['day5_return_pct']) else 0.0
        actual_reversal = int(row['target_reversal']) if pd.notnull(row['target_reversal']) else 0

        # Classification
        if prob_d5_up >= 0.55:
            pred_direction = 'BULLISH'
            pred_dir_correct = (actual_day5 > 0)
        elif prob_d5_up <= 0.45:
            pred_direction = 'BEARISH'
            pred_dir_correct = (actual_day5 < 0)
        else:
            pred_direction = 'NEUTRAL'
            pred_dir_correct = None

        pred_reversal_flag = 1 if prob_rev >= 0.45 else 0

        # Determine Options Strategy
        if pred_direction == 'BULLISH' and prob_rev < 0.45:
            strategy = 'Debit Bull Call'
            # Wins if Day 5 is positive (target >= +1.5%), max 100% gain, max -100% loss
            if actual_day5 >= 1.5:
                sim_pnl_pct = min(100.0, actual_day5 * 20.0)
                outcome = 'WIN'
            elif actual_day5 > 0:
                sim_pnl_pct = 20.0
                outcome = 'WIN'
            else:
                sim_pnl_pct = -80.0
                outcome = 'LOSS'

        elif pred_direction == 'BEARISH' and prob_rev < 0.45:
            strategy = 'Debit Bear Put'
            # Wins if Day 5 is negative (target <= -1.5%)
            if actual_day5 <= -1.5:
                sim_pnl_pct = min(100.0, abs(actual_day5) * 20.0)
                outcome = 'WIN'
            elif actual_day5 < 0:
                sim_pnl_pct = 20.0
                outcome = 'WIN'
            else:
                sim_pnl_pct = -80.0
                outcome = 'LOSS'

        elif prob_rev >= 0.50:
            strategy = 'Gap Fade'
            if actual_reversal == 1:
                sim_pnl_pct = min(100.0, abs(actual_day5 - actual_gap) * 15.0)
                outcome = 'WIN'
            else:
                sim_pnl_pct = -75.0
                outcome = 'LOSS'

        elif pred_gap >= 4.5 and pred_direction == 'NEUTRAL':
            strategy = 'Long Straddle'
            if abs(actual_gap) >= pred_gap:
                sim_pnl_pct = (abs(actual_gap) - pred_gap) * 25.0
                outcome = 'WIN'
            else:
                sim_pnl_pct = -50.0
                outcome = 'LOSS'

        else:
            strategy = 'Iron Condor'
            # Wins if realized gap stays within expected move window (+/- 25% buffer)
            if abs(actual_gap) <= (pred_gap * 1.25):
                sim_pnl_pct = 35.0
                outcome = 'WIN'
            else:
                sim_pnl_pct = -100.0
                outcome = 'LOSS'

        events_evaluated.append({
            'symbol': row['symbol'],
            'earnings_date': row['earnings_date'][:10],
            'actual_eps_surprise': round(float(row['eps_surprise_pct']), 2) if pd.notnull(row['eps_surprise_pct']) else None,
            'pred_direction': pred_direction,
            'prob_day5_up': round(prob_d5_up, 3),
            'actual_gap_pct': round(actual_gap, 2),
            'actual_day5_pct': round(actual_day5, 2),
            'pred_expected_gap': round(pred_gap, 2),
            'gap_abs_error': round(abs(pred_gap - abs(actual_gap)), 2),
            'prob_reversal': round(prob_rev, 3),
            'actual_reversal': actual_reversal,
            'pred_reversal_flag': pred_reversal_flag,
            'strategy': strategy,
            'sim_pnl_pct': round(sim_pnl_pct, 1),
            'outcome': outcome
        })

    ev_df = pd.DataFrame(events_evaluated)

    # 1. Directional Accuracy Metrics
    dir_trades = ev_df[ev_df['pred_direction'] != 'NEUTRAL']
    if len(dir_trades) > 0:
        dir_wins = (
            ((dir_trades['pred_direction'] == 'BULLISH') & (dir_trades['actual_day5_pct'] > 0)) |
            ((dir_trades['pred_direction'] == 'BEARISH') & (dir_trades['actual_day5_pct'] < 0))
        )
        dir_accuracy = float(dir_wins.mean())
    else:
        dir_accuracy = 0.50

    # 2. Reversal Model Metrics
    rev_acc = float(accuracy_score(ev_df['actual_reversal'], ev_df['pred_reversal_flag']))
    try:
        rev_prec = float(precision_score(ev_df['actual_reversal'], ev_df['pred_reversal_flag'], zero_division=0))
        rev_rec = float(recall_score(ev_df['actual_reversal'], ev_df['pred_reversal_flag'], zero_division=0))
    except Exception:
        rev_prec, rev_rec = 0.0, 0.0

    # 3. Expected Gap Error
    gap_mae = float(ev_df['gap_abs_error'].mean())
    gap_corr = float(ev_df['pred_expected_gap'].corr(ev_df['actual_gap_pct'].abs()))

    # 4. Overall Options Strategy Simulation Metrics
    total_trades = len(ev_df)
    winning_trades = len(ev_df[ev_df['outcome'] == 'WIN'])
    losing_trades = len(ev_df[ev_df['outcome'] == 'LOSS'])
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

    gross_gains = ev_df[ev_df['sim_pnl_pct'] > 0]['sim_pnl_pct'].sum()
    gross_losses = abs(ev_df[ev_df['sim_pnl_pct'] < 0]['sim_pnl_pct'].sum())
    profit_factor = round(gross_gains / gross_losses, 2) if gross_losses > 0 else float('inf')
    avg_trade_pnl = float(ev_df['sim_pnl_pct'].mean())

    # Strategy breakdown
    strat_breakdown = {}
    for strat_name, sgroup in ev_df.groupby('strategy'):
        s_wins = len(sgroup[sgroup['outcome'] == 'WIN'])
        s_tot = len(sgroup)
        strat_breakdown[strat_name] = {
            'trades': s_tot,
            'win_rate': round(s_wins / s_tot, 3) if s_tot > 0 else 0.0,
            'avg_pnl_pct': round(float(sgroup['sim_pnl_pct'].mean()), 1)
        }

    results = {
        'cutoff_date': cutoff_str,
        'train_samples': len(train_df),
        'holdout_samples': len(holdout_df),
        'holdout_date_range': f"{holdout_df['earnings_dt'].min().strftime('%Y-%m-%d')} to {max_dt.strftime('%Y-%m-%d')}",
        'directional_trades_count': len(dir_trades),
        'directional_accuracy_pct': round(dir_accuracy * 100, 2),
        'reversal_accuracy_pct': round(rev_acc * 100, 2),
        'reversal_precision_pct': round(rev_prec * 100, 2),
        'reversal_recall_pct': round(rev_rec * 100, 2),
        'expected_gap_mae_pct': round(gap_mae, 2),
        'expected_gap_correlation': round(gap_corr, 3) if pd.notnull(gap_corr) else 0.0,
        'simulated_total_trades': total_trades,
        'simulated_win_rate_pct': round(win_rate * 100, 2),
        'simulated_profit_factor': profit_factor,
        'simulated_avg_pnl_pct': round(avg_trade_pnl, 2),
        'strategy_breakdown': strat_breakdown,
        'sample_events': events_evaluated[:15]
    }

    # Generate Markdown Scorecard
    md_report = generate_backtest_markdown(results, ev_df)
    if report_output:
        os.makedirs(os.path.dirname(report_output), exist_ok=True)
        with open(report_output, 'w') as f:
            f.write(md_report)
    else:
        default_path = os.path.join(DATA_DIR, 'backtest_report.md')
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(default_path, 'w') as f:
            f.write(md_report)

    return results


def generate_backtest_markdown(res: Dict[str, Any], ev_df: pd.DataFrame) -> str:
    """Format backtest results into a clear Markdown report."""
    lines = [
        "# Earnings Model — Time-Travel Holdout Backtest Scorecard",
        f"**Cutoff Date**: `{res['cutoff_date']}` | **Holdout Period**: `{res['holdout_date_range']}`",
        f"**Training Set**: {res['train_samples']} historical reports | **Holdout Test Set**: {res['holdout_samples']} reports",
        "",
        "---",
        "",
        "## 1. Executive Summary & Out-of-Sample Performance",
        "",
        "| Metric | Backtest Result | Benchmark / Target | Status |",
        "|---|---|---|---|",
        f"| **Simulated Strategy Win Rate** | **{res['simulated_win_rate_pct']}%** ({len(ev_df[ev_df['outcome'] == 'WIN'])}/{res['simulated_total_trades']}) | > 55.0% | {'✅ PASS' if res['simulated_win_rate_pct'] >= 55.0 else '⚠️ CAUTION'} |",
        f"| **Simulated Profit Factor** | **{res['simulated_profit_factor']}x** | > 1.30x | {'✅ PROFITABLE' if res['simulated_profit_factor'] > 1.20 else '⚠️ NEUTRAL'} |",
        f"| **Average Trade P&L** | **{res['simulated_avg_pnl_pct']:+.2f}%** | > 0.0% | {'✅ POSITIVE' if res['simulated_avg_pnl_pct'] > 0 else '❌ NEGATIVE'} |",
        f"| **Gap Reversal Accuracy** | **{res['reversal_accuracy_pct']}%** | > 70.0% | {'✅ STRONG' if res['reversal_accuracy_pct'] >= 70.0 else '⚠️ FAIR'} |",
        f"| **Expected Gap MAE** | **{res['expected_gap_mae_pct']}%** | < 2.50% | {'✅ ACCURATE' if res['expected_gap_mae_pct'] <= 2.50 else '⚠️ WIDE'} |",
        f"| **Directional Trade Accuracy** | **{res['directional_accuracy_pct']}%** (N={res['directional_trades_count']}) | > 50.0% | {'✅ ALPHA' if res['directional_accuracy_pct'] >= 52.0 else '⚠️ NEUTRAL'} |",
        "",
        "---",
        "",
        "## 2. Options Strategy Breakdown (Simulated Defined-Risk)",
        "",
        "| Strategy Structure | Total Trades | Win Rate % | Avg Return on Risk |",
        "|---|---|---|---|",
    ]

    for s_name, s_data in res.get('strategy_breakdown', {}).items():
        lines.append(f"| **{s_name}** | {s_data['trades']} | {s_data['win_rate'] * 100:.1f}% | {s_data['avg_pnl_pct']:+.1f}% |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Sample Holdout Events Audit (First 15 Reports)",
        "",
        "| Ticker | Report Date | Direction Bias | Expected Move | Actual Gap | Day 5 Move | Strategy Chosen | P&L % | Outcome |",
        "|---|---|---|---|---|---|---|---|---|",
    ])

    for ev in res.get('sample_events', []):
        lines.append(
            f"| **{ev['symbol']}** | {ev['earnings_date']} | {ev['pred_direction']} | "
            f"{ev['pred_expected_gap']:.2f}% | {ev['actual_gap_pct']:+.2f}% | {ev['actual_day5_pct']:+.2f}% | "
            f"{ev['strategy']} | {ev['sim_pnl_pct']:+.1f}% | {ev['outcome']} |"
        )

    return "\n".join(lines)
