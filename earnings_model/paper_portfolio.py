"""Paper Portfolio & Position Simulator for Earnings Events.

Simulates defined-risk options strategies (Debit Spreads, Iron Condors, Butterflies)
for upcoming earnings announcements without routing broker orders.

All positions are logged persistently in SQLite with exact strike legs, entry debit/credit,
max gain, max risk, and settlement tracking.
"""
import os
import re
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
import yfinance as yf

from .config import DATA_DIR
from .model import predict_for_symbol

PAPER_DB_PATH = os.path.join(DATA_DIR, 'paper_portfolio.sqlite')

# --- Entry-quote capture (read-only local market metadata) ---
#
# Paper entries must not price risk off a width heuristic when real quotes are
# one read away. The core service on the loopback interface is the ONLY market
# endpoint consulted here; it is strictly read-only and paper stays paper.
CORE_MARKET_DATA_BASE_URL = os.environ.get(
    'CIPHER_CORE_MARKET_DATA_URL', 'http://127.0.0.1:8282'
).rstrip('/')
CORE_REQUEST_TIMEOUT_SECONDS = 8.0
LEG_QUOTE_FRESHNESS_SECONDS = 120   # both legs must be stamped within this window
DEBIT_SOURCE_CAPTURED = 'CAPTURED_QUOTES'
DEBIT_SOURCE_ESTIMATED = 'ESTIMATED_DEBIT'
SKIP_QUOTES_UNAVAILABLE = 'QUOTES_UNAVAILABLE'

# Pre-gating cohort marker: see freeze_legacy_paper_cohort().
LEGACY_FROZEN_VALIDATION_STATUS = 'FROZEN_LEGACY'


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
    for name in ('model_version', 'validation_status', 'debit_source', 'entry_quotes_json'):
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


def _parse_quote_stamp(value: Any) -> Optional[datetime]:
    """Parse an exchange/API quote timestamp into aware UTC (None if unusable).

    Handles Alpaca-style nanosecond ISO stamps; unknown formats stay unusable
    rather than being silently treated as fresh.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('Z', '+00:00')
    match = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$", text)
    if not match:
        return None
    base, frac, tail = match.groups()
    tz_part = tail.strip() or '+00:00'
    if tz_part in ('+0000', '-0000'):
        tz_part = '+00:00'
    normalized = f"{base}.{(frac or '')[:6]:<06s}{tz_part}" if frac else f"{base}{tz_part}"
    try:
        stamp = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _quote_age_seconds(stamp_value: Any, now: Optional[datetime] = None) -> Optional[float]:
    """Age of a quote stamp in seconds, or None when the stamp is unusable."""
    stamp = _parse_quote_stamp(stamp_value)
    if stamp is None:
        return None
    return (now or datetime.now(timezone.utc)).timestamp() - stamp.timestamp()


def _fresh_leg_quote(quote: Any, now: Optional[datetime] = None) -> bool:
    """True only for a two-sided quote stamped within the freshness window.

    Quotes without a parseable timestamp are stale by definition: an entry
    price with unknown provenance is exactly what this gate exists to prevent.
    """
    if not isinstance(quote, dict):
        return False
    try:
        bid, ask = float(quote['bid']), float(quote['ask'])
    except (KeyError, TypeError, ValueError):
        return False
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return False
    age = _quote_age_seconds(quote.get('quote_time') or quote.get('timestamp'), now)
    # Small negative tolerance absorbs minor clock skew between core and host.
    return age is not None and -5.0 <= age <= LEG_QUOTE_FRESHNESS_SECONDS


def fetch_core_chain_contract_rows(
    symbol: str,
    timeout: float = CORE_REQUEST_TIMEOUT_SECONDS,
    base_url: str = CORE_MARKET_DATA_BASE_URL,
) -> Optional[Dict[str, Any]]:
    """GET the local read-only core options-chain view for one underlying.

    Returns None (never raises) when the core is down, slow, or answers with an
    error payload — quote capture must degrade to a refusal, not a crash.
    """
    url = (
        f"{base_url}/api/options-chain?"
        + urllib.parse.urlencode({
            'ticker': str(symbol).upper(), 'feed': 'opra', 'expirations': '12',
        })
    )
    request = urllib.request.Request(url, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[paper-entry] core options-chain unavailable for {symbol}: {exc}")
        return None
    if not isinstance(payload, dict) or payload.get('error'):
        print(f"[paper-entry] core options-chain rejected {symbol}: invalid payload")
        return None
    return payload


def fetch_leg_quotes(
    symbol: str,
    legs: List[Dict[str, Any]],
    chain_loader=None,
    timeout: float = CORE_REQUEST_TIMEOUT_SECONDS,
) -> Optional[List[Optional[Dict[str, Any]]]]:
    """Fetch contemporaneous core quotes for each defined-risk leg.

    Returns a list aligned with `legs`; legs that are missing from the chain or
    lack usable two-sided quotes come back as None so the entry gate can refuse
    instead of guessing. Read-only metadata only — failures never raise.
    """
    loader = chain_loader or (
        lambda sym: fetch_core_chain_contract_rows(sym, timeout=timeout)
    )
    payload = loader(str(symbol).upper())
    if payload is None:
        return None

    wanted = []
    for leg in legs:
        wanted.append((
            str(leg.get('type', '')).lower(),
            float(leg.get('strike')),
            str(leg.get('expiry')),
        ))

    out: List[Optional[Dict[str, Any]]] = []
    for side, strike, expiry in wanted:
        found: Optional[Dict[str, Any]] = None
        for group in payload.get('expirations') or []:
            if str(group.get('expiration')) != expiry:
                continue
            for strike_row in group.get('rows') or []:
                contract = strike_row.get(side) if isinstance(strike_row, dict) else None
                if not isinstance(contract, dict):
                    continue
                try:
                    row_strike = float(contract.get('strike'))
                except (TypeError, ValueError):
                    continue
                if abs(row_strike - strike) < 1e-9:
                    found = {
                        'symbol': contract.get('symbol'),
                        'bid': contract.get('bid'),
                        'ask': contract.get('ask'),
                        'quote_time': contract.get('quote_time') or contract.get('as_of'),
                    }
                    break
            if found:
                break
        out.append(found)
    return out


def captured_spread_debit(
    leg_quotes: Optional[List[Optional[Dict[str, Any]]]],
    width: float,
    now: Optional[datetime] = None,
) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
    """Actual debit = long ask − short bid, but ONLY on fresh two-sided quotes.

    Returns (debit, snapshot) or (None, None). Callers must refuse the entry on
    None rather than silently falling back to the width heuristic.
    """
    if not leg_quotes or len(leg_quotes) != 2:
        return None, None
    long_quote, short_quote = leg_quotes
    if not (_fresh_leg_quote(long_quote, now) and _fresh_leg_quote(short_quote, now)):
        return None, None
    debit = round(float(long_quote['ask']) - float(short_quote['bid']), 2)
    # Crossed or inverted captures cannot define a sane defined-risk entry.
    if debit <= 0.0 or debit > width + 1e-9:
        return None, None
    snapshot = {
        'debit_source': DEBIT_SOURCE_CAPTURED,
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'computed_debit': debit,
        'legs': [
            {
                'action': ('BUY' if index == 0 else 'SELL'),
                'bid': quote.get('bid'), 'ask': quote.get('ask'),
                'quote_time': quote.get('quote_time'), 'symbol': quote.get('symbol'),
            }
            for index, quote in enumerate((long_quote, short_quote))
        ],
    }
    return debit, snapshot


def generate_optimal_paper_setup(
    symbol: str,
    spot: float,
    report_date: str,
    target_risk: float = 2000.0,
    prediction: Optional[Dict[str, Any]] = None,
    *,
    allow_estimated_debit: bool = False,
    quote_fetcher=None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Generate a defined-risk setup only after the model clears its holdout gate.

    Entry pricing is fail-closed: both legs are priced from contemporaneous
    read-only core quotes when available (actual debit = long ask − short bid,
    stored with the quote timestamps). Without fresh quotes the entry is refused
    with QUOTES_UNAVAILABLE unless the caller explicitly passes
    allow_estimated_debit=True, in which case the width heuristic is used and
    clearly labeled ESTIMATED_DEBIT.
    """
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
        est_unit_debit = round(width * 0.40, 2)  # width heuristic prior; only used with explicit opt-in below
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
        est_unit_debit = round(width * 0.38, 2)  # width heuristic prior; only used with explicit opt-in below
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

    # Entry pricing: capture real quotes for BOTH legs, else refuse/opt-in.
    if quote_fetcher is not None:
        leg_quotes = quote_fetcher(symbol.upper(), legs)
    else:
        leg_quotes = fetch_leg_quotes(symbol, legs)
    unit_debit, quote_snapshot = captured_spread_debit(leg_quotes, width=width, now=now)

    if unit_debit is not None:
        debit_source = DEBIT_SOURCE_CAPTURED
        notes += f" Entry debit captured from core quotes (${unit_debit:.2f} = long ask − short bid)."
    elif allow_estimated_debit:
        unit_debit = est_unit_debit
        debit_source = DEBIT_SOURCE_ESTIMATED
        notes += (
            f" ESTIMATED_DEBIT: quotes unavailable/stale; priced at width heuristic"
            f" (${unit_debit:.2f}), NOT a market price."
        )
    else:
        return {
            'symbol': symbol.upper(),
            'report_date': report_date,
            'skip_reason': SKIP_QUOTES_UNAVAILABLE,
            'strategy_type': strategy_type,
            'model_version': prediction['model_version'],
            'validation_status': prediction['validation_status'],
        }

    est_unit_gain = round(width - unit_debit, 2)
    contracts = max(1, int(target_risk / (unit_debit * 100)))
    total_risk = round(contracts * unit_debit * 100, 2)
    total_max_gain = round(contracts * est_unit_gain * 100, 2)

    setup = {
        'symbol': symbol.upper(),
        'strategy_type': strategy_type,
        'report_date': report_date,
        'entry_date': entry_dt.strftime('%Y-%m-%d'),
        'expiry_date': expiry_str,
        'spot_at_entry': round(spot, 2),
        'legs': legs,
        'contracts': contracts,
        'unit_debit': unit_debit,
        'debit_source': debit_source,
        'total_cost': total_risk,
        'max_gain': total_max_gain,
        'max_loss': total_risk,
        'notes': notes,
        'model_version': prediction['model_version'],
        'validation_status': prediction['validation_status'],
    }
    if quote_snapshot is not None:
        setup['entry_quotes'] = quote_snapshot
    return setup


def execute_paper_order(conn: sqlite3.Connection, setup: Dict[str, Any]) -> int:
    """Record a paper options position into the persistent database."""
    c = conn.cursor()
    now_str = datetime.now(timezone.utc).isoformat()
    entry_quotes = setup.get('entry_quotes')
    c.execute("""
        INSERT INTO paper_positions (
            symbol, strategy_type, report_date, entry_date, expiry_date,
            spot_at_entry, legs_json, contracts, unit_debit, total_cost,
            max_gain, max_loss, status, notes, created_at, model_version,
            validation_status, debit_source, entry_quotes_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
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
        setup.get('debit_source'),
        json.dumps(entry_quotes) if entry_quotes is not None else None,
    ))
    conn.commit()
    return c.lastrowid


def enter_this_week_paper_book(
    target_risk_per_trade: float = 2000.0,
    days_ahead: int = 7,
    schedule: Optional[List[tuple]] = None,
    db_path: Optional[str] = None,
    allow_estimated_debit: bool = False,
    quote_fetcher=None,
) -> List[Dict[str, Any]]:
    """Enter paper trades for companies reporting within the window (default next 7 days).

    Idempotent by design: a symbol already entered for the same report date is
    skipped, so repeated scheduled runs never stack duplicate positions and open
    positions are never deleted by a re-run.

    Fail-closed pricing: entries without fresh two-sided core quotes for both
    legs are refused (QUOTES_UNAVAILABLE) unless allow_estimated_debit=True is
    explicitly passed; estimated entries stay labeled ESTIMATED_DEBIT.
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
                allow_estimated_debit=allow_estimated_debit,
                quote_fetcher=quote_fetcher,
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
    # Frozen-legacy visibility: pre-gating positions marked FROZEN_LEGACY stay
    # out of every gated-cohort comparison; surfaced here so digests can state
    # how much of the open book is excluded history rather than live signal.
    result['legacy_frozen_open'] = int(conn.execute(
        "SELECT COUNT(*) FROM paper_positions "
        "WHERE status='OPEN' AND validation_status=?",
        (LEGACY_FROZEN_VALIDATION_STATUS,),
    ).fetchone()[0])
    if own_connection:
        conn.close()
    return result


def freeze_legacy_paper_cohort(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str] = None,
    marker: str = LEGACY_FROZEN_VALIDATION_STATUS,
) -> Dict[str, Any]:
    """Mark remaining OPEN pre-gating positions as a frozen legacy cohort.

    Positions recorded before holdout gating carry no model version and priced
    their entries off a width heuristic (ESTIMATED_DEBIT), so their outcomes are
    not comparable with gated cohorts and must never blend into new-cohort
    statistics. Marking is one-way and conservative: only rows that are still
    OPEN with BOTH model_version and validation_status NULL are touched;
    settled history and any explicitly tagged row are left as-is.
    """
    own_connection = conn is None
    if conn is None:
        conn = init_paper_db(db_path)
    before = conn.execute(
        "SELECT symbol FROM paper_positions WHERE status='OPEN' AND model_version IS NULL"
    ).fetchall()
    cur = conn.execute(
        "UPDATE paper_positions SET validation_status=? "
        "WHERE status='OPEN' AND model_version IS NULL AND validation_status IS NULL",
        (marker,),
    )
    conn.commit()
    result = {
        'marked': cur.rowcount,
        'symbols': sorted({str(row[0]) for row in before}),
        'marker': marker,
    }
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
