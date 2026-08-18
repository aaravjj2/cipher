"""Earnings Model Command-Line Interface.

Commands:
  collect        Ingest earnings reports and price impact data
  collect-news   Ingest and score pre/post earnings news
  train          Train machine learning predictive models
  predict        Generate forecast and option strategy recommendation
  backtest       Execute time-travel holdout backtest and strategy simulation
  radar / scan   Scan upcoming earnings and generate trade cards
  status         Show SQLite database counts and fetch health
  summary        Display universe rankings and performance statistics
  report         Display detailed report for a single ticker
  export         Export full earnings dataset to CSV
"""
import argparse
import sys
import json
import pandas as pd

from .collector import run_collection
from .news import run_news_collection, collect_news_for_symbol
from .model import train_earnings_models, predict_for_symbol
from .reaction_predictor import predict_stock_reaction, train_reaction_pipeline
from .report import generate_symbol_report, generate_universe_summary
from .backtest import run_holdout_backtest
from .scanner import find_upcoming_earnings, render_radar_table
from .paper_portfolio import enter_this_week_paper_book, get_active_paper_positions, render_paper_book_table
from .discord_bot import notify_discord_paper_book, notify_discord_weekly_preview, get_discord_webhook_url
from .db import init_db, get_all_earnings, get_price_impact


def main():
    parser = argparse.ArgumentParser(
        description="Cipher Earnings Model Research & Forecasting System",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # 1. Collect Earnings & Price Impact
    collect_parser = subparsers.add_parser('collect', help='Ingest earnings events and price impacts')
    collect_parser.add_argument('--tiers', type=str, help='Comma-separated cap tiers (e.g. mega,large,medium,small)')
    collect_parser.add_argument('--symbol', type=str, help='Specific symbol to collect')
    collect_parser.add_argument('--force', action='store_true', help='Refetch existing symbols')

    # 2. Collect News & Sentiment
    news_parser = subparsers.add_parser('collect-news', help='Ingest and score pre/post earnings news')
    news_parser.add_argument('--tiers', type=str, help='Comma-separated cap tiers (e.g. mega,large)')
    news_parser.add_argument('--symbol', type=str, help='Specific symbol to fetch news for')
    news_parser.add_argument('--max-events', type=int, default=12, help='Max recent earnings events per symbol')

    # 3. Train ML Models
    train_parser = subparsers.add_parser('train', help='Train predictive ML models on historical dataset')

    # 4. Predict (Classic Strategy Mode)
    predict_parser = subparsers.add_parser('predict', help='Predict earnings move and suggest option strategy')
    predict_parser.add_argument('--symbol', type=str, required=True, help='Ticker symbol (e.g. NVDA, AAPL)')

    # 4B. Reaction Forecast (2-Stage Fundamental & Market Reaction Engine)
    reaction_parser = subparsers.add_parser('reaction', help='2-Stage Fundamental Beat & Stock Reaction Forecast')
    reaction_parser.add_argument('--symbol', type=str, required=True, help='Ticker symbol (e.g. NVDA, TSLA, MSFT)')

    # 5. Backtest (Holdout Time-Travel Validation)
    backtest_parser = subparsers.add_parser('backtest', help='Run time-travel holdout backtest simulation')
    backtest_parser.add_argument('--holdout-months', type=int, default=1, help='Number of recent months to hold out (default 1)')
    backtest_parser.add_argument('--cutoff-date', type=str, help='Explicit cutoff date (YYYY-MM-DD) for training split')
    backtest_parser.add_argument('--report-output', type=str, help='Path to write Markdown scorecard')

    # 6. Upcoming Radar / Scan
    radar_parser = subparsers.add_parser('radar', help='Scan upcoming earnings in the next 1-4 weeks')
    radar_parser.add_argument('--days', type=int, default=14, help='Days ahead to scan (default 14)')
    radar_parser.add_argument('--tiers', type=str, help='Cap tiers to filter (e.g. mega,large)')
    radar_parser.add_argument('--symbol', type=str, help='Specific ticker to check')

    scan_parser = subparsers.add_parser('scan', help='Alias for radar')
    scan_parser.add_argument('--days', type=int, default=14, help='Days ahead to scan (default 14)')
    scan_parser.add_argument('--tiers', type=str, help='Cap tiers to filter (e.g. mega,large)')
    scan_parser.add_argument('--symbol', type=str, help='Specific ticker to check')

    # 7. Status
    status_parser = subparsers.add_parser('status', help='Show database status and fetch logs')

    # 8. Summary Report
    summary_parser = subparsers.add_parser('summary', help='Display universe summary report')
    summary_parser.add_argument('--tiers', type=str, help='Filter by cap tiers')

    # 9. Symbol Report
    report_parser = subparsers.add_parser('report', help='Display detailed report for a single ticker')
    report_parser.add_argument('--symbol', type=str, required=True, help='Ticker symbol')

    # 10. Export CSV
    export_parser = subparsers.add_parser('export', help='Export dataset to CSV file')
    export_parser.add_argument('--output', type=str, required=True, help='Output file path')

    # 11. Paper Options Portfolio
    paper_enter_parser = subparsers.add_parser('paper-enter', help='Simulate entering paper options trades for this week')
    paper_enter_parser.add_argument('--risk-per-trade', type=float, default=2000.0, help='Max allocated risk per position ($)')

    paper_book_parser = subparsers.add_parser('paper-book', help='Display active paper options positions and risk')

    # 12. Discord Webhook Digest
    discord_parser = subparsers.add_parser('notify-discord', help='Deliver earnings radar and paper book to Discord')
    discord_parser.add_argument('--type', choices=['preview', 'portfolio', 'all'], default='all', help='Notification type')
    discord_parser.add_argument('--webhook-url', type=str, help='Override Discord Webhook URL')

    args = parser.parse_args()

    if args.command == 'collect':
        tiers = [t.strip() for t in args.tiers.split(',')] if args.tiers else None
        symbols = [args.symbol.upper()] if args.symbol else None
        run_collection(tiers=tiers, symbols=symbols, skip_existing=not args.force)

    elif args.command == 'collect-news':
        tiers = [t.strip() for t in args.tiers.split(',')] if args.tiers else None
        symbols = [args.symbol.upper()] if args.symbol else None
        run_news_collection(symbols=symbols, tiers=tiers, max_events_per_symbol=args.max_events)

    elif args.command == 'train':
        print("Training earnings prediction models...")
        results = train_earnings_models()
        print("\n=== Model Training Complete ===")
        print(f"Total Samples: {results.get('total_samples')} (Train: {results.get('train_samples')}, Test: {results.get('test_samples')})")
        print("\nModel Metrics:")
        for m_name, m_res in results.get('models', {}).items():
            print(f"  {m_name:20s}: {m_res}")
        print("\nTop Feature Importances:")
        for feat, imp in list(results.get('feature_importances', {}).items())[:8]:
            print(f"  {feat:25s}: {imp:.4f}")

    elif args.command == 'predict':
        pred = predict_for_symbol(args.symbol)
        if 'error' in pred:
            print(f"Error: {pred['error']}")
            return

        print(f"\n=======================================================")
        print(f"  EARNINGS PREDICTION & STRATEGY FORECAST: {pred['symbol']}")
        print(f"=======================================================")
        print(f"Direction Bias     : {pred['direction']} (Confidence: {pred['confidence']:.1%})")
        print(f"Expected Move (Gap): {pred['expected_gap_pct']:.2f}%")
        print(f"Day-1 Up Prob      : {pred['prob_day1_up']:.1%}")
        print(f"Day-5 Up Prob      : {pred['prob_day5_up']:.1%}")
        print(f"Gap Reversal Risk  : {pred['prob_reversal']:.1%}")
        print(f"\nRecommended Options Strategy:")
        print(f"  -> {pred['primary_strategy']}")
        print(f"Rationale: {pred['rationale']}")
        print(f"\nHistorical Feature Snapshot:")
        for k, v in pred.get('inputs_snapshot', {}).items():
            print(f"  {k:22s}: {v}")
        print(f"=======================================================\n")

    elif args.command == 'reaction':
        res = predict_stock_reaction(args.symbol)
        if 'error' in res:
            print(f"Error: {res['error']}")
            return

        f = res['fundamental_forecast']
        m = res['market_reaction_forecast']
        t = res['expectation_tension']
        a = res['ticker_archetype']

        print(f"\n=========================================================================")
        print(f"           2-STAGE STOCK & EARNINGS REACTION FORECAST: {res['symbol']}")
        print(f"=========================================================================")
        print(f"1. FUNDAMENTAL EVENT PREDICTION:")
        print(f"   - EPS Beat Probability   : {f['beat_probability_pct']}% (Historical: {f['historical_beat_rate_pct']}%)")
        print(f"   - Expected EPS Surprise  : {f['expected_eps_surprise_pct']:+.2f}%")
        print(f"   - Historical Streak      : {f['current_beat_streak']} consecutive {'beats' if f['current_beat_streak'] >= 0 else 'misses'}")
        print(f"")
        print(f"2. EXPECTATION TENSION & PRICED-IN SENTIMENT:")
        print(f"   - Market State           : {t['state']}")
        print(f"   - Pre-Earnings 5D Drift  : {t['pre_5d_drift_pct']:+.2f}%")
        print(f"   - Pre-Earnings 20D Drift : {t['pre_20d_drift_pct']:+.2f}% (Tension vs Hist: {t['tension_vs_hist_20d_pct']:+.2f}%)")
        print(f"")
        print(f"3. MARKET REACTION & PRICE RESPONSE:")
        print(f"   - Expected Opening Gap   : {m['expected_opening_gap_pct']:+.2f}%")
        print(f"   - Opening Gap-Up Prob    : {m['opening_gap_up_probability_pct']}%")
        print(f"   - Day-5 PEAD Horizon     : {m['day5_continuation_bias']} (Up Prob: {m['day5_up_probability_pct']}%)")
        print(f"   - Gap Reversal Risk      : {m['gap_reversal_risk_pct']}%")
        print(f"")
        print(f"4. HISTORICAL REACTION ARCHETYPE:")
        print(f"   - Average Historic Gap   : {a['avg_historical_gap_pct']:.2f}%")
        print(f"   - Gap-Fade Tendency      : {a['historical_gap_fade_rate_pct']}% (Day 1 closes weaker than open)")
        print(f"   - Multi-Day Reversal Rate: {a['historical_reversal_rate_pct']}%")
        print(f"=========================================================================\n")

    elif args.command == 'backtest':
        print(f"Executing holdout backtest (holding out last {args.holdout_months} month(s))...")
        res = run_holdout_backtest(
            holdout_months=args.holdout_months,
            cutoff_date=args.cutoff_date,
            report_output=args.report_output
        )
        if 'error' in res:
            print(f"Error: {res['error']}")
            return

        print(f"\n=======================================================================")
        print(f"              HOLDOUT BACKTEST SCORECARD (Time-Travel Validation)")
        print(f"=======================================================================")
        print(f"Cutoff Date           : {res['cutoff_date']}")
        print(f"Holdout Window        : {res['holdout_date_range']}")
        print(f"Training Reports      : {res['train_samples']} historical quarters")
        print(f"Holdout Test Reports  : {res['holdout_samples']} out-of-sample quarters")
        print(f"-----------------------------------------------------------------------")
        print(f"Simulated Win Rate    : {res['simulated_win_rate_pct']:.1f}%")
        print(f"Profit Factor         : {res['simulated_profit_factor']}x")
        print(f"Average Trade P&L     : {res['simulated_avg_pnl_pct']:+.2f}%")
        print(f"Gap Reversal Accuracy : {res['reversal_accuracy_pct']:.1f}%")
        print(f"Expected Gap Error    : {res['expected_gap_mae_pct']:.2f}% MAE")
        print(f"Directional Accuracy  : {res['directional_accuracy_pct']:.1f}% (N={res['directional_trades_count']})")
        print(f"-----------------------------------------------------------------------")
        print(f"Strategy Performance Breakdown:")
        for s_name, s_data in res.get('strategy_breakdown', {}).items():
            print(f"  {s_name:18s} | {s_data['trades']:3d} trades | Win Rate: {s_data['win_rate']*100:5.1f}% | Avg PnL: {s_data['avg_pnl_pct']:+5.1f}%")
        print(f"=======================================================================\n")
        print("Detailed scorecard saved to earnings_model/data/backtest_report.md\n")

    elif args.command in ('radar', 'scan'):
        tiers = [t.strip() for t in args.tiers.split(',')] if args.tiers else None
        symbols = [args.symbol.upper()] if args.symbol else None
        print(f"Scanning for upcoming earnings in the next {args.days} days...")
        cards = find_upcoming_earnings(days_ahead=args.days, tiers=tiers, symbols=symbols)
        print(render_radar_table(cards))

    elif args.command == 'status':
        conn = init_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM earnings_events")
        events_count = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) as cnt FROM price_impact")
        impact_count = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) as cnt FROM earnings_news")
        news_count = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) as cnt FROM earnings_news_metrics")
        news_metrics_count = c.fetchone()['cnt']

        c.execute("SELECT status, fetch_type, COUNT(*) as cnt FROM fetch_log GROUP BY fetch_type, status")
        log_counts = c.fetchall()

        print("\n--- Cipher Earnings Database Status ---")
        print(f"Total Earnings Events : {events_count}")
        print(f"Total Price Impacts   : {impact_count}")
        print(f"Total News Articles   : {news_count}")
        print(f"News Metric Snapshots : {news_metrics_count}")
        print("\nFetch Health Ledger:")
        for row in log_counts:
            print(f"  [{row['fetch_type'].upper()}] {row['status']}: {row['cnt']} symbols")
        print("---------------------------------------\n")
        conn.close()

    elif args.command == 'summary':
        conn = init_db()
        tiers = [t.strip() for t in args.tiers.split(',')] if args.tiers else None
        print(generate_universe_summary(conn=conn, tiers=tiers))
        conn.close()

    elif args.command == 'report':
        conn = init_db()
        print(generate_symbol_report(args.symbol.upper(), conn=conn))
        conn.close()

    elif args.command == 'export':
        conn = init_db()
        events = get_all_earnings(conn)
        impacts = get_price_impact(conn)

        df_e = pd.DataFrame(events)
        df_i = pd.DataFrame(impacts)

        if not df_e.empty and not df_i.empty:
            df_merged = pd.merge(df_e, df_i, on=['symbol', 'earnings_date'], how='left', suffixes=('', '_y'))
            df_merged.drop(columns=[col for col in df_merged.columns if col.endswith('_y')], inplace=True)
            df_merged.to_csv(args.output, index=False)
            print(f"Exported {len(df_merged)} merged records to {args.output}")
        else:
            print("No data to export.")
        conn.close()

    elif args.command == 'paper-enter':
        print(f"Generating optimal defined-risk paper orders for this week ($ {args.risk_per_trade:,.0f} risk per position)...")
        placed = enter_this_week_paper_book(target_risk_per_trade=args.risk_per_trade)
        print(f"\nSuccessfully entered {len(placed)} paper positions into the simulator!\n")
        print(render_paper_book_table(placed))

    elif args.command == 'paper-book':
        positions = get_active_paper_positions()
        print(render_paper_book_table(positions))

    elif args.command == 'notify-discord':
        webhook_url = args.webhook_url or get_discord_webhook_url()
        print(f"Preparing Discord digest notification...")
        if not webhook_url:
            print("[INFO] No DISCORD_WEBHOOK_URL found in .env or arguments.")
            print("Set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... in cipher-system/app/.env or pass --webhook-url")
            print("\nPreviewing formatted Discord payload:")

        if args.type in ('preview', 'all'):
            res1 = notify_discord_weekly_preview(webhook_url=webhook_url)
            print(f"Weekly Preview Notification: {res1.get('status')}")
            if 'payload' in res1 and not webhook_url:
                print(json.dumps(res1['payload'], indent=2))

        if args.type in ('portfolio', 'all'):
            res2 = notify_discord_paper_book(webhook_url=webhook_url)
            print(f"Paper Portfolio Notification: {res2.get('status')}")
            if 'payload' in res2 and not webhook_url:
                print(json.dumps(res2['payload'], indent=2))


if __name__ == '__main__':
    main()
