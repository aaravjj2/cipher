"""Upcoming Earnings Radar & Strategy Trade Card Scanner.

Scans the optionable universe for equities reporting earnings in the upcoming
1 to 4 weeks, analyzes consensus estimates, pre-earnings drift, and news tone,
and generates actionable trade setups using the trained ML forecasting models.
"""
import json
import time
import urllib.parse
import urllib.request

import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import logging
import math
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime as dt, timedelta, date

from .config import DB_PATH
from .db import init_db, get_earnings_for_symbol
from .universe import load_universe
from .model import predict_for_symbol
from .collector import is_etf

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Secondary earnings-calendar source (read-only market metadata) ---
#
# yfinance earnings dates are provider estimates; the radar flags them
# single_source_unconfirmed. Nasdaq's public calendar is consulted ONLY as a
# second opinion: any failure (offline, blocked UA, malformed payload) degrades
# to "second source unavailable" and never crashes or delays the pipeline.
NASDAQ_CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_REQUEST_TIMEOUT_SECONDS = 8.0
NASDAQ_CACHE_TTL_SECONDS = 6 * 3600        # successful day maps are stable intraday
NASDAQ_FAILURE_TTL_SECONDS = 600           # retry outages/blocks after 10 minutes

# Module-level TTL cache keyed by ISO day -> (monotonic_stamp, mapping|None).
# A None value means the source was unreachable for that day and is cached only
# briefly so an outage does not pin unavailability for the full success TTL.
_nasdaq_day_cache: Dict[str, Tuple[float, Optional[Dict[str, str]]]] = {}


def current_price_drift(ticker) -> Dict[str, float]:
    """Return current 5/20-session close drift when enough bars exist."""
    history = ticker.history(period="2mo", auto_adjust=False)
    if history.empty or "Close" not in history:
        return {}
    closes = history["Close"].dropna()
    if len(closes) < 6:
        return {}
    closes = pd.to_numeric(closes, errors='coerce')
    if not all(math.isfinite(float(value)) and value > 0 for value in closes):
        return {}
    latest = float(closes.iloc[-1])
    drift = {"pre_5d_return_pct": (latest / float(closes.iloc[-6]) - 1.0) * 100.0}
    if len(closes) >= 21:
        drift["pre_20d_return_pct"] = (latest / float(closes.iloc[-21]) - 1.0) * 100.0
    return drift


def _parse_nasdaq_calendar_payload(payload: Any) -> Dict[str, str]:
    """Extract {symbol: session_text} rows from a Nasdaq calendar response."""
    rows: Dict[str, str] = {}
    data = payload.get("data") if isinstance(payload, dict) else None
    for row in ((data or {}).get("rows") if isinstance(data, dict) else None) or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            rows[symbol] = str(row.get("time") or "")
    return rows


def _fetch_nasdaq_day_map(day: date) -> Optional[Dict[str, str]]:
    """Read-only fetch of Nasdaq's public earnings calendar for one day.

    Returns None (never raises) when the endpoint is unreachable, blocked, or
    malformed — market metadata must not break the radar pipeline.
    """
    url = f"{NASDAQ_CALENDAR_URL}?{urllib.parse.urlencode({'date': day.isoformat()})}"
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) CipherEarningsRadar/1.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=NASDAQ_REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return _parse_nasdaq_calendar_payload(payload)
    except Exception as exc:
        logging.debug(f"Nasdaq earnings calendar unavailable for {day.isoformat()}: {exc}")
        return None


def _nasdaq_earnings_for_day(day: date) -> Optional[Dict[str, str]]:
    """TTL-cached `_fetch_nasdaq_day_map`; failures are cached briefly."""
    key = day.isoformat()
    now_mono = time.monotonic()
    cached = _nasdaq_day_cache.get(key)
    if cached is not None:
        stamped, value = cached
        ttl = NASDAQ_FAILURE_TTL_SECONDS if value is None else NASDAQ_CACHE_TTL_SECONDS
        if now_mono - stamped <= ttl:
            return value
    value = _fetch_nasdaq_day_map(day)
    _nasdaq_day_cache[key] = (now_mono, value)
    return value


def _shift_trading_days(day: date, offset: int) -> date:
    """Move `offset` trading days from `day`, skipping weekends.

    Exchange holidays are deliberately ignored: they can only shift a match by
    one more day into the same ±1-trading-day tolerance window.
    """
    step = timedelta(days=1 if offset >= 0 else -1)
    moved = day
    for _ in range(abs(offset)):
        moved += step
        while moved.weekday() >= 5:
            moved += step
    return moved


def cross_check_earnings_date(symbol: str, yahoo_date: date) -> Dict[str, Any]:
    """Cross-check a yfinance report date against Nasdaq's public calendar.

    Agreement within ±1 trading day confirms the date; disagreement or an
    unavailable second source keeps the caller's single-source flag intact.
    Both raw values travel on the trade card so nothing silently overrides.
    """
    candidates = {
        -1: _shift_trading_days(yahoo_date, -1),
        0: yahoo_date,
        1: _shift_trading_days(yahoo_date, 1),
    }
    day_maps = {offset: _nasdaq_earnings_for_day(day) for offset, day in candidates.items()}
    unavailable = {
        "confirmation_status": "SECOND_SOURCE_UNAVAILABLE", "confirmed": False,
        "nasdaq_date": None,
    }
    if all(day_map is None for day_map in day_maps.values()):
        return unavailable

    ticker = str(symbol).upper()
    for offset in (0, -1, 1):  # exact match wins over adjacent-day tolerance
        day_map = day_maps[offset]
        if day_map and ticker in day_map:
            matched_iso = candidates[offset].isoformat()
            if offset == 0:
                status = "CONFIRMED_EXACT"
            else:
                direction = "PRIOR" if offset < 0 else "NEXT"
                status = f"CONFIRMED_ADJACENT_{direction}_TRADING_DAY"
            return {
                "confirmation_status": status, "confirmed": True,
                "nasdaq_date": matched_iso,
            }

    # Source answered but the symbol is absent across the whole tolerance
    # window: treat as a disagreement, never as confirmation.
    return {
        "confirmation_status": "DISAGREED_WITHIN_ONE_TRADING_DAY", "confirmed": False,
        "nasdaq_date": None,
    }


def find_upcoming_earnings(
    days_ahead: int = 14,
    tiers: Optional[List[str]] = None,
    symbols: Optional[List[str]] = None,
    conn=None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Scan the universe for companies with scheduled earnings announcements."""
    own_connection = conn is None
    if conn is None:
        conn = init_db()

    if symbols is None:
        symbols = load_universe(tiers)

    equities = [s for s in symbols if not is_etf(s)]
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update(symbols_requested=len(equities), calendars_received=0, errors=[])

    today = date.today()
    target_end = today + timedelta(days=days_ahead)

    upcoming_cards = []
    logging.info(f"Scanning {len(equities)} equities for earnings between {today} and {target_end}...")

    for sym in equities:
        try:
            t = yf.Ticker(sym)
            cal = t.get_calendar()
            diagnostics['calendars_received'] += 1
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
            try:
                drift = current_price_drift(t)
            except Exception as exc:
                drift = {}
                diagnostics['errors'].append({'symbol': sym, 'stage': 'price_history', 'error': type(exc).__name__})
            try:
                pred = predict_for_symbol(sym, conn=conn, feature_overrides=drift)
            except Exception as exc:
                pred = {'error': f'Model inference unavailable ({type(exc).__name__})'}
            if pred.get('error'):
                diagnostics['errors'].append({'symbol': sym, 'stage': 'prediction', 'error': str(pred['error'])[:200]})
                pred = {'forecast_status': 'UNAVAILABLE', 'strategy_eligible': False,
                        'primary_strategy': 'NO TRADE — forecast unavailable', 'rationale': str(pred['error'])[:200]}
            elif not all(key in drift for key in ('pre_5d_return_pct', 'pre_20d_return_pct')):
                pred = {**pred, 'strategy_eligible': False, 'forecast_status': 'DEGRADED_INPUTS',
                        'primary_strategy': 'NO TRADE — current price history unavailable',
                        'rationale': 'Raw model output may use historical-event drift; current 5D/20D inputs are incomplete.'}

            # Historical beat rate from DB
            past_events = get_earnings_for_symbol(conn, sym)
            if past_events:
                valid_beats = [e for e in past_events if e['eps_actual'] is not None and e['eps_estimate'] is not None]
                beat_count = sum(1 for e in valid_beats if e['eps_actual'] > e['eps_estimate'])
                hist_beat_rate = round(beat_count / len(valid_beats), 3) if valid_beats else None
                total_hist_reports = len(valid_beats)
            else:
                hist_beat_rate = None
                total_hist_reports = 0

            days_to_report = (matched_date - today).days

            # Second-opinion date check; failure keeps the unconfirmed flag.
            crosscheck = cross_check_earnings_date(sym, matched_date)
            if crosscheck["confirmed"]:
                confirmation = f"CONFIRMED_YAHOO_NASDAQ_{crosscheck['confirmation_status']}"
                sources = ["yahoo_finance", "nasdaq"]
            else:
                confirmation = "single_source_unconfirmed"
                sources = ["yahoo_finance"]

            upcoming_cards.append({
                'symbol': sym,
                'scheduled_date': matched_date.strftime('%Y-%m-%d'),
                'earnings_date_sources': sources,
                'earnings_date_confirmation': confirmation,
                'yahoo_earnings_date': matched_date.strftime('%Y-%m-%d'),
                'nasdaq_earnings_date': crosscheck.get('nasdaq_date'),
                'earnings_date_crosscheck': crosscheck['confirmation_status'],
                'days_until': days_to_report,
                'eps_estimate_avg': eps_avg,
                'eps_estimate_range': f"${eps_low} - ${eps_high}" if (eps_low and eps_high) else "N/A",
                'hist_beat_rate': hist_beat_rate,
                'total_hist_reports': total_hist_reports,
                'direction_bias': pred.get('direction', 'NEUTRAL'),
                'confidence': pred.get('confidence'),
                'raw_direction': pred.get('raw_direction'),
                'raw_confidence': pred.get('raw_confidence'),
                'prob_day5_up': pred.get('prob_day5_up'),
                'forecast_status': pred.get('forecast_status', 'UNVALIDATED'),
                'feature_method': pred.get('inputs_snapshot', {}).get('feature_method'),
                'model_artifact_sha256': pred.get('model_artifact_sha256'),
                'forward_baseline': pred.get('forward_baseline'),
                'last_mature_report_date': pred.get('inputs_snapshot', {}).get('last_mature_report_date'),
                'strategy_eligible': pred.get('strategy_eligible', False),
                'expected_gap_pct': pred.get('expected_gap_pct'),
                'reversal_risk_pct': pred.get('prob_reversal'),
                'recommended_strategy': pred.get('primary_strategy', 'NO TRADE — forecast unavailable'),
                'rationale': pred.get('rationale', ''),
                'pre_drift_5d': drift.get('pre_5d_return_pct'),
                'pre_drift_20d': drift.get('pre_20d_return_pct'),
                'market_drift_source': 'current' if len(drift) == 2 else 'unavailable',
                'news_sentiment': pred.get('inputs_snapshot', {}).get('pre_news_sentiment', 0.0)
            })

        except Exception as e:
            diagnostics['errors'].append({'symbol': sym, 'stage': 'scan', 'error': type(e).__name__})
            logging.warning('Earnings scan failed for %s (%s)', sym, type(e).__name__)

    # Sort by upcoming date ascending, then confidence descending
    upcoming_cards.sort(key=lambda x: (x['days_until'], -(x['confidence'] or 0)))
    diagnostics['status'] = ('unavailable' if equities and not diagnostics['calendars_received']
                             else 'partial' if diagnostics['errors'] or any(c['forecast_status'] in {'UNAVAILABLE', 'DEGRADED_INPUTS'} for c in upcoming_cards)
                             else 'current')
    if own_connection:
        conn.close()
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
        beat_str = f"{c['hist_beat_rate']*100:.0f}% ({c['total_hist_reports']})" if c['hist_beat_rate'] is not None else 'unknown'
        probability = c.get('prob_day5_up')
        bias_str = f"raw P(up) {probability:.1%}" if probability is not None else "raw unavailable"
        gap_str = f"±{c['expected_gap_pct']:.1f}%" if c['expected_gap_pct'] is not None else 'unknown'
        in_str = f"{c['days_until']}d"

        lines.append(
            f"{c['symbol']:6s} | {c['scheduled_date']:10s} | {in_str:4s} | {est_str:9s} | {beat_str:9s} | "
            f"{bias_str:14s} | {gap_str:7s} | {c['recommended_strategy'][:32]:32s} | {c.get('forecast_status', 'UNVALIDATED')}"
        )

    lines.extend([
        "=======================================================================================================",
        "",
    ])

    return "\n".join(lines)
