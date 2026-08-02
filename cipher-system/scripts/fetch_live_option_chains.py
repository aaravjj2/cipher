#!/usr/bin/env python3
"""
Fetch live option chains for Cipher's 12 scanner tickers via Alpaca OPRA feed.
Saves to cipher-system/data/live_option_chains/ as daily JSONL files with full
bid/ask/mid/last/vol/OI/IV/Greeks.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add cipher-system/core to path
CORE_DIR = Path(__file__).resolve().parent.parent / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from app import option_chain, local_settings

SCANNER_TICKERS = [
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT",
    "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ"
]

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "live_option_chains"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def fetch_and_save_chain(ticker: str, feed: str = "opra", max_pages: int = 8) -> dict:
    """Fetch option chain and return summary stats."""
    try:
        contracts = option_chain(ticker, feed, force=True, max_pages=max_pages)
        
        # Save as JSONL
        today = datetime.now(timezone.utc).date().isoformat()
        jsonl_path = DATA_DIR / f"{today}_{ticker}.jsonl"
        
        with jsonl_path.open("a", encoding="utf-8") as f:
            for contract in contracts:
                # Add timestamp to each record
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "ticker": ticker,
                    **contract
                }
                f.write(json.dumps(record) + "\n")
        
        # Also save latest snapshot
        latest_path = DATA_DIR / f"latest_{ticker}.json"
        latest_data = {
            "ticker": ticker,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "feed": feed,
            "contract_count": len(contracts),
            "contracts": contracts
        }
        latest_path.write_text(json.dumps(latest_data, indent=2))
        
        # Summary stats
        with_oi = sum(1 for c in contracts if c.get("open_interest") is not None and c.get("open_interest", 0) > 0)
        with_gamma = sum(1 for c in contracts if c.get("gamma") is not None)
        with_iv = sum(1 for c in contracts if c.get("iv") is not None)
        with_greeks = sum(1 for c in contracts if c.get("gamma") is not None or c.get("delta") is not None)
        
        return {
            "ticker": ticker,
            "success": True,
            "contracts": len(contracts),
            "with_oi": with_oi,
            "with_gamma": with_gamma,
            "with_iv": with_iv,
            "with_greeks": with_greeks,
            "feed": feed
        }
    except Exception as e:
        return {
            "ticker": ticker,
            "success": False,
            "error": str(e)
        }

def main():
    print(f"Fetching live option chains for {len(SCANNER_TICKERS)} tickers at {datetime.now(timezone.utc).isoformat()}")
    print(f"Data directory: {DATA_DIR}")
    
    # Check credentials
    try:
        key, secret, options_feed, stock_feed = local_settings()
        print(f"Alpaca feed: options={options_feed}, stocks={stock_feed}")
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    
    results = []
    for i, ticker in enumerate(SCANNER_TICKERS, 1):
        print(f"[{i}/{len(SCANNER_TICKERS)}] Fetching {ticker}...", end=" ", flush=True)
        result = fetch_and_save_chain(ticker, feed="opra", max_pages=8)
        results.append(result)
        if result["success"]:
            print(f"OK ({result['contracts']} contracts, OI:{result['with_oi']}, Gamma:{result['with_gamma']}, IV:{result['with_iv']})")
        else:
            print(f"FAILED: {result['error']}")
        # Small delay to avoid rate limits
        if i < len(SCANNER_TICKERS):
            time.sleep(0.5)
    
    # Save summary
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tickers": SCANNER_TICKERS,
        "results": results,
        "success_count": sum(1 for r in results if r["success"]),
        "total_contracts": sum(r.get("contracts", 0) for r in results if r["success"])
    }
    summary_path = DATA_DIR / f"summary_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nSummary saved to {summary_path}")
    print(f"Successful: {summary['success_count']}/{len(SCANNER_TICKERS)}")
    print(f"Total contracts: {summary['total_contracts']}")

if __name__ == "__main__":
    main()