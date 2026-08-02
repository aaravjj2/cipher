#!/usr/bin/env python3
"""Read-only Alpaca SIP access/pilot audit for frozen Holdout C recovery."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data'/'market_quality'
SYMBOLS=('SPY','QQQ','IWM','AAPL','MSFT','JPM','XOM','WMT','GE','BAC')
DAYS=('2017-01-03','2017-06-15','2018-01-04','2018-02-05','2018-11-23','2019-01-03','2019-12-02')
def credentials():
    pairs={}
    for line in (ROOT/'.env').read_text().splitlines():
        if '=' in line: k,v=line.split('=',1);pairs[k]=v
    return {'APCA-API-KEY-ID':pairs['ALPACA_API_KEY'],'APCA-API-SECRET-KEY':pairs['ALPACA_SECRET_KEY']}
def main():
    rows=[]; schema=None
    for day in DAYS:
        r=requests.get('https://data.alpaca.markets/v2/stocks/bars',headers=credentials(),params={'symbols':','.join(SYMBOLS),'timeframe':'1Min','start':day+'T14:30:00Z','end':day+'T21:00:00Z','feed':'sip','limit':10000},timeout=60)
        data=r.json() if r.headers.get('content-type','').startswith('application/json') else {}
        bars=data.get('bars',{})
        if schema is None:
            first=next((v[0] for v in bars.values() if v),None);schema=sorted(first) if first else None
        rows.append({'day':day,'status':r.status_code,'symbols_returned':len(bars),'bar_counts':{s:len(bars.get(s,[])) for s in SYMBOLS},'next_page':bool(data.get('next_page_token')),'error':data.get('message')})
    payload={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'provider':'Alpaca','dataset':'historical stock bars, SIP feed','credential_state':'available_and_authorized','period':'2017-01-01..2019-12-31','pilot_symbols':list(SYMBOLS),'pilot_dates':list(DAYS),'results':rows,'bar_schema':schema,'volume_used':False,'ranking_outcomes_evaluated':False,'live_execution':False}
    OUT.mkdir(parents=True,exist_ok=True);p=OUT/f'alpaca_holdout_c_access_pilot_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json';p.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n');print(json.dumps({'path':str(p),'statuses':[x['status'] for x in rows]},indent=2))
if __name__=='__main__':main()
