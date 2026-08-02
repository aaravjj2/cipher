#!/usr/bin/env python3
"""
Historical data fetcher for backtesting.

Fetches maximum historical OHLCV data from Alpaca and stores locally
in SQLite for deep backtesting (1-2 years).

Usage:
    python3 cipher-system/core/data_fetcher.py --tickers SPY,QQQ,IWM --days 365
    python3 cipher-system/core/data_fetcher.py --all --days 730
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add core to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    import requests
except ImportError:
    print("requests not installed. pip install requests")
    sys.exit(1)


# Load credentials
def load_env():
    """Load credentials from .env file."""
    env_path = Path(__file__).parent.parent / "app" / ".env"
    creds = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    creds[key.strip()] = val.strip()
    # Also check environment
    for key in ["ALPACA_ALGO_PLUS_KEY", "ALPACA_ALGO_PLUS_SECRET", "ALPACA_ALGO_KEY", "ALPACA_ALGO_SECRET"]:
        if key in os.environ:
            creds[key] = os.environ[key]
    return creds


def get_alpaca_token(creds):
    """Get Alpaca API credentials."""
    key = creds.get("ALPACA_ALGO_PLUS_KEY") or creds.get("ALPACA_ALGO_KEY")
    secret = creds.get("ALPACA_ALGO_PLUS_SECRET") or creds.get("ALPACA_ALGO_SECRET")
    if not key or not secret:
        raise ValueError("Missing Alpaca credentials")
    return key, secret


def fetch_alpaca_bars(symbol, start_date, end_date, timeframe="1Day", creds=None):
    """Fetch historical bars from Alpaca.
    
    Alpaca free tier: ~1-2 years of daily data.
    """
    if creds is None:
        creds = load_env()
    
    key, secret = get_alpaca_token(creds)
    
    base_url = "https://data.alpaca.markets/v2/stocks/bars"
    
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }
    
    all_bars = []
    page_token = None
    
    while True:
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start_date.isoformat() + "Z",
            "end": end_date.isoformat() + "Z",
            "limit": 10000,
            "adjustment": "raw",
        }
        
        if page_token:
            params["page_token"] = page_token
        
        try:
            resp = requests.get(base_url, headers=headers, params=params, timeout=30)
            
            if resp.status_code == 429:
                # Rate limited - wait and retry
                print(f"  Rate limited, waiting 5s...")
                time.sleep(5)
                continue
            
            if resp.status_code != 200:
                print(f"  Error {resp.status_code}: {resp.text[:200]}")
                break
            
            data = resp.json()
            bars = data.get("bars", {}).get(symbol, [])
            
            if not bars:
                break
            
            for bar in bars:
                all_bars.append({
                    "t": bar.get("t", ""),  # timestamp
                    "o": bar.get("o", 0),   # open
                    "h": bar.get("h", 0),   # high
                    "l": bar.get("l", 0),   # low
                    "c": bar.get("c", 0),   # close
                    "v": bar.get("v", 0),   # volume
                    "vw": bar.get("vw", 0), # vwap
                    "n": bar.get("n", 0),   # number of trades
                })
            
            # Check for more pages
            next_token = data.get("next_page_token")
            if not next_token or next_token == page_token:
                break
            page_token = next_token
            
        except Exception as e:
            print(f"  Exception: {e}")
            break
    
    return all_bars


def init_db(db_path):
    """Initialize SQLite database for historical data."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_bars (
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            vwap REAL,
            trades INTEGER,
            PRIMARY KEY (symbol, timestamp)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON historical_bars(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON historical_bars(timestamp)")
    conn.commit()
    return conn


def save_bars(conn, symbol, bars):
    """Save bars to database."""
    if not bars:
        return 0
    
    # Delete existing bars for this symbol
    conn.execute("DELETE FROM historical_bars WHERE symbol = ?", (symbol,))
    
    # Insert new bars
    rows = []
    for bar in bars:
        rows.append((
            symbol,
            bar.get("t", ""),
            bar.get("o", 0),
            bar.get("h", 0),
            bar.get("l", 0),
            bar.get("c", 0),
            bar.get("v", 0),
            bar.get("vw", 0),
            bar.get("n", 0),
        ))
    
    conn.executemany("""
        INSERT INTO historical_bars (symbol, timestamp, open, high, low, close, volume, vwap, trades)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    return len(rows)


def load_bars(conn, symbol, start_date=None, end_date=None):
    """Load bars from database."""
    query = "SELECT * FROM historical_bars WHERE symbol = ?"
    params = [symbol]
    
    if start_date:
        query += " AND timestamp >= ?"
        params.append(start_date)
    if end_date:
        query += " AND timestamp <= ?"
        params.append(end_date)
    
    query += " ORDER BY timestamp"
    
    rows = conn.execute(query, params).fetchall()
    
    bars = []
    for row in rows:
        bars.append({
            "time": row[1],
            "open": row[2],
            "high": row[3],
            "low": row[4],
            "close": row[5],
            "volume": row[6],
            "vwap": row[7],
            "trades": row[8],
        })
    
    return bars


def fetch_and_store(tickers, days=365, db_path=None, timeframe="1Day"):
    """Fetch historical data for tickers and store locally."""
    if db_path is None:
        db_path = Path(__file__).parent.parent / "data" / "historical_bars.sqlite"
    
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = init_db(str(db_path))
    creds = load_env()
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    print(f"Fetching {days} days of {timeframe} data for {len(tickers)} tickers")
    print(f"Date range: {start_date.date()} to {end_date.date()}")
    print()
    
    total_bars = 0
    
    for i, symbol in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] Fetching {symbol}...", end=" ")
        
        bars = fetch_alpaca_bars(symbol, start_date, end_date, timeframe=timeframe, creds=creds)
        
        if bars:
            count = save_bars(conn, symbol, bars)
            total_bars += count
            print(f"{count} bars")
        else:
            print("no data")
        
        # Rate limit - be nice to Alpaca
        if i % 5 == 0:
            time.sleep(1)
    
    conn.close()
    
    print()
    print(f"Total: {total_bars} bars stored in {db_path}")
    
    return total_bars


def main():
    parser = argparse.ArgumentParser(description="Fetch historical market data")
    parser.add_argument("--tickers", "-t", type=str, help="Comma-separated tickers")
    parser.add_argument("--all", "-a", action="store_true", help="Fetch all default tickers")
    parser.add_argument("--days", "-d", type=int, default=365, help="Days of history (default: 365)")
    parser.add_argument("--db", type=str, help="Database path")
    parser.add_argument("--timeframe", "-tf", type=str, default="1Day", 
                        help="Bar timeframe: 1Min, 5Min, 15Min, 1Hour, 1Day (default: 1Day)")
    
    args = parser.parse_args()
    
    if args.all:
        tickers = [
            "SPY", "QQQ", "IWM", "DIA",
            "NVDA", "AAPL", "AMD", "TSLA", "META", "AMZN", "MSFT", "GOOGL",
            "NFLX", "CRM", "PLTR", "SOFI", "COIN", "MARA", "RIOT",
            "XOM", "JPM", "GS", "BA", "CAT",
        ]
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        tickers = ["SPY", "QQQ", "IWM"]
    
    fetch_and_store(tickers, args.days, args.db, args.timeframe)


if __name__ == "__main__":
    main()
