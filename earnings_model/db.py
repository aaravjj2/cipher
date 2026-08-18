import sqlite3
import os
import datetime
from .config import DB_PATH, DATA_DIR


def init_db(db_path=None):
    if db_path is None:
        db_path = DB_PATH
        
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS earnings_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        earnings_date TEXT NOT NULL,
        fiscal_quarter TEXT,
        timing TEXT,
        eps_estimate REAL,
        eps_actual REAL,
        eps_surprise_pct REAL,
        revenue REAL,
        revenue_estimate REAL,
        net_income REAL,
        diluted_eps REAL,
        cap_tier TEXT,
        fetched_at TEXT NOT NULL,
        UNIQUE(symbol, earnings_date)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS price_impact (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        earnings_date TEXT NOT NULL,
        pre_close REAL,
        pre_5d_return_pct REAL,
        pre_20d_return_pct REAL,
        post_open REAL,
        post_close REAL,
        post_high REAL,
        post_low REAL,
        post_volume REAL,
        gap_pct REAL,
        day1_return_pct REAL,
        day1_range_pct REAL,
        day5_close REAL,
        day5_return_pct REAL,
        day10_close REAL,
        day10_return_pct REAL,
        day20_close REAL,
        day20_return_pct REAL,
        avg_volume_20d REAL,
        volume_ratio REAL,
        fetched_at TEXT NOT NULL,
        UNIQUE(symbol, earnings_date)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS earnings_news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        earnings_date TEXT NOT NULL,
        news_id INTEGER,
        headline TEXT NOT NULL,
        summary TEXT,
        created_at TEXT NOT NULL,
        timing_rel TEXT NOT NULL,          -- 'pre' or 'post'
        sentiment_score REAL NOT NULL,     -- [-1.0, 1.0]
        sentiment_label TEXT NOT NULL,     -- 'positive', 'neutral', 'negative'
        uncertainty_ratio REAL,
        source TEXT,
        url TEXT,
        fetched_at TEXT NOT NULL,
        UNIQUE(symbol, news_id, earnings_date)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS earnings_news_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        earnings_date TEXT NOT NULL,
        pre_news_count INTEGER DEFAULT 0,
        pre_news_sentiment_avg REAL,
        pre_news_pos_ratio REAL,
        pre_news_neg_ratio REAL,
        pre_news_unc_ratio REAL,
        post_news_count INTEGER DEFAULT 0,
        post_news_sentiment_avg REAL,
        post_news_pos_ratio REAL,
        post_news_neg_ratio REAL,
        post_news_unc_ratio REAL,
        sentiment_shift REAL,              -- post_avg - pre_avg
        fetched_at TEXT NOT NULL,
        UNIQUE(symbol, earnings_date)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS fetch_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        fetch_type TEXT NOT NULL,
        status TEXT NOT NULL,
        events_count INTEGER DEFAULT 0,
        error_message TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT
    )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_earnings_symbol ON earnings_events(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings_events(earnings_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_impact_symbol ON price_impact(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_impact_date ON price_impact(earnings_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_symbol ON earnings_news(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_date ON earnings_news(earnings_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_metrics_symbol ON earnings_news_metrics(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fetch_log_symbol ON fetch_log(symbol)')
    
    conn.commit()
    return conn


def upsert_earnings_event(conn, event_dict):
    keys = list(event_dict.keys())
    placeholders = ','.join(['?'] * len(keys))
    cols = ','.join(keys)
    
    sql = f'''
    INSERT OR REPLACE INTO earnings_events ({cols})
    VALUES ({placeholders})
    '''
    
    conn.cursor().execute(sql, list(event_dict.values()))
    conn.commit()


def upsert_price_impact(conn, impact_dict):
    keys = list(impact_dict.keys())
    placeholders = ','.join(['?'] * len(keys))
    cols = ','.join(keys)
    
    sql = f'''
    INSERT OR REPLACE INTO price_impact ({cols})
    VALUES ({placeholders})
    '''
    
    conn.cursor().execute(sql, list(impact_dict.values()))
    conn.commit()


def upsert_earnings_news(conn, news_dict):
    keys = list(news_dict.keys())
    placeholders = ','.join(['?'] * len(keys))
    cols = ','.join(keys)
    
    sql = f'''
    INSERT OR REPLACE INTO earnings_news ({cols})
    VALUES ({placeholders})
    '''
    
    conn.cursor().execute(sql, list(news_dict.values()))
    conn.commit()


def upsert_news_metrics(conn, metrics_dict):
    keys = list(metrics_dict.keys())
    placeholders = ','.join(['?'] * len(keys))
    cols = ','.join(keys)
    
    sql = f'''
    INSERT OR REPLACE INTO earnings_news_metrics ({cols})
    VALUES ({placeholders})
    '''
    
    conn.cursor().execute(sql, list(metrics_dict.values()))
    conn.commit()


def log_fetch(conn, symbol, fetch_type, status, events_count=0, error_message=None):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sql = '''
    INSERT INTO fetch_log (symbol, fetch_type, status, events_count, error_message, started_at, completed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    '''
    conn.cursor().execute(sql, (symbol, fetch_type, status, events_count, error_message, now, now))
    conn.commit()


def get_earnings_for_symbol(conn, symbol):
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM earnings_events WHERE symbol = ? ORDER BY earnings_date DESC', (symbol,))
    return [dict(row) for row in cursor.fetchall()]


def get_all_earnings(conn, min_date=None, max_date=None):
    cursor = conn.cursor()
    query = 'SELECT * FROM earnings_events WHERE 1=1'
    params = []
    if min_date:
        query += ' AND earnings_date >= ?'
        params.append(min_date)
    if max_date:
        query += ' AND earnings_date <= ?'
        params.append(max_date)
    query += ' ORDER BY earnings_date DESC'
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def get_price_impact(conn, symbol=None, min_date=None):
    cursor = conn.cursor()
    query = 'SELECT * FROM price_impact WHERE 1=1'
    params = []
    if symbol:
        query += ' AND symbol = ?'
        params.append(symbol)
    if min_date:
        query += ' AND earnings_date >= ?'
        params.append(min_date)
    query += ' ORDER BY earnings_date DESC'
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def get_news_for_event(conn, symbol, earnings_date):
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM earnings_news WHERE symbol = ? AND earnings_date = ? ORDER BY created_at ASC', (symbol, earnings_date))
    return [dict(row) for row in cursor.fetchall()]


def get_news_metrics(conn, symbol=None):
    cursor = conn.cursor()
    query = 'SELECT * FROM earnings_news_metrics WHERE 1=1'
    params = []
    if symbol:
        query += ' AND symbol = ?'
        params.append(symbol)
    query += ' ORDER BY earnings_date DESC'
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def get_fetch_status(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT symbol, fetch_type, status, MAX(completed_at) as last_fetch FROM fetch_log GROUP BY symbol, fetch_type')
    return [dict(row) for row in cursor.fetchall()]


def get_symbols_needing_fetch(conn, all_symbols, fetch_type='earnings'):
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT symbol FROM fetch_log WHERE status = 'success' AND fetch_type = ?", (fetch_type,))
    fetched = set(row['symbol'] for row in cursor.fetchall())
    return sorted(list(set(all_symbols) - fetched))
