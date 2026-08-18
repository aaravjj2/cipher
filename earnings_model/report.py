"""Report generation for the earnings model.

Produces markdown reports for individual symbols and the full universe,
and exports predictive features as CSV for further ML work.

All data is read via analyzer functions which use the correct table/column
names from the schema defined in db.py.
"""
import pandas as pd
import numpy as np
from . import analyzer
from . import db


def _fmt(val, suffix='%', decimals=2):
    """Format a numeric value for display, returning 'N/A' for None/NaN."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return 'N/A'
    return f"{val:.{decimals}f}{suffix}"


def generate_symbol_report(symbol, conn=None):
    """Generate a markdown report for a single symbol combining all analysis."""
    profile = analyzer.earnings_profile(symbol, conn)
    if not profile:
        return f"# Earnings Report: {symbol}\n\nNo earnings data available for {symbol}."

    features = analyzer.predictive_features(symbol, conn)
    score = analyzer.upcoming_earnings_score(symbol, conn)
    pattern = analyzer.historical_pattern(symbol, conn)

    report = [
        f"# Earnings Analysis Report: {symbol}",
        "---",
        "## 1. Earnings Profile",
        f"- **Total Events Analyzed**: {profile.get('total_events', 0)}",
        f"- **Beat Rate**: {profile.get('beat_rate', 0):.1%}",
        f"- **Miss Rate**: {profile.get('miss_rate', 0):.1%}",
        f"- **Current Streak**: {profile.get('streak', 0)} (positive=beats, negative=misses)",
        f"- **Average Surprise**: {_fmt(profile.get('avg_surprise_pct'))}",
        f"- **Median Surprise**: {_fmt(profile.get('median_surprise_pct'))}",
        "",
        "## 2. Price Reaction (Historical Averages)",
        f"- **Average Gap**: {_fmt(profile.get('avg_gap_pct'))}",
        f"- **Average Day 1 Return**: {_fmt(profile.get('avg_day1_return_pct'))}",
        f"- **Average Day 5 Return**: {_fmt(profile.get('avg_day5_return_pct'))}",
        f"- **Gap Direction Accuracy**: {_fmt(profile.get('gap_dir_accuracy'), suffix='', decimals=1)}% (how often day 5 matches gap direction)",
        f"- **Average Volume Ratio**: {_fmt(profile.get('avg_volume_ratio'), suffix='x')}",
        "",
        "## 3. Predictive Features",
        f"- **Pre-Earnings 5D Momentum**: {_fmt(features.get('pre_5d_momentum') if features else None)}",
        f"- **Average Absolute Gap**: {_fmt(features.get('avg_abs_gap_pct') if features else None)}",
        f"- **Gap Reversal Rate**: {_fmt(features.get('gap_reversal_rate') if features else None, suffix='', decimals=1)}%",
        f"- **Revenue Growth Acceleration**: {_fmt(features.get('rev_growth_accel') if features else None)}",
        "",
        "## 4. Upcoming Earnings Score",
    ]

    if score:
        report.extend([
            f"**Composite Score: {_fmt(score.get('composite_score'), suffix='', decimals=1)} / 100**",
            f"- Beat Probability: {score.get('beat_probability', 0):.1%}",
            f"- Expected Move Magnitude: {_fmt(score.get('expected_move_pct'))}",
            f"- Direction Confidence: {_fmt(score.get('direction_confidence'), suffix='', decimals=2)}",
            f"- Risk Score: {_fmt(score.get('risk_score'), suffix='', decimals=1)} / 100",
        ])
    else:
        report.append("Not enough data to compute score.")

    report.extend(["", "## 5. Seasonality (Day 5 Return by Fiscal Quarter)"])

    seasonality = pattern.get('seasonality', {}) if pattern else {}
    for q, ret in sorted(seasonality.items()):
        report.append(f"- **{q}**: {_fmt(ret)}")

    if pattern and pattern.get('pre_drift'):
        pd_data = pattern['pre_drift']
        report.extend([
            "",
            "## 6. Pre-Earnings Drift",
            f"- **Average 5-Day Pre-Earnings Return**: {_fmt(pd_data.get('avg_pre_5d_pct'))}",
            f"- **Average 20-Day Pre-Earnings Return**: {_fmt(pd_data.get('avg_pre_20d_pct'))}",
        ])

    return "\n".join(report)


def generate_universe_summary(conn=None, tiers=None):
    """Generate a summary report across the universe.

    Reports top-10 lists, surprise-impact correlation, and cap tier
    comparison.  Requires at least 4 earnings events per symbol.
    """
    # Build per-symbol stats from the merged data
    df = analyzer._get_merged_df(None, conn)
    if df.empty:
        return "No earnings data available."

    if tiers:
        df = df[df['cap_tier'].isin(tiers)]

    report = [
        "# Universe Earnings Summary",
        "---",
    ]

    symbols = df['symbol'].unique()
    stats = []

    for sym in symbols:
        sym_df = df[df['symbol'] == sym].copy()
        if len(sym_df) < 4:
            continue

        sym_df['is_beat'] = sym_df['eps_actual'] > sym_df['eps_estimate']
        beat_rate = sym_df['is_beat'].mean()
        avg_gap = sym_df['gap_pct'].mean()
        avg_abs_gap = sym_df['gap_pct'].abs().mean()
        avg_day5 = sym_df['day5_return_pct'].mean()

        stats.append({
            'symbol': sym,
            'beat_rate': beat_rate,
            'avg_gap_pct': avg_gap,
            'avg_abs_gap_pct': avg_abs_gap,
            'avg_day5_pct': avg_day5,
            'count': len(sym_df),
        })

    if not stats:
        return "Not enough data to generate summary."

    stats_df = pd.DataFrame(stats)

    # Top 10 beat rates
    report.append("## Top 10 Highest Beat Rates (min 4 events)")
    top_beats = stats_df.sort_values('beat_rate', ascending=False).head(10)
    for _, row in top_beats.iterrows():
        report.append(f"- **{row['symbol']}**: {row['beat_rate']:.1%} ({int(row['count'])} events)")

    # Top 10 largest gaps
    report.append("\n## Top 10 Largest Average Absolute Gaps")
    top_gaps = stats_df.sort_values('avg_abs_gap_pct', ascending=False).head(10)
    for _, row in top_gaps.iterrows():
        report.append(f"- **{row['symbol']}**: {_fmt(row['avg_abs_gap_pct'])}")

    # Top 10 best day5 returns
    report.append("\n## Top 10 Best Day 5 Returns After Earnings")
    top_returns = stats_df.dropna(subset=['avg_day5_pct']).sort_values('avg_day5_pct', ascending=False).head(10)
    for _, row in top_returns.iterrows():
        report.append(f"- **{row['symbol']}**: {_fmt(row['avg_day5_pct'])}")

    # Surprise-impact correlation
    report.append("\n## Surprise-Impact Correlation")
    impact = analyzer.surprise_impact_correlation(conn)
    corr = impact.get('correlation')
    if corr is not None and not np.isnan(corr):
        report.append(f"Overall Correlation (Surprise % vs Day 1 Return): {corr:.3f}")
    else:
        report.append("Correlation: N/A")

    report.append("\n### Return by Surprise Bin:")
    bins_data = impact.get('bins', {})
    for b_name, b_stats in bins_data.items():
        count = b_stats.get('count', 0)
        if count and count > 0:
            report.append(
                f"- **{b_name}**: Gap {_fmt(b_stats.get('avg_gap_pct'))}, "
                f"Day1 {_fmt(b_stats.get('avg_day1_return_pct'))}, "
                f"Day5 {_fmt(b_stats.get('avg_day5_return_pct'))} "
                f"(N={int(count)})"
            )

    # Cap tier comparison
    report.append("\n## Cap Tier Comparison")
    tiers_data = analyzer.sector_patterns(conn)
    for tier, t_stats in tiers_data.items():
        count = t_stats.get('count', 0)
        if count and count > 0:
            report.append(
                f"- **{tier}**: Beat Rate {t_stats.get('beat_rate', 0):.1%}, "
                f"Avg Gap {_fmt(t_stats.get('avg_gap_pct'))}, "
                f"Avg Day 5 {_fmt(t_stats.get('avg_day5_return_pct'))} "
                f"(N={int(count)})"
            )

    return "\n".join(report)


def export_features_csv(output_path, conn=None):
    """Export predictive features for all symbols as CSV for further ML work."""
    df = analyzer._get_merged_df(None, conn)
    if df.empty:
        print("No data available to export.")
        return

    symbols = df['symbol'].unique()
    features_list = []

    for sym in symbols:
        try:
            feat = analyzer.predictive_features(sym, conn)
            score = analyzer.upcoming_earnings_score(sym, conn)

            row = {'symbol': sym}
            if feat:
                row.update(feat)
            if score:
                row.update(score)

            features_list.append(row)
        except Exception as e:
            print(f"Error processing {sym}: {e}")

    if features_list:
        out_df = pd.DataFrame(features_list)
        out_df.to_csv(output_path, index=False)
        print(f"Exported features for {len(features_list)} symbols to {output_path}")
