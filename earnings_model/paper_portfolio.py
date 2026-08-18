"""Paper Portfolio & Position Simulator for Earnings Events.

Simulates defined-risk options strategies (Debit Spreads, Iron Condors, Butterflies)
for upcoming earnings announcements without routing broker orders.

All positions are logged persistently in SQLite with exact strike legs, entry debit/credit,
max gain, max risk, and settlement tracking.
"""
import os
import json
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
import yfinance as yf

from .config import DATA_DIR
from .reaction_predictor import predict_stock_reaction

PAPER_DB_PATH = os.path.join(DATA_DIR, 'paper_portfolio.sqlite')


def init_paper_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Initialize SQLite table for paper options positions."""
    if db_path is None:
        db_path = PAPER_DB_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            report_date TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            expiry_date TEXT NOT NULL,
            spot_at_entry REAL NOT NULL,
            legs_json TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            unit_debit REAL NOT NULL,        -- Positive for debit paid, negative for credit received
            total_cost REAL NOT NULL,        -- Total capital risked (debit paid or margin collateral)
            max_gain REAL NOT NULL,
            max_loss REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN', -- 'OPEN', 'SETTLED', 'CLOSED'
            settle_spot REAL,
            realized_pnl REAL,
            realized_pnl_pct REAL,
            notes TEXT,
            created_at TEXT NOT NULL,
            settled_at TEXT
        );
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_paper_symbol ON paper_positions(symbol);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_positions(status);")
    conn.commit()
    return conn


def _next_friday(report_date: str) -> str:
    """Nearest Friday on or after the report date (options settle into that week's expiry)."""
    day = date.fromisoformat(report_date)
    days_ahead = (4 - day.weekday()) % 7
    if days_ahead == 0:
        # Reporting on a Friday rolls to the following week's expiry.
        days_ahead = 7
    return (day + timedelta(days=days_ahead)).isoformat()


def upcoming_week_schedule(days_ahead: int = 7, conn=None) -> List[tuple]:
    """Earnings schedule for the window from the live scanner — never hardcoded.

    Returns [(symbol, scheduled_date), ...] sorted by report date.
    """
    from .scanner import find_upcoming_earnings

    seen = set()
    out = []
    for card in find_upcoming_earnings(days_ahead=days_ahead, conn=conn):
        key = (str(card.get("symbol") or "").upper(), card.get("scheduled_date"))
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            out.append(key)
    out.sort(key=lambda item: item[1])
    return out


def round_strike(val: float, base: float = 2.5) -> float:
    """Round a price to the nearest strike increment."""
    if val >= 200:
        base = 5.0
    elif val >= 500:
        base = 10.0
    elif val <= 50:
        base = 1.0
    return round(round(val / base) * base, 2)


def generate_optimal_paper_setup(symbol: str, spot: float, report_date: str, target_risk: float = 2000.0) -> Dict[str, Any]:
    """Generate the optimal defined-risk option setup based on reaction model forecast."""
    res = predict_stock_reaction(symbol)
    if 'error' in res:
        # Fallback to standard neutral iron condor
        exp_gap = 2.5
        t_state = 'BALANCED'
        rev_risk = 25.0
        beat_prob = 75.0
    else:
        f = res['fundamental_forecast']
        m = res['market_reaction_forecast']
        t = res['expectation_tension']
        exp_gap = max(1.5, abs(float(m['expected_opening_gap_pct'])))
        t_state = t['state']
        rev_risk = float(m['gap_reversal_risk_pct'])
        beat_prob = float(f['beat_probability_pct'])

    # Expiry: nearest Friday on or after the report date.
    entry_dt = date.today()
    expiry_str = _next_friday(report_date)

    # Strike width scaling
    if spot >= 500:
        wing_w = 10.0
    elif spot >= 200:
        wing_w = 5.0
    elif spot >= 80:
        wing_w = 2.5
    else:
        wing_w = 1.0

    # Decision Matrix:
    # 1. Oversold relief candidates -> Bull Call Debit Spread
    if 'OVERSOLD' in t_state and beat_prob >= 65.0:
        strategy_type = 'Debit Bull Call Spread'
        strike_long = round_strike(spot, wing_w)
        strike_short = round_strike(spot * (1.0 + max(0.03, exp_gap / 100.0)), wing_w)
        if strike_short <= strike_long:
            strike_short = strike_long + wing_w

        width = strike_short - strike_long
        est_unit_debit = round(width * 0.40, 2) # Typically ~40% of spread width
        est_unit_gain = round(width - est_unit_debit, 2)

        contracts = max(1, int(target_risk / (est_unit_debit * 100)))
        total_risk = round(contracts * est_unit_debit * 100, 2)
        total_max_gain = round(contracts * est_unit_gain * 100, 2)

        legs = [
            {'action': 'BUY', 'type': 'CALL', 'strike': strike_long, 'expiry': expiry_str},
            {'action': 'SELL', 'type': 'CALL', 'strike': strike_short, 'expiry': expiry_str}
        ]
        notes = f"Oversold relief play. Model predicts {beat_prob:.1f}% beat with low priced-in expectations."

    # 2. Overheated candidates -> Bear Put Debit Spread
    elif 'OVERHEATED' in t_state and rev_risk >= 20.0:
        strategy_type = 'Debit Bear Put Spread'
        strike_long = round_strike(spot, wing_w)
        strike_short = round_strike(spot * (1.0 - max(0.03, exp_gap / 100.0)), wing_w)
        if strike_short >= strike_long:
            strike_short = strike_long - wing_w

        width = strike_long - strike_short
        est_unit_debit = round(width * 0.38, 2)
        est_unit_gain = round(width - est_unit_debit, 2)

        contracts = max(1, int(target_risk / (est_unit_debit * 100)))
        total_risk = round(contracts * est_unit_debit * 100, 2)
        total_max_gain = round(contracts * est_unit_gain * 100, 2)

        legs = [
            {'action': 'BUY', 'type': 'PUT', 'strike': strike_long, 'expiry': expiry_str},
            {'action': 'SELL', 'type': 'PUT', 'strike': strike_short, 'expiry': expiry_str}
        ]
        notes = f"Overheated fade play. Pre-drift run-up priced in; vulnerable to sell-the-news gap down."

    # 3. High-conviction beat trenders with balanced drift -> Bull Call Spread
    elif beat_prob >= 85.0 and exp_gap >= 0.5:
        strategy_type = 'Debit Bull Call Spread'
        strike_long = round_strike(spot, wing_w)
        strike_short = round_strike(spot * 1.04, wing_w)
        if strike_short <= strike_long:
            strike_short = strike_long + wing_w

        width = strike_short - strike_long
        est_unit_debit = round(width * 0.42, 2)
        est_unit_gain = round(width - est_unit_debit, 2)

        contracts = max(1, int(target_risk / (est_unit_debit * 100)))
        total_risk = round(contracts * est_unit_debit * 100, 2)
        total_max_gain = round(contracts * est_unit_gain * 100, 2)

        legs = [
            {'action': 'BUY', 'type': 'CALL', 'strike': strike_long, 'expiry': expiry_str},
            {'action': 'SELL', 'type': 'CALL', 'strike': strike_short, 'expiry': expiry_str}
        ]
        notes = f"High-conviction beat ({beat_prob:.1f}%) with expected upward PEAD continuation."

    # 4. Balanced / Steady Volatility Crush -> Iron Condor
    else:
        strategy_type = 'Iron Condor'
        move_buffer = max(0.035, (exp_gap / 100.0) * 1.35)
        short_put = round_strike(spot * (1.0 - move_buffer), wing_w)
        long_put = short_put - wing_w
        short_call = round_strike(spot * (1.0 + move_buffer), wing_w)
        long_call = short_call + wing_w

        width = wing_w
        est_unit_credit = round(width * 0.32, 2)
        est_unit_risk = round(width - est_unit_credit, 2)

        contracts = max(1, int(target_risk / (est_unit_risk * 100)))
        total_risk = round(contracts * est_unit_risk * 100, 2)
        total_max_gain = round(contracts * est_unit_credit * 100, 2)
        est_unit_debit = -est_unit_credit

        legs = [
            {'action': 'BUY', 'type': 'PUT', 'strike': long_put, 'expiry': expiry_str},
            {'action': 'SELL', 'type': 'PUT', 'strike': short_put, 'expiry': expiry_str},
            {'action': 'SELL', 'type': 'CALL', 'strike': short_call, 'expiry': expiry_str},
            {'action': 'BUY', 'type': 'CALL', 'strike': long_call, 'expiry': expiry_str}
        ]
        notes = f"Volatility crush play outside ±{move_buffer*100:.1f}% expected range. Balanced pre-drift."

    return {
        'symbol': symbol.upper(),
        'strategy_type': strategy_type,
        'report_date': report_date,
        'entry_date': entry_dt.strftime('%Y-%m-%d'),
        'expiry_date': expiry_str,
        'spot_at_entry': round(spot, 2),
        'legs': legs,
        'contracts': contracts,
        'unit_debit': est_unit_debit,
        'total_cost': total_risk,
        'max_gain': total_max_gain,
        'max_loss': total_risk,
        'notes': notes
    }


def execute_paper_order(conn: sqlite3.Connection, setup: Dict[str, Any]) -> int:
    """Record a paper options position into the persistent database."""
    c = conn.cursor()
    now_str = datetime.utcnow().isoformat()

    c.execute("""
        INSERT INTO paper_positions (
            symbol, strategy_type, report_date, entry_date, expiry_date,
            spot_at_entry, legs_json, contracts, unit_debit, total_cost,
            max_gain, max_loss, status, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
    """, (
        setup['symbol'],
        setup['strategy_type'],
        setup['report_date'],
        setup['entry_date'],
        setup['expiry_date'],
        setup['spot_at_entry'],
        json.dumps(setup['legs']),
        setup['contracts'],
        setup['unit_debit'],
        setup['total_cost'],
        setup['max_gain'],
        setup['max_loss'],
        setup['notes'],
        now_str
    ))
    conn.commit()
    return c.lastrowid


def enter_this_week_paper_book(
    target_risk_per_trade: float = 2000.0,
    days_ahead: int = 7,
    schedule: Optional[List[tuple]] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Enter paper trades for companies reporting within the window (default next 7 days).

    Idempotent by design: a symbol already entered for the same report date is
    skipped, so repeated scheduled runs never stack duplicate positions and open
    positions are never deleted by a re-run.
    """
    conn = init_paper_db(db_path)
    if schedule is None:
        schedule = upcoming_week_schedule(days_ahead=days_ahead)
    existing = {
        (str(row[0]), str(row[1]))
        for row in conn.execute("select symbol, report_date from paper_positions")
    }

    placed_orders = []

    for sym, rep_date in schedule:
        key = (str(sym).upper(), str(rep_date))
        if key in existing:
            print(f"Skipping {sym} {rep_date}: already entered")
            continue
        try:
            t = yf.Ticker(sym)
            h = t.history(period='5d')
            if h.empty:
                continue
            spot = float(h['Close'].iloc[-1])

            setup = generate_optimal_paper_setup(sym, spot, rep_date, target_risk=target_risk_per_trade)
            order_id = execute_paper_order(conn, setup)
            setup['id'] = order_id
            placed_orders.append(setup)
        except Exception as e:
            print(f"Error placing paper order for {sym}: {e}")

    conn.close()
    return placed_orders


def get_active_paper_positions(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Fetch all open paper positions."""
    if conn is None:
        conn = init_paper_db()
    c = conn.cursor()
    c.execute("SELECT * FROM paper_positions WHERE status = 'OPEN' ORDER BY report_date, symbol")
    rows = c.fetchall()
    positions = []
    for r in rows:
        d = dict(r)
        d['legs'] = json.loads(d['legs_json'])
        positions.append(d)
    return positions


def render_paper_book_table(positions: List[Dict[str, Any]]) -> str:
    """Format active paper portfolio into a terminal table."""
    if not positions:
        return "No active paper positions in portfolio."

    lines = []
    lines.append("=" * 110)
    lines.append("                        CIPHER EARNINGS PAPER OPTIONS PORTFOLIO (THIS WEEK)")
    lines.append("=" * 110)
    lines.append(f"{'ID':2s} | {'SYM':5s} | {'REPORT':10s} | {'STRATEGY':22s} | {'STRIKES / LEGS':30s} | {'CTS':3s} | {'MAX RISK':9s} | {'MAX GAIN':9s}")
    lines.append("-" * 110)

    total_risk = 0.0
    total_gain = 0.0

    for p in positions:
        legs = p['legs']
        if p['strategy_type'] == 'Iron Condor':
            strikes_str = f"P {legs[0]['strike']}/{legs[1]['strike']} - C {legs[2]['strike']}/{legs[3]['strike']}"
        elif 'Spread' in p['strategy_type']:
            strikes_str = f"{legs[0]['strike']}/{legs[1]['strike']} {legs[0]['type']}"
        else:
            strikes_str = f"ATM Straddle"

        lines.append(
            f"{p['id']:2d} | {p['symbol']:5s} | {p['report_date']:10s} | {p['strategy_type']:22s} | {strikes_str:30s} | {p['contracts']:3d} | ${p['total_cost']:8.2f} | ${p['max_gain']:8.2f}"
        )
        total_risk += p['total_cost']
        total_gain += p['max_gain']

    lines.append("-" * 110)
    lines.append(f"TOTAL ACTIVE POSITIONS: {len(positions)} | TOTAL ALLOCATED RISK: ${total_risk:,.2f} | MAX GAIN POTENTIAL: ${total_gain:,.2f}")
    lines.append("=" * 110)
    return "\n".join(lines)
