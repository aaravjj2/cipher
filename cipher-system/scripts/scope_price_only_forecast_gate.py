#!/usr/bin/env python3
"""Count price-only forecast windows without changing the full market-data gate."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CATALOG = ROOT / "data" / "market_catalog.duckdb"
OUT_DIR = ROOT / "data" / "market_quality"
MINIMUM_SESSIONS = 52  # 32 input closes plus 20 realized closes.


def scan_price_only_stretches(catalog: Path = CATALOG) -> list[dict]:
    """Return complete-session, no-split-like-close-jump stretches by ticker.

    A missing ticker session starts a new stretch. Volume is intentionally not
    read as an eligibility condition; callers must honor the output's
    price-forecast-only allowed use.
    """
    import duckdb

    query = """
        with regular as (
          select ticker, date(timezone('America/New_York', timestamp)) as trading_date,
                 timezone('America/New_York', timestamp) as local_timestamp, close
          from cipher_market.ohlcv_1m
          where cast(timezone('America/New_York', timestamp) as time)
                between time '09:30:00' and time '16:00:00'
        ), numbered as (
          select *, row_number() over (
            partition by ticker, trading_date order by local_timestamp desc
          ) as closing_row
          from regular
        ), daily as (
          select ticker, trading_date, count(*) as bars,
                 max(close) filter (where closing_row = 1) as close
          from numbered group by ticker, trading_date
        ), calendar_raw as (
          select trading_date, lag(trading_date) over (order by trading_date) as prior_trading_date
          from (select distinct trading_date from daily)
        ), calendar as (
          select trading_date,
                 dense_rank() over (order by trading_date) as session_number,
                 sum(case when prior_trading_date is null or trading_date - prior_trading_date > 4
                          then 1 else 0 end)
                   over (order by trading_date rows unbounded preceding) as calendar_block
          from calendar_raw
        ), sequenced as (
          select d.*, c.session_number, c.calendar_block,
                 lag(c.session_number) over (partition by d.ticker order by d.trading_date) as prior_session_number,
                 lag(c.calendar_block) over (partition by d.ticker order by d.trading_date) as prior_calendar_block,
                 lag(d.close) over (partition by d.ticker order by d.trading_date) as prior_close
          from daily d join calendar c using (trading_date)
        ), eligible as (
          select *, case
            when bars != 391 then 0
            when prior_session_number is null or calendar_block != prior_calendar_block
              or session_number != prior_session_number + 1 then 1
            when prior_close <= 0 then 0
            when close / prior_close <= 0.5 or close / prior_close >= 2.0 then 0
            else 1 end as price_only_eligible,
            case when prior_session_number is null or calendar_block != prior_calendar_block
              or session_number != prior_session_number + 1
              then 1 else 0 end as starts_new_stretch
          from sequenced
        ), marked as (
          select *, sum(case when price_only_eligible = 0 or starts_new_stretch = 1 then 1 else 0 end)
              over (partition by ticker order by trading_date rows unbounded preceding) as stretch_id
          from eligible
        ), grouped as (
          select * from marked
          where price_only_eligible = 1
        )
        select ticker, min(trading_date) as start_date, max(trading_date) as end_date,
               count(*) as sessions
        from grouped
        group by ticker, stretch_id
        having count(*) >= ?
        order by sessions desc, ticker, start_date
    """
    with duckdb.connect(str(catalog), read_only=True) as db:
        rows = db.execute(query, [MINIMUM_SESSIONS]).fetchall()
    return [
        {"ticker": ticker, "start": start.isoformat(), "end": end.isoformat(), "sessions": int(sessions)}
        for ticker, start, end, sessions in rows
    ]


def main() -> int:
    if not CATALOG.is_file():
        raise FileNotFoundError(CATALOG)
    stretches = scan_price_only_stretches()
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "catalog": str(CATALOG),
        "gate": {
            "session_completeness": "exactly 391 NY regular-session minute bars",
            "price_continuity": "daily close ratio strictly between 0.5 and 2.0 when prior session is present",
            "volume_reconciliation": "not evaluated",
            "allowed_use": "price_forecast_research_only_no_volume_features",
        },
        "minimum_sessions": MINIMUM_SESSIONS,
        "stretch_count": len(stretches),
        "ticker_count": len({row["ticker"] for row in stretches}),
        "stretches": stretches,
        "full_gate_changed": False,
        "live_execution": False,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"price_only_forecast_scope_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(out), "stretch_count": payload["stretch_count"], "ticker_count": payload["ticker_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
