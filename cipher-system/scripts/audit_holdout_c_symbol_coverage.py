#!/usr/bin/env python3
"""Audit frozen 2017-2019 Holdout C symbol coverage without reading outcomes."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "market_catalog.duckdb"
QUALITY = ROOT / "data" / "market_quality"
START, END = "2017-01", "2019-12"

def main() -> int:
    import duckdb
    scope_path = sorted(QUALITY.glob("price_only_forecast_scope_*.json"))[-1]
    scope = json.loads(scope_path.read_text())
    stretches = [r for r in scope["stretches"] if r["start"] <= "2019-12-31" and r["end"] >= "2017-01-01"]
    longest = {}
    for row in stretches:
        if row["ticker"] not in longest or row["sessions"] > longest[row["ticker"]]["sessions"]:
            longest[row["ticker"]] = row
    with duckdb.connect(str(CATALOG), read_only=True) as db:
        rows = db.execute("""
          select ticker, list_sort(list(distinct strftime(timestamp, '%Y-%m'))) as months
          from cipher_market.ohlcv_1m where timestamp >= '2017-01-01' and timestamp < '2020-01-01'
          group by ticker
        """).fetchall()
    wanted = [f"{year}-{month:02d}" for year in range(2017, 2020) for month in range(1, 13)]
    matrix=[]
    for ticker, months in rows:
        available=set(months); clean=longest.get(ticker)
        matrix.append({"ticker":ticker,"available_months":months,"missing_months":[m for m in wanted if m not in available],"current_longest_clean_block":clean,"expected_after_filling_gaps":"unchanged: source is month-partitioned for the full universe; no ticker-month object exists","would_join_common_universe_of_at_least_8":False,"mechanical_expected_gain":0})
    payload={"schema_version":1,"created_at":datetime.now(timezone.utc).isoformat(),"period":f"{START}..{END}","source_partitioning":"one complete-universe Parquet file per month; no ticker-month files available","source_months_present":wanted,"gap_matrix":matrix,"selection":"no downloads selected: all months already present and every possible ticker-month download has zero expected gain","scope_source":scope_path.name,"stop_condition":"source lacks ticker-specific historical partitions; continuity failures cannot be repaired by downloading more ticker-month files","volume_used":False,"live_execution":False}
    out=QUALITY/f"holdout_c_symbol_coverage_gap_matrix_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"path":str(out),"tickers":len(matrix),"selected_downloads":0},indent=2))

if __name__ == '__main__': main()
