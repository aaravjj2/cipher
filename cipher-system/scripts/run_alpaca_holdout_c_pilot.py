#!/usr/bin/env python3
"""Frozen, read-only Alpaca SIP continuity pilot; no ranking outcomes are read."""
from __future__ import annotations
import argparse, json, time
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
import pandas as pd
import requests
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data'/'market_quality'/'alpaca_holdout_c_pilot'
SYMBOLS=('SPY','QQQ','IWM','AAPL','MSFT','JPM','XOM','WMT','GE','BAC')
START,END=date(2019,1,1),date(2019,3,31)
def headers():
    values=dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines() if '=' in line)
    return {'APCA-API-KEY-ID':values['ALPACA_API_KEY'],'APCA-API-SECRET-KEY':values['ALPACA_SECRET_KEY']}
def trading_days(start_date: date, end_date: date) -> list[dict]:
    """Use Alpaca's exchange calendar; a price-only regular session is 09:30-16:00 NY.

    This intentionally excludes early closes from the 391-bar criterion.  The
    production price-only gate remains unchanged and is never used for volume
    research.
    """
    r = requests.get(
        'https://paper-api.alpaca.markets/v2/calendar', headers=headers(),
        params={'start': start_date.isoformat(), 'end': end_date.isoformat()}, timeout=60,
    )
    r.raise_for_status()
    sessions = []
    for row in r.json():
        day = date.fromisoformat(row['date'])
        is_regular = row['open'] == '09:30' and row['close'] == '16:00'
        if is_regular:
            sessions.append({'day': day, 'calendar_open': row['open'], 'calendar_close': row['close']})
    return sessions

def ny_utc(day: date, local_time: str) -> str:
    from zoneinfo import ZoneInfo
    hour, minute = map(int, local_time.split(':'))
    return datetime.combine(day, clock_time(hour, minute), ZoneInfo('America/New_York')).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--start',default=START.isoformat())
    parser.add_argument('--end',default=END.isoformat())
    parser.add_argument('--symbols', default=','.join(SYMBOLS),
                        help='Fixed comma-separated pilot set selected without outcomes.')
    args=parser.parse_args()
    start_date,end_date=date.fromisoformat(args.start),date.fromisoformat(args.end)
    symbols=tuple(symbol.strip().upper() for symbol in args.symbols.split(',') if symbol.strip())
    OUT.mkdir(parents=True,exist_ok=True); rows=[]; days=[]
    calendar_days = trading_days(start_date, end_date)
    for i, session in enumerate(calendar_days):
        day = session['day']
        start, end = ny_utc(day, session['calendar_open']), ny_utc(day, session['calendar_close'])
        r=requests.get('https://data.alpaca.markets/v2/stocks/bars',headers=headers(),params={'symbols':','.join(symbols),'timeframe':'1Min','start':start,'end':end,'feed':'sip','limit':10000},timeout=60)
        data=r.json() if r.headers.get('content-type','').startswith('application/json') else {}
        for symbol,bars in data.get('bars',{}).items():
            for bar in bars: rows.append({'timestamp':bar['t'],'ticker':symbol,'open':bar['o'],'high':bar['h'],'low':bar['l'],'close':bar['c'],'volume':bar['v'],'trade_count':bar.get('n'),'vwap':bar.get('vw'),'provider':'alpaca_sip','source_day':day.isoformat()})
        counts={s:len(data.get('bars',{}).get(s,[])) for s in symbols}
        days.append({'day':day.isoformat(),'status':r.status_code,'counts':counts,'next_page':bool(data.get('next_page_token')),
                     'calendar_open':session['calendar_open'],'calendar_close':session['calendar_close'],
                     'request_start_utc':start,'request_end_utc':end})
        time.sleep(.31)
    frame=pd.DataFrame(rows); bad=[]
    if not frame.empty:
        bad=frame[(frame.low>frame.open)|(frame.open>frame.high)|(frame.low>frame.close)|(frame.close>frame.high)|(frame[['open','high','low','close']]<=0).any(axis=1)].index.tolist()
    complete={s:sum(1 for d in days if d['counts'][s]==391) for s in symbols}
    normalized=OUT/f'normalized_{start_date}_{end_date}_{"-".join(symbols)}.parquet'
    if not frame.empty: frame.to_parquet(normalized,index=False)
    payload={'schema_version':2,'created_at':datetime.now(timezone.utc).isoformat(),'provider':'Alpaca SIP','period':f'{start_date}..{end_date}','symbols':list(symbols),'selection_basis':'pre-outcome liquidity and sector/corporate-action representation only','source_requests':days,'regular_sessions_requested':len(calendar_days),'calendar_endpoint':'https://paper-api.alpaca.markets/v2/calendar','complete_session_counts':complete,'ohlc_integrity_failures':len(bad),'normalized_path':str(normalized),'ranking_outcomes_evaluated':False,'volume_used':False,'live_execution':False}
    p=OUT/f'pilot_report_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json';p.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n');print(json.dumps({'path':str(p),'complete_session_counts':complete,'ohlc_integrity_failures':len(bad)},indent=2))
if __name__=='__main__':main()
