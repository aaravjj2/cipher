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
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pathlib import Path

from .paper_portfolio import get_active_paper_positions, get_paper_scorecard
from .reaction_predictor import predict_stock_reaction
from .scanner import find_upcoming_earnings

DISCORD_EMBED_CHAR_LIMIT = 6000


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


def _embed_chars(embed: Dict[str, Any]) -> int:
    return sum(len(str(value)) for value in (
        embed.get('title', ''), embed.get('description', ''),
        embed.get('footer', {}).get('text', ''),
        *(part for field in embed.get('fields', []) for part in (field.get('name', ''), field.get('value', ''))),
    ))


def build_paper_portfolio_embed(
    positions: List[Dict[str, Any]], scorecard: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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

        notes = str(p.get('notes') or '')[:400]
        val_str = (
            f"**Strategy**: `{p['strategy_type']}`\n"
            f"**Strikes**: `{strikes}` ({p['contracts']} cts)\n"
            f"**Max Risk**: `${p['total_cost']:,.0f}` | **Max Gain**: `${p['max_gain']:,.0f}`\n"
            f"*{notes}*"
        )
        fields.append({
            'name': f"📈 {p['symbol']} — Reports {p['report_date']}",
            'value': val_str,
            'inline': False
        })

    expiries = sorted({str(p['expiry_date']) for p in positions})
    expiry_text = expiries[0] if len(expiries) == 1 else f"{expiries[0]} to {expiries[-1]}"
    scorecard = scorecard or {}
    settled = int(scorecard.get('settled') or 0)
    realized_pnl = float(scorecard.get('realized_pnl') or 0)
    realized_pnl_text = f"{'-' if realized_pnl < 0 else '+'}${abs(realized_pnl):,.2f}"
    cohorts = scorecard.get('cohorts') or []
    cohort_text = ", ".join(
        f"{row.get('model_version')}: {int(row.get('settled') or 0)} settled"
        for row in cohorts
    ) or "none"
    score_text = (
        f"**Settled Scorecard**: `{scorecard.get('wins', 0)}/{settled} wins`"
        f" | **Estimated P&L**: `{realized_pnl_text}`\n"
        if settled else "**Settled Scorecard**: `No settled observations yet`\n"
    )
    embed = {
        'title': '⚡ Cipher Earnings Model — Active Paper Portfolio',
        'description': (
            f"**Total Positions**: `{len(positions)}`\n"
            f"**Total Capital Risked**: `${total_risk:,.2f}`\n"
            f"**Max Potential Gain**: `${total_gain:,.2f}`\n"
            f"**Expiry Range**: `{expiry_text}`\n"
            f"{score_text}"
            f"**Model Cohorts**: `{cohort_text}`\n"
            "*Settlement uses underlying intrinsic value and estimated entry prices; it is not captured option-quote P&L.*\n"
            f"*(Read-only paper simulation — no live broker orders)*"
        ),
        'color': 0x00FF88, # Emerald green
        'fields': fields,
        'footer': {
            'text': f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} • Cipher System"
        }
    }
    return {'embeds': [embed]}


def build_paper_portfolio_payloads(
    positions: List[Dict[str, Any]], scorecard: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Split the complete paper book into Discord-compliant messages."""
    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for position in positions:
        candidate = current + [position]
        if current and (
            len(candidate) > 25
            or _embed_chars(build_paper_portfolio_embed(candidate, scorecard)['embeds'][0]) > DISCORD_EMBED_CHAR_LIMIT - 16
        ):
            batches.append(current)
            current = [position]
        else:
            current = candidate
    if current:
        batches.append(current)
    payloads = [build_paper_portfolio_embed(batch, scorecard) for batch in batches]
    for index, payload in enumerate(payloads, 1):
        if len(payloads) > 1:
            payload['embeds'][0]['title'] += f" ({index}/{len(payloads)})"
    return payloads


def build_this_week_preview_embed(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a rich Discord embed summarizing this week's earnings radar."""
    fields = []
    for item in cards:
        bias = str(item.get('direction_bias') or 'NEUTRAL')
        state_badge = "🟢" if bias.startswith('BULLISH') else ("🔴" if bias.startswith('BEARISH') else "⚪")
        eps = item.get('eps_estimate_avg')
        val_str = (
            f"**Consensus EPS**: `{eps if eps is not None else 'unknown'}` | **Historical Beat**: `{float(item.get('hist_beat_rate') or 0) * 100:.1f}%`\n"
            f"**Model Bias**: `{bias}` | **Confidence**: `{float(item.get('confidence') or 0) * 100:.1f}%`\n"
            f"**Current 20D Drift**: `{float(item.get('pre_drift_20d') or 0):+.1f}%` | **Expected Gap**: `±{float(item.get('expected_gap_pct') or 0):.1f}%`\n"
            f"**Paper Structure**: `{item.get('recommended_strategy') or 'research only'}`"
        )
        fields.append({
            'name': f"{state_badge} {item['symbol']} ({item['scheduled_date']})",
            'value': val_str,
            'inline': True
        })

    dates = sorted({str(item['scheduled_date']) for item in cards})
    window = f"{dates[0]} to {dates[-1]}" if dates else "No scheduled reports"
    embed = {
        'title': '🎯 This Week Earnings Radar & Forecast Scorecard',
        'description': (
            f"Research forecasts for **{len(cards)} equities** reporting {window}; "
            f"showing **{len(fields[:25])}** in this message.\n"
            "Paper-only and unvalidated for live options P&L; dates are provider estimates."
        ),
        'color': 0x3498DB, # Blue
        'fields': fields[:25],
        'footer': {
            'text': f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} • Cipher Research"
        }
    }
    while embed['fields'] and _embed_chars(embed) > DISCORD_EMBED_CHAR_LIMIT:
        embed['fields'].pop()
    return {'embeds': [embed]}


def notify_discord_paper_book(webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """Send active paper options book to Discord."""
    positions = get_active_paper_positions()
    if not positions:
        return {'status': 'empty', 'message': 'No active paper positions to send'}

    payloads = build_paper_portfolio_payloads(positions, get_paper_scorecard())
    deliveries = [send_discord_payload(payload, webhook_url=webhook_url) for payload in payloads]
    statuses = {result.get('status') for result in deliveries}
    status = 'error' if 'error' in statuses else 'warning' if 'warning' in statuses else 'skipped' if statuses == {'skipped'} else 'delivered'
    return {'status': status, 'messages': len(payloads), 'deliveries': deliveries,
            **({'payloads': payloads} if status == 'skipped' else {})}


def notify_discord_weekly_preview(webhook_url: Optional[str] = None) -> Dict[str, Any]:
    """Send this week's earnings radar digest to Discord."""
    payload = build_this_week_preview_embed(find_upcoming_earnings(days_ahead=7))
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
            'text': f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} • Cipher Earnings Terminal"
        }
    }
    return send_discord_payload({'embeds': [embed]}, webhook_url=webhook_url)
