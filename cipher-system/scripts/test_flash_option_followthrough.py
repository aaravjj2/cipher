#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from historical_options_download import alpaca_credentials

RAW = ROOT / "data/browser_ingest/raw_windows/device-windows/uploaded"
UTC = timezone.utc


def dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC)


def load_episodes() -> list[dict]:
    rows=[]
    for p in RAW.glob("*.json"):
        try:
            x=json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        scan=str(x.get("scan_type") or "").lower()
        if scan not in {"flash","flash_agentic"}: continue
        t=dt(x["captured_at"])
        for c in x.get("cards") or []:
            ticker=str(c.get("ticker") or "").upper()
            if not ticker or ticker=="TEST": continue
            rows.append({"scan":scan,"t":t,"ticker":ticker,"direction":str(c.get("direction") or "").lower(),"setup":str(c.get("setup_type") or "").strip().lower(),"spot":float(c.get("spot") or 0)})
    rows.sort(key=lambda r:r["t"])
    grouped=defaultdict(list)
    for r in rows: grouped[(r["scan"],r["ticker"],r["direction"],r["setup"])].append(r)
    eps=[]
    for key, arr in grouped.items():
        cur=[]
        for r in arr:
            if cur and r["t"]-cur[-1]["t"]>timedelta(minutes=10):
                eps.append({**cur[0],"polls":len(cur)})
                cur=[]
            cur.append(r)
        if cur: eps.append({**cur[0],"polls":len(cur)})
    return sorted(eps,key=lambda r:r["t"])


def get(url: str, params: dict, key: str, secret: str) -> dict:
    req=urllib.request.Request(url+"?"+urllib.parse.urlencode(params),headers={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":secret})
    with urllib.request.urlopen(req,timeout=60) as r:
        return json.load(r)


def contracts_for(tickers: list[str], key: str, secret: str) -> dict[str,list[dict]]:
    out=defaultdict(list)
    for ticker in tickers:
        params={"underlying_symbols":ticker,"status":"active","expiration_date_gte":"2026-07-28","expiration_date_lte":"2026-08-07","limit":1000}
        token=None
        while True:
            if token: params["page_token"]=token
            x=get("https://paper-api.alpaca.markets/v2/options/contracts",params,key,secret)
            for c in x.get("option_contracts") or []: out[ticker].append(c)
            token=x.get("next_page_token")
            if not token: break
    return out


def choose(ep: dict, contracts: list[dict]) -> dict|None:
    want="call" if ep["direction"]=="bullish" else "put"
    candidates=[c for c in contracts if str(c.get("type") or "").lower()==want]
    if not candidates: return None
    expiries=sorted({c.get("expiration_date") for c in candidates if c.get("expiration_date")})
    if not expiries: return None
    expiry=expiries[0]
    candidates=[c for c in candidates if c.get("expiration_date")==expiry]
    return min(candidates,key=lambda c:abs(float(c.get("strike_price") or 0)-ep["spot"]))


def fetch_bars(symbols: list[str], start: datetime, end: datetime, key: str, secret: str) -> dict[str,list[dict]]:
    out=defaultdict(list)
    for i in range(0,len(symbols),100):
        chunk=symbols[i:i+100]
        params={"symbols":",".join(chunk),"timeframe":"1Min","start":start.isoformat().replace("+00:00","Z"),"end":end.isoformat().replace("+00:00","Z"),"limit":10000,"sort":"asc"}
        token=None
        while True:
            if token: params["page_token"]=token
            x=get("https://data.alpaca.markets/v1beta1/options/bars",params,key,secret)
            for s,rows in (x.get("bars") or {}).items(): out[s].extend(rows or [])
            token=x.get("next_page_token")
            if not token: break
    return out


def n(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception: return None


def main():
    key,secret,_=alpaca_credentials()
    eps=load_episodes()
    by_ticker=contracts_for(sorted({e["ticker"] for e in eps}),key,secret)
    chosen=[]
    for e in eps:
        c=choose(e,by_ticker[e["ticker"]])
        if c: chosen.append({**e,"symbol":c["symbol"],"expiry":c["expiration_date"],"strike":float(c["strike_price"]),"option_type":c["type"]})
    if not chosen:
        print(json.dumps({"error":"no contracts"})); return
    start=min(e["t"] for e in chosen)-timedelta(minutes=1)
    end=datetime.now(UTC)
    bars=fetch_bars(sorted({e["symbol"] for e in chosen}),start,end,key,secret)
    results=[]
    for e in chosen:
        rr=[]
        for b in bars.get(e["symbol"],[]):
            try: bt=dt(b["t"])
            except Exception: continue
            if bt>=e["t"]: rr.append((bt,b))
        if not rr: continue
        entry=n(rr[0][1].get("vw")) or n(rr[0][1].get("c"))
        if not entry or entry<=0: continue
        highs=[n(b.get("h")) for _,b in rr]; lows=[n(b.get("l")) for _,b in rr]; closes=[n(b.get("c")) for _,b in rr]
        highs=[x for x in highs if x is not None]; lows=[x for x in lows if x is not None]; closes=[x for x in closes if x is not None]
        if not closes: continue
        final=closes[-1]; maxret=(max(highs)/entry-1)*100 if highs else None; minret=(min(lows)/entry-1)*100 if lows else None; endret=(final/entry-1)*100
        outcome="open"
        hit20=maxret is not None and maxret>=20
        hitm20=minret is not None and minret<=-20
        if hit20 and hitm20: outcome="both_touched_order_unknown"
        elif hit20: outcome="plus20"
        elif hitm20: outcome="minus20"
        results.append({**e,"t":e["t"].isoformat(),"entry_option":round(entry,4),"final_option":round(final,4),"end_return_pct":round(endret,2),"max_return_pct":round(maxret,2) if maxret is not None else None,"min_return_pct":round(minret,2) if minret is not None else None,"option_outcome":outcome,"bars":len(rr)})
    def summarize(scan,field):
        rows=[r for r in results if r["scan"]==scan]
        g=defaultdict(list)
        for r in rows:g[r[field]].append(r)
        out=[]
        for name,a in sorted(g.items(),key=lambda kv:(-len(kv[1]),kv[0])):
            out.append({"name":name,"n":len(a),"avg_end_pct":round(sum(r["end_return_pct"] for r in a)/len(a),2),"median_end_pct":round(sorted(r["end_return_pct"] for r in a)[len(a)//2],2),"avg_max_pct":round(sum(r["max_return_pct"] for r in a)/len(a),2),"plus20":sum(r["option_outcome"]=="plus20" for r in a),"minus20":sum(r["option_outcome"]=="minus20" for r in a),"open_or_both":sum(r["option_outcome"] not in {"plus20","minus20"} for r in a)})
        return out
    payload={"as_of":end.isoformat(),"method":"nearest listed expiry, nearest-to-alert-spot call for bullish / put for bearish; entry first 1-minute option bar VWAP at/after alert; no historical bid/ask","episodes_with_option_bars":len(results),"scan_summary":{},"results":results}
    for scan in ("flash_agentic","flash"):
        rr=[r for r in results if r["scan"]==scan]
        payload["scan_summary"][scan]={"n":len(rr),"avg_end_pct":round(sum(r["end_return_pct"] for r in rr)/len(rr),2) if rr else None,"median_end_pct":round(sorted(r["end_return_pct"] for r in rr)[len(rr)//2],2) if rr else None,"profitable_at_cutoff":sum(r["end_return_pct"]>0 for r in rr),"plus20":sum(r["option_outcome"]=="plus20" for r in rr),"minus20":sum(r["option_outcome"]=="minus20" for r in rr),"by_setup":summarize(scan,"setup"),"by_ticker":summarize(scan,"ticker")}
    print(json.dumps(payload,indent=2))

if __name__=="__main__": main()
