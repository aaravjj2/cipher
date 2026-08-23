"""Paper Portfolio & Position Simulator for Earnings Events.

Simulates defined-risk options strategies (Debit Spreads, Iron Condors, Butterflies)
for upcoming earnings announcements without routing broker orders.

All positions are logged persistently in SQLite with exact strike legs, entry debit/credit,
max gain, max risk, and settlement tracking.
"""
import os
import json
import sqlite3
from datetime import datetime, date, timedelta, timezone
from typing import Dict, Any, List, Optional
import yfinance as yf

from .config import DATA_DIR
from .model import predict_for_symbol

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
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_event ON paper_positions(symbol, report_date);")
    columns = {row[1] for row in c.execute("PRAGMA table_info(paper_positions)")}
    for name in ('model_version', 'validation_status'):
        if name not in columns:
            c.execute(f"ALTER TABLE paper_positions ADD COLUMN {name} TEXT")
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
    if val >= 500:
        base = 10.0
    elif val >= 200:
        base = 5.0
    elif val <= 50:
        base = 1.0
    return round(round(val / base) * base, 2)


def generate_optimal_paper_setup(
    symbol: str,
    spot: float,
    report_date: str,
    target_risk: float = 2000.0,
    prediction: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a defined-risk setup only after the model clears its holdout gate."""
    prediction = prediction or predict_for_symbol(symbol)
    if prediction.get('error') or not prediction.get('strategy_eligible'):
        return {
            'symbol': symbol.upper(),
            'report_date': report_date,
            'skip_reason': prediction.get('error') or prediction.get(
                'validation_status', 'UNVALIDATED_MODEL'
            ),
            'model_version': prediction.get('model_version', 'legacy-unversioned'),
            'validation_status': prediction.get('validation_status', 'UNVALIDATED_MODEL'),
        }

    exp_gap = max(1.5, abs(float(prediction['expected_gap_pct'])))
    direction = prediction['direction']

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

    # Only directional debit spreads are eligible. Short-volatility structures
    # require captured implied moves and are not inferred from underlying gaps.
    if direction == 'BULLISH':
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
        notes = f"Holdout-gated bullish paper cohort; expected underlying gap {exp_gap:.1f}%."

    elif direction == 'BEARISH':
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
        notes = f"Holdout-gated bearish paper cohort; expected underlying gap {exp_gap:.1f}%."

    else:
        return {
            'symbol': symbol.upper(), 'report_date': report_date,
            'skip_reason': 'NO_VALIDATED_DIRECTION',
            'model_version': prediction['model_version'],
            'validation_status': prediction['validation_status'],
        }

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
        'notes': notes,
        'model_version': prediction['model_version'],
        'validation_status': prediction['validation_status'],
    }


def execute_paper_order(conn: sqlite3.Connection, setup: Dict[str, Any]) -> int:
    """Record a paper options position into the persistent database."""
    c = conn.cursor()
    now_str = datetime.now(timezone.utc).isoformat()

    c.execute("""
        INSERT INTO paper_positions (
            symbol, strategy_type, report_date, entry_date, expiry_date,
            spot_at_entry, legs_json, contracts, unit_debit, total_cost,
            max_gain, max_loss, status, notes, created_at, model_version,
            validation_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
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
        now_str,
        setup.get('model_version'),
        setup.get('validation_status'),
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
            h = t.history(period='2mo', auto_adjust=False)
            if h.empty:
                continue
            spot = float(h['Close'].iloc[-1])

            closes = h['Close'].dropna()
            drift = {}
            if len(closes) >= 6:
                drift['pre_5d_return_pct'] = (spot / float(closes.iloc[-6]) - 1.0) * 100.0
            if len(closes) >= 21:
                drift['pre_20d_return_pct'] = (spot / float(closes.iloc[-21]) - 1.0) * 100.0
            prediction = predict_for_symbol(sym, feature_overrides=drift)
            setup = generate_optimal_paper_setup(
                sym, spot, rep_date, target_risk=target_risk_per_trade,
                prediction=prediction,
            )
            if setup.get('skip_reason'):
                print(f"Skipping {sym} {rep_date}: {setup['skip_reason']}")
                continue
            order_id = execute_paper_order(conn, setup)
            setup['id'] = order_id
            placed_orders.append(setup)
        except Exception as e:
            print(f"Error placing paper order for {sym}: {e}")

    conn.close()
    return placed_orders


def get_active_paper_positions(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Fetch all open paper positions."""
    own_connection = conn is None
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
    if own_connection:
        conn.close()
    return positions


def get_paper_scorecard(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Return a compact outcome summary without treating open trades as wins."""
    own_connection = conn is None
    if conn is None:
        conn = init_paper_db()
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open, "
        "SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) AS settled, "
        "SUM(CASE WHEN status='SETTLED' AND realized_pnl > 0 THEN 1 ELSE 0 END) AS wins, "
        "SUM(CASE WHEN status='SETTLED' THEN realized_pnl ELSE 0 END) AS realized_pnl "
        "FROM paper_positions"
    ).fetchone()
    result = dict(row)
    result.update({key: int(result[key] or 0) for key in ('total', 'open', 'settled', 'wins')})
    result['realized_pnl'] = round(float(result['realized_pnl'] or 0.0), 2)
    result['win_rate_pct'] = (
        round(result['wins'] / result['settled'] * 100.0, 2) if result['settled'] else None
    )
    result['cohorts'] = [dict(row) for row in conn.execute(
        "SELECT COALESCE(model_version, 'legacy-unversioned') AS model_version, "
        "COALESCE(validation_status, 'LEGACY_ESTIMATED_ENTRY') AS validation_status, "
        "COUNT(*) AS total, SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open, "
        "SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) AS settled, "
        "SUM(CASE WHEN status='SETTLED' AND realized_pnl > 0 THEN 1 ELSE 0 END) AS wins, "
        "SUM(CASE WHEN status='SETTLED' THEN realized_pnl ELSE 0 END) AS realized_pnl "
        "FROM paper_positions GROUP BY 1, 2 ORDER BY 1, 2"
    )]
    if own_connection:
        conn.close()
    return result


def _expiry_close(symbol: str, expiry_date: str) -> float:
    """Load the last underlying close on or before expiry."""
    expiry = date.fromisoformat(expiry_date)
    history = yf.Ticker(symbol).history(
        start=(expiry - timedelta(days=7)).isoformat(),
        end=(expiry + timedelta(days=1)).isoformat(),
        auto_adjust=False,
    )
    if history.empty or "Close" not in history or history["Close"].dropna().empty:
        raise ValueError(f"No expiry close available for {symbol} on {expiry_date}")
    return float(history["Close"].dropna().iloc[-1])


def _settlement_pnl(position: Dict[str, Any], settle_spot: float) -> float:
    """Value the stored defined-risk legs at intrinsic value on expiry."""
    payoff = 0.0
    for leg in json.loads(position["legs_json"]):
        intrinsic = (
            max(0.0, settle_spot - float(leg["strike"]))
            if leg["type"] == "CALL"
            else max(0.0, float(leg["strike"]) - settle_spot)
        )
        payoff += intrinsic if leg["action"] == "BUY" else -intrinsic
    initial_cashflow = -float(position["unit_debit"])
    return round((initial_cashflow + payoff) * 100 * int(position["contracts"]), 2)


def settle_expired_positions(
    *,
    as_of: Optional[date] = None,
    db_path: Optional[str] = None,
    close_loader=None,
) -> Dict[str, Any]:
    """Settle past-expiry paper positions; unresolved prices remain OPEN."""
    as_of = as_of or date.today()
    close_loader = close_loader or _expiry_close
    conn = init_paper_db(db_path)
    rows = conn.execute(
        "SELECT * FROM paper_positions WHERE status = 'OPEN' AND expiry_date < ? "
        "ORDER BY expiry_date, symbol",
        (as_of.isoformat(),),
    ).fetchall()
    settled, errors = [], []
    for row in rows:
        position = dict(row)
        try:
            spot = round(float(close_loader(position["symbol"], position["expiry_date"])), 4)
            pnl = _settlement_pnl(position, spot)
            risk = float(position["max_loss"])
            pnl_pct = round(pnl / risk * 100.0, 2) if risk else None
            settled_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE paper_positions SET status='SETTLED', settle_spot=?, "
                "realized_pnl=?, realized_pnl_pct=?, settled_at=? WHERE id=?",
                (spot, pnl, pnl_pct, settled_at, position["id"]),
            )
            settled.append({"id": position["id"], "symbol": position["symbol"],
                            "settle_spot": spot, "realized_pnl": pnl,
                            "realized_pnl_pct": pnl_pct})
        except Exception as exc:
            errors.append({"id": position["id"], "symbol": position["symbol"], "error": str(exc)})
    conn.commit()
    conn.close()
    return {"eligible": len(rows), "settled": settled, "errors": errors}


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
