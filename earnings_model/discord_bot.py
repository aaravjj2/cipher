"""Discord Webhook & Bot Notification Service for Cipher Earnings Model.

Delivers rich formatted Discord embeds and digests for:
  1. Upcoming Earnings Previews & Catalysts
  2. Active Paper Options Book & Sizing
  3. Pre-Report Trade Cards & Tension Alerts
  4. Post-Report Settlement & Scorecards

Reads webhook from DISCORD_WEBHOOK_URL or DISCORD_PROGRESS_WEBHOOK in .env or environment.
"""
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from pathlib import Path

from .config import DATA_DIR
from .paper_portfolio import get_active_paper_positions
from .reaction_predictor import predict_stock_reaction


def get_discord_webhook_url() -> Optional[str]:
    """Retrieve configured Discord webhook URL from environment or .env files."""
    # Check OS environment first
    for key in ['DISCORD_WEBHOOK_URL', 'DISCORD_PROGRESS_WEBHOOK', 'DISCORD_WEBHOOK']:
        val = os.environ.get(key)
        if val and val.strip().startswith('http'):
            return val.strip()

    # Check local .env files
    env_paths = [
        Path('/home/aarav/Aarav/cipher/cipher-system/app/.env'),
        Path('/home/aarav/Aarav/cipher/earnings_model/.env'),
        Path('/home/aarav/Aarav/cipher/.env')
    ]
    for p in env_paths:
        if p.is_file():
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        if k.strip() in ['DISCORD_WEBHOOK_URL', 'DISCORD_PROGRESS_WEBHOOK', 'DISCORD_WEBHOOK']:
                            val = v.strip().strip("'").strip('"')
                            if val.startswith('http'):
                                return val
            except Exception:
                pass
    return None


def send_discord_payload(payload: Dict[str, Any], webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """Send JSON payload to Discord webhook via standard library urllib."""
    url = webhook_url or get_discord_webhook_url()
    if not url:
        return {
            'status': 'skipped',
            'reason': 'No DISCORD_WEBHOOK_URL configured in .env or environment',
            'payload': payload
        }

    try:
        req_data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'Cipher-Earnings-Bot/1.0'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            status_code = response.getcode()
            if status_code in (200, 204):
                return {'status': 'delivered', 'code': status_code}
            return {'status': 'warning', 'code': status_code}
    except urllib.error.HTTPError as e:
        return {'status': 'error', 'code': e.code, 'reason': e.read().decode('utf-8')}
    except Exception as e:
        return {'status': 'error', 'reason': str(e)}


def build_paper_portfolio_embed(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a rich Discord embed for active paper options positions."""
    total_risk = sum(p['total_cost'] for p in positions)
    total_gain = sum(p['max_gain'] for p in positions)

    fields = []
    for p in positions:
        legs = p['legs']
        if p['strategy_type'] == 'Iron Condor':
            strikes = f"P {legs[0]['strike']}/{legs[1]['strike']} - C {legs[2]['strike']}/{legs[3]['strike']}"
        elif 'Spread' in p['strategy_type']:
            strikes = f"{legs[0]['strike']}/{legs[1]['strike']} {legs[0]['type']}"
        else:
            strikes = "ATM Straddle"

        val_str = (
            f"**Strategy**: `{p['strategy_type']}`\n"
            f"**Strikes**: `{strikes}` ({p['contracts']} cts)\n"
            f"**Max Risk**: `${p['total_cost']:,.0f}` | **Max Gain**: `${p['max_gain']:,.0f}`\n"
            f"*{p.get('notes', '')}*"
        )
        fields.append({
            'name': f"📈 {p['symbol']} — Reports {p['report_date']}",
            'value': val_str,
            'inline': False
        })

    embed = {
        'title': '⚡ Cipher Earnings Model — Active Paper Portfolio',
        'description': (
            f"**Total Positions**: `{len(positions)}`\n"
            f"**Total Capital Risked**: `${total_risk:,.2f}`\n"
            f"**Max Potential Gain**: `${total_gain:,.2f}`\n"
            f"**Expiry Target**: `2026-08-21` (This Friday)\n"
            f"*(Read-only paper simulation — no live broker orders)*"
        ),
        'color': 0x00FF88, # Emerald green
        'fields': fields[:25], # Discord max 25 fields
        'footer': {
            'text': f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} • Cipher System"
        }
    }
    return {'embeds': [embed]}


def build_this_week_preview_embed(schedule_predictions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a rich Discord embed summarizing this week's earnings radar."""
    fields = []
    for item in schedule_predictions:
        f = item['fundamental']
        m = item['market']
        t = item['tension']

        state_badge = "🟢" if "OVERSOLD" in t['state'] else ("🔴" if "OVERHEATED" in t['state'] else "⚪")

        val_str = (
            f"**Consensus EPS**: `${item['consensus_eps']}` | **Model P(Beat)**: `{f['beat_probability_pct']}%`\n"
            f"**Exp. Surprise**: `{f['expected_eps_surprise_pct']:+.1f}%` | **Tension**: {state_badge} `{t['state'][:18]}`\n"
            f"**20D Pre-Drift**: `{t['pre_20d_drift_pct']:+.1f}%` | **Exp. Gap**: `{m['expected_opening_gap_pct']:+.1f}%`\n"
            f"**Reversal Risk**: `{m['gap_reversal_risk_pct']}%`"
        )
        fields.append({
            'name': f"{state_badge} {item['symbol']} ({item['date']})",
            'value': val_str,
            'inline': True
        })

    embed = {
        'title': '🎯 This Week Earnings Radar & Forecast Scorecard',
        'description': (
            f"Quantitative forecasts for **{len(schedule_predictions)} equities** reporting August 18–20, 2026.\n"
            f"Tension States: 🟢 Oversold Relief Candidate | 🔴 Overheated Sell-the-News | ⚪ Balanced Baseline"
        ),
        'color': 0x3498DB, # Blue
        'fields': fields[:25],
        'footer': {
            'text': f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} • Cipher Research"
        }
    }
    return {'embeds': [embed]}


def notify_discord_paper_book(webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """Send active paper options book to Discord."""
    positions = get_active_paper_positions()
    if not positions:
        return {'status': 'empty', 'message': 'No active paper positions to send'}

    payload = build_paper_portfolio_embed(positions)
    return send_discord_payload(payload, webhook_url=webhook_url)


def notify_discord_weekly_preview(webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """Send this week's earnings radar digest to Discord."""
    week_schedule = [
        ('HD', '2026-08-18', 4.73),
        ('BIDU', '2026-08-18', 9.84),
        ('TGT', '2026-08-19', 2.33),
        ('LOW', '2026-08-19', 4.23),
        ('TJX', '2026-08-19', 1.19),
        ('ADI', '2026-08-19', 3.33),
        ('EL', '2026-08-19', 0.32),
        ('NDSN', '2026-08-19', 3.09),
        ('WMT', '2026-08-20', 0.74),
        ('BABA', '2026-08-20', 10.72),
        ('ROST', '2026-08-20', 1.93),
        ('DE', '2026-08-20', 4.67)
    ]

    preds = []
    for sym, rep_date, est_eps in week_schedule:
        try:
            res = predict_stock_reaction(sym)
            if 'error' in res:
                continue
            preds.append({
                'symbol': sym,
                'date': rep_date,
                'consensus_eps': est_eps,
                'fundamental': res['fundamental_forecast'],
                'market': res['market_reaction_forecast'],
                'tension': res['expectation_tension']
            })
        except Exception:
            pass

    payload = build_this_week_preview_embed(preds)
    return send_discord_payload(payload, webhook_url=webhook_url)


def notify_discord_trade_alert(symbol: str, webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """Send an individual stock reaction forecast card to Discord."""
    res = predict_stock_reaction(symbol)
    if 'error' in res:
        return {'status': 'error', 'reason': res['error']}

    f = res['fundamental_forecast']
    m = res['market_reaction_forecast']
    t = res['expectation_tension']
    a = res['ticker_archetype']

    state_badge = "🟢" if "OVERSOLD" in t['state'] else ("🔴" if "OVERHEATED" in t['state'] else "⚪")

    embed = {
        'title': f"🎯 Earnings Catalyst Card: {symbol.upper()} {state_badge}",
        'description': f"**Market State**: `{t['state']}`\n**20D Pre-Drift**: `{t['pre_20d_drift_pct']:+.2f}%` (Tension vs Baseline: `{t['tension_vs_hist_20d_pct']:+.2f}%`)",
        'color': 0x00FF88 if "OVERSOLD" in t['state'] else (0xFF4444 if "OVERHEATED" in t['state'] else 0x3498DB),
        'fields': [
            {
                'name': '1. Fundamental Forecast',
                'value': (
                    f"**Beat Probability**: `{f['beat_probability_pct']}%`\n"
                    f"**Exp. EPS Surprise**: `{f['expected_eps_surprise_pct']:+.2f}%`\n"
                    f"**Streak**: `{f['current_beat_streak']} beats in a row`"
                ),
                'inline': True
            },
            {
                'name': '2. Market Reaction Target',
                'value': (
                    f"**Exp. Opening Gap**: `{m['expected_opening_gap_pct']:+.2f}%`\n"
                    f"**Gap-Up Prob**: `{m['opening_gap_up_probability_pct']}%`\n"
                    f"**Day-5 Bias**: `{m['day5_continuation_bias']}`"
                ),
                'inline': True
            },
            {
                'name': '3. Historical Reaction Archetype',
                'value': (
                    f"**Average Gap**: `{a['avg_historical_gap_pct']:.2f}%`\n"
                    f"**Gap-Fade Tendency**: `{a['historical_gap_fade_rate_pct']}%`\n"
                    f"**Reversal Risk**: `{m['gap_reversal_risk_pct']}%`"
                ),
                'inline': False
            }
        ],
        'footer': {
            'text': f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} • Cipher Earnings Terminal"
        }
    }
    return send_discord_payload({'embeds': [embed]}, webhook_url=webhook_url)
