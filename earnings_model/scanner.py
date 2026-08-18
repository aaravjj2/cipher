"""Upcoming Earnings Radar & Strategy Trade Card Scanner.

Scans the optionable universe for equities reporting earnings in the upcoming
1 to 4 weeks, analyzes consensus estimates, pre-earnings drift, and news tone,
and generates actionable trade setups using the trained ML forecasting models.
"""
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime as dt, timedelta, date

from .config import DB_PATH
from .db import init_db, get_earnings_for_symbol
from .universe import load_universe
from .model import predict_for_symbol
from .collector import is_etf

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def find_upcoming_earnings(
    days_ahead: int = 14,
    tiers: Optional[List[str]] = None,
    symbols: Optional[List[str]] = None,
    conn=None
) -> List[Dict[str, Any]]:
    """Scan the universe for companies with scheduled earnings announcements."""
    if conn is None:
        conn = init_db()

    if symbols is None:
        symbols = load_universe(tiers)

    equities = [s for s in symbols if not is_etf(s)]

    today = date.today()
    target_end = today + timedelta(days=days_ahead)

    upcoming_cards = []
    logging.info(f"Scanning {len(equities)} equities for earnings between {today} and {target_end}...")

    for sym in equities:
        try:
            t = yf.Ticker(sym)
            cal = t.get_calendar()
            if not cal:
                continue

            raw_dates = cal.get('Earnings Date') or cal.get('EarningsDate') or []
            if not isinstance(raw_dates, (list, tuple)):
                raw_dates = [raw_dates]

            if not raw_dates:
                continue

            # Check if any scheduled date falls in the window
            matched_date = None
            for d in raw_dates:
                if hasattr(d, 'date'):
                    d_val = d.date()
                elif isinstance(d, datetime.datetime):
                    d_val = d.date()
                elif isinstance(d, datetime.date):
                    d_val = d
                else:
                    try:
                        d_val = dt.strptime(str(d)[:10], '%Y-%m-%d').date()
                    except Exception:
                        continue

                if today <= d_val <= target_end:
                    matched_date = d_val
                    break

            if not matched_date:
                continue

            # Found an upcoming earnings event!
            eps_high = cal.get('Earnings High')
            eps_low = cal.get('Earnings Low')
            eps_avg = cal.get('Earnings Average')

            # Run prediction from model
            pred = predict_for_symbol(sym, conn=conn)

            # Historical beat rate from DB
            past_events = get_earnings_for_symbol(conn, sym)
            if past_events:
                valid_beats = [e for e in past_events if e['eps_actual'] is not None and e['eps_estimate'] is not None]
                beat_count = sum(1 for e in valid_beats if e['eps_actual'] > e['eps_estimate'])
                hist_beat_rate = round(beat_count / len(valid_beats), 3) if valid_beats else 0.5
                total_hist_reports = len(valid_beats)
            else:
                hist_beat_rate = 0.5
                total_hist_reports = 0

            days_to_report = (matched_date - today).days

            upcoming_cards.append({
                'symbol': sym,
                'scheduled_date': matched_date.strftime('%Y-%m-%d'),
                'days_until': days_to_report,
                'eps_estimate_avg': eps_avg,
                'eps_estimate_range': f"${eps_low} - ${eps_high}" if (eps_low and eps_high) else "N/A",
                'hist_beat_rate': hist_beat_rate,
                'total_hist_reports': total_hist_reports,
                'direction_bias': pred.get('direction', 'NEUTRAL'),
                'confidence': pred.get('confidence', 0.5),
                'expected_gap_pct': pred.get('expected_gap_pct', 2.0),
                'reversal_risk_pct': pred.get('prob_reversal', 0.25),
                'recommended_strategy': pred.get('primary_strategy', 'Iron Condor'),
                'rationale': pred.get('rationale', ''),
                'pre_drift_5d': pred.get('inputs_snapshot', {}).get('pre_5d_drift_pct', 0.0),
                'news_sentiment': pred.get('inputs_snapshot', {}).get('pre_news_sentiment', 0.0)
            })

        except Exception as e:
            logging.debug(f"Error scanning {sym}: {e}")

    # Sort by upcoming date ascending, then confidence descending
    upcoming_cards.sort(key=lambda x: (x['days_until'], -x['confidence']))
    return upcoming_cards


def render_radar_table(cards: List[Dict[str, Any]]) -> str:
    """Format upcoming earnings trade cards into a clean terminal radar."""
    if not cards:
        return "No upcoming earnings found in the specified window."

    lines = [
        "",
        "=======================================================================================================",
        "                               UPCOMING EARNINGS RADAR & STRATEGY CARDS",
        "=======================================================================================================",
        f"{'SYMBOL':6s} | {'DATE':10s} | {'IN':4s} | {'EST EPS':9s} | {'HIST BEAT':9s} | {'BIAS':14s} | {'EXP GAP':7s} | {'RECOMMENDED STRATEGY':32s}",
        "-------------------------------------------------------------------------------------------------------",
    ]

    for c in cards:
        est_str = f"${c['eps_estimate_avg']:.2f}" if (c['eps_estimate_avg'] is not None and isinstance(c['eps_estimate_avg'], (int, float))) else "N/A"
        beat_str = f"{c['hist_beat_rate']*100:.0f}% ({c['total_hist_reports']})"
        bias_str = f"{c['direction_bias'][:8]} ({c['confidence']*100:.0f}%)"
        gap_str = f"±{c['expected_gap_pct']:.1f}%"
        in_str = f"{c['days_until']}d"

        lines.append(
            f"{c['symbol']:6s} | {c['scheduled_date']:10s} | {in_str:4s} | {est_str:9s} | {beat_str:9s} | "
            f"{bias_str:14s} | {gap_str:7s} | {c['recommended_strategy'][:32]:32s}"
        )

    lines.extend([
        "=======================================================================================================",
        "",
    ])

    return "\n".join(lines)
