#!/usr/bin/env python3
"""Cipher Research Engine MCP. Research only: no orders, no secrets in output."""
from __future__ import annotations
import json, math, os, re, sqlite3, sys, uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent; DB=ROOT/"data"/"cipher_research.db"; OUT=ROOT/"exports"; ENV=ROOT.parent/"app"/".env"
CHAIN="https://data.alpaca.markets/v1beta1/options/snapshots/{ticker}"
QUOTE="https://data.alpaca.markets/v2/stocks/{ticker}/quotes/latest"
OCC=re.compile(r"^([A-Z.]+)(\d{6})([CP])(\d{8})$")
STEPS=[("strike_matrix","Open Strike Matrix"),("night_vision","Open Night Vision + X-Ray"),("spyglass","Open Spyglass / flow"),("scanner","Run Setup Scanner"),("synthesis","Write research synthesis")]

def now(): return datetime.now(timezone.utc).isoformat()
def num(x):
 try:return None if x in (None,"") else float(x)
 except (TypeError,ValueError):return None
def js(x):return json.dumps(x,default=str)
def ret(x):return {"content":[{"type":"text","text":js(x)}],"structuredContent":x}
def err(x):return {"content":[{"type":"text","text":"Error: "+x}],"isError":True}
def sch(p,r=[]):return {"type":"object","properties":p,"required":r,"additionalProperties":False}
def rows(c,q,a=()):return [dict(x) for x in c.execute(q,a).fetchall()]

def db():
 DB.parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
 c.executescript("""CREATE TABLE IF NOT EXISTS profiles(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,trading_date TEXT NOT NULL,tickers TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS steps(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,ordinal INTEGER,screen TEXT,action TEXT,rationale TEXT,status TEXT DEFAULT 'pending',details TEXT,completed_at TEXT);
 CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,screen TEXT,ticker TEXT,values_json TEXT,note TEXT,confidence TEXT,observed_at TEXT);
 CREATE TABLE IF NOT EXISTS captures(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,screen TEXT,source_path TEXT,label TEXT,attached_at TEXT);
 CREATE TABLE IF NOT EXISTS option_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,ticker TEXT,contract_symbol TEXT,expiration TEXT,strike REAL,option_type TEXT,snapshot_at TEXT,feed TEXT,bid REAL,ask REAL,mid REAL,last REAL,volume REAL,open_interest REAL,iv REAL,delta REAL,gamma REAL,theta REAL,vega REAL,quote_time TEXT,raw_json TEXT);
 CREATE INDEX IF NOT EXISTS idx_os ON option_snapshots(run_id,ticker,contract_symbol);
 CREATE TABLE IF NOT EXISTS option_scores(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,ticker TEXT,contract_symbol TEXT,liquidity REAL,flow REAL,side TEXT,flags_json TEXT,created_at TEXT);
 CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,ticker TEXT,contract_symbol TEXT,score REAL,confidence TEXT,rationale_json TEXT,created_at TEXT);
 CREATE TABLE IF NOT EXISTS outcomes(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,ticker TEXT,contract_symbol TEXT,label TEXT,notes TEXT,observed_at TEXT);
 CREATE TABLE IF NOT EXISTS underlying_quotes(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,ticker TEXT,as_of TEXT,feed TEXT,bid REAL,ask REAL,mid REAL,last REAL,raw_json TEXT);""")
 return c

def need(c,r):
 x=c.execute("SELECT * FROM runs WHERE id=?",(r,)).fetchone()
 if not x:raise ValueError("run not found")
 return x

def creds(feed=None):
 values={}
 if ENV.is_file():
  for line in ENV.read_text(encoding="utf-8").splitlines():
   if "=" in line and not line.lstrip().startswith("#"):
    k,v=line.split("=",1);values[k.strip()]=v.strip().strip('"').strip("'")
 for k in ("ALPACA_ALGO_PLUS_KEY","ALPACA_ALGO_PLUS_SECRET","ALPACA_API_KEY","ALPACA_API_SECRET","ALPACA_DATA_FEED"):
  if os.getenv(k):values[k]=os.getenv(k)
 key=values.get("ALPACA_ALGO_PLUS_KEY") or values.get("ALPACA_API_KEY");secret=values.get("ALPACA_ALGO_PLUS_SECRET") or values.get("ALPACA_API_SECRET")
 if not key or not secret:raise ValueError("Alpaca market-data credentials are not configured locally.")
 feed=(feed or values.get("ALPACA_DATA_FEED") or "indicative").lower()
 if feed not in ("opra","indicative"):raise ValueError("feed must be opra or indicative")
 return key,secret,feed

def parse(symbol):
 m=OCC.match(symbol.upper())
 if not m:return symbol.upper(),None,None,None
 _,d,k,s=m.groups();return symbol.upper(),"20%s-%s-%s"%(d[:2],d[2:4],d[4:]),int(s)/1000,"call" if k=="C" else "put"

def chain(ticker,feed=None):
 key,secret,feed=creds(feed);url=CHAIN.format(ticker=ticker.upper())+"?"+urlencode({"feed":feed})
 req=Request(url,headers={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":secret,"Accept":"application/json"})
 try:
  with urlopen(req,timeout=30) as r:data=json.loads(r.read().decode())
 except HTTPError as e:raise ValueError("Alpaca request failed (HTTP %s)%s"%(e.code,"; the selected feed may require a subscription" if e.code==403 else ""))
 except URLError:raise ValueError("Unable to reach Alpaca market data. Check network access and retry.")
 items=data.get("snapshots",{})
 if not isinstance(items,dict):raise ValueError("Unexpected Alpaca option-chain response.")
 out=[]
 for symbol,item in items.items():
  q=item.get("latestQuote") or {};t=item.get("latestTrade") or {};g=item.get("greeks") or {};b=item.get("dailyBar") or {}
  bid=num(q.get("bp",q.get("bid_price")));ask=num(q.get("ap",q.get("ask_price")));mid=(bid+ask)/2 if bid is not None and ask is not None and ask>=bid else None
  contract,expiry,strike,kind=parse(symbol)
  out.append({"symbol":contract,"expiry":expiry,"strike":strike,"kind":kind,"at":now(),"feed":feed,"bid":bid,"ask":ask,"mid":mid,"last":num(t.get("p",t.get("price"))),"vol":num(b.get("v",b.get("volume",item.get("volume")))),"oi":num(item.get("openInterest",item.get("open_interest"))),"iv":num(item.get("impliedVolatility",item.get("implied_volatility"))),"delta":num(g.get("delta")),"gamma":num(g.get("gamma")),"theta":num(g.get("theta")),"vega":num(g.get("vega")),"quote_time":q.get("t",q.get("timestamp")),"raw":item})
 return out,feed

def underlying_quote(ticker):
 key,secret,_=creds()
 url="https://data.alpaca.markets/v2/stocks/{}/quotes/latest?".format(ticker.upper())+urlencode({"feed":"iex"})
 req=Request(url,headers={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":secret,"Accept":"application/json"})
 try:
  with urlopen(req,timeout=20) as r:data=json.loads(r.read().decode())
 except HTTPError as e:raise ValueError("Alpaca underlying quote failed (HTTP %s)."%e.code)
 except URLError:raise ValueError("Unable to reach Alpaca stock data. Check network access and retry.")
 q=data.get("quote",data);bid=num(q.get("bp",q.get("bid_price")));ask=num(q.get("ap",q.get("ask_price")))
 return {"ticker":ticker.upper(),"bid":bid,"ask":ask,"mid":(bid+ask)/2 if bid is not None and ask is not None else None,"last":num(q.get("ap",q.get("price"))),"at":q.get("t",q.get("timestamp",now())),"feed":"iex","raw":q}

def save_chain(c,r,ticker,items):
 c.executemany("INSERT INTO option_snapshots(run_id,ticker,contract_symbol,expiration,strike,option_type,snapshot_at,feed,bid,ask,mid,last,volume,open_interest,iv,delta,gamma,theta,vega,quote_time,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
 [(r,ticker,x["symbol"],x["expiry"],x["strike"],x["kind"],x["at"],x["feed"],x["bid"],x["ask"],x["mid"],x["last"],x["vol"],x["oi"],x["iv"],x["delta"],x["gamma"],x["theta"],x["vega"],x["quote_time"],js(x["raw"])) for x in items])

def recent(c,r,ticker):
 return rows(c,"SELECT s.* FROM option_snapshots s JOIN (SELECT contract_symbol,MAX(id) i FROM option_snapshots WHERE run_id=? AND ticker=? GROUP BY contract_symbol) z ON z.i=s.id",(r,ticker.upper()))

def score(x):
 bid,ask,mid=num(x["bid"]),num(x["ask"]),num(x["mid"]);vol=num(x["volume"]) or 0;oi=num(x["open_interest"]) or 0;flags=[];liq=0
 if mid and bid is not None and ask is not None and ask>=bid:
  spread=(ask-bid)/mid*100;liq+=40 if spread<=2 else 32 if spread<=5 else 22 if spread<=10 else 10 if spread<=20 else 0
  if spread>10:flags.append("wide_spread")
 else:flags.append("missing_two_sided_quote")
 liq+=min(25,vol/20)+min(20,oi/50)
 if vol<10:flags.append("low_volume")
 if oi<50:flags.append("low_open_interest")
 price=num(x["last"]) or mid or 0;premium=price*vol*100;ratio=vol/oi if oi else vol
 flow=min(35,12*math.log10(1+ratio))+min(25,8*math.log10(1+premium/1000))+min(20,liq*.2)
 last=num(x["last"]);side="unknown"
 if bid is not None and ask is not None and last is not None and ask>bid:
  w=ask-bid;side="ask_side_inferred" if abs(last-ask)<=w*.25 else "bid_side_inferred" if abs(last-bid)<=w*.25 else "mid_market_or_uncertain"
 else:flags.append("trade_side_unavailable")
 if ratio>=1:flags.append("volume_exceeds_open_interest")
 if premium>=100000:flags.append("large_notional")
 return round(min(100,liq),1),round(min(100,flow),1),side,round(premium,2),round(ratio,3),flags

def calc(c,r,ticker):
 items=recent(c,r,ticker)
 if not items:raise ValueError("No option-chain data for this run and ticker; call pull_option_chain first.")
 c.execute("DELETE FROM option_scores WHERE run_id=? AND ticker=?",(r,ticker.upper()));out=[]
 for x in items:
  liq,flow,side,premium,ratio,flags=score(x);classification="research_candidate" if liq>=60 and flow>=35 else "watch" if liq>=40 else "insufficient_liquidity"
  c.execute("INSERT INTO option_scores(run_id,ticker,contract_symbol,liquidity,flow,side,flags_json,created_at) VALUES(?,?,?,?,?,?,?,?)",(r,ticker.upper(),x["contract_symbol"],liq,flow,side,js(flags),now()))
  out.append({"contract_symbol":x["contract_symbol"],"expiration":x["expiration"],"strike":x["strike"],"option_type":x["option_type"],"bid":x["bid"],"ask":x["ask"],"mid":x["mid"],"volume":x["volume"],"open_interest":x["open_interest"],"liquidity_score":liq,"flow_score":flow,"quote_side":side,"notional_estimate":premium,"volume_open_interest_ratio":ratio,"flags":flags,"classification":classification})
 c.commit();return out

def tools():
 return [
 {"name":"configure_ai_profile","description":"Store provider/model metadata only; no API key is stored.","inputSchema":sch({"provider":{"type":"string"},"model":{"type":"string"},"instructions":{"type":"string"}},["provider","model"])},
 {"name":"start_daily_workflow","description":"Create a browser-first Cipher research run.","inputSchema":sch({"trading_date":{"type":"string"},"tickers":{"type":"array","items":{"type":"string"}},"mode":{"type":"string","enum":["premarket","intraday","close"]}},["tickers"])},
 {"name":"get_next_cipher_step","description":"Get the next visible Cipher research step.","inputSchema":sch({"run_id":{"type":"string"}},["run_id"])},
 {"name":"record_observation","description":"Save visible Cipher evidence.","inputSchema":sch({"run_id":{"type":"string"},"screen":{"type":"string"},"ticker":{"type":"string"},"values":{"type":"object"},"note":{"type":"string"},"confidence":{"type":"string","enum":["observed","inferred","uncertain"]},"complete_step":{"type":"boolean"}},["run_id","screen","values","note"])},
 {"name":"attach_screenshot","description":"Attach an existing local screenshot path as evidence.","inputSchema":sch({"run_id":{"type":"string"},"screen":{"type":"string"},"path":{"type":"string"},"label":{"type":"string"}},["run_id","screen","path"])},
 {"name":"pull_option_chain","description":"Fetch Alpaca option snapshots and persist them locally. No trade action.","inputSchema":sch({"run_id":{"type":"string"},"ticker":{"type":"string"},"feed":{"type":"string","enum":["opra","indicative"]},"limit":{"type":"integer","minimum":0}},["run_id","ticker"])},
 {"name":"pull_underlying_quote","description":"Fetch and persist the underlying quote used as price context. Market data only.","inputSchema":sch({"run_id":{"type":"string"},"ticker":{"type":"string"}},["run_id","ticker"])},
 {"name":"score_option_liquidity","description":"Score saved contracts by spread, volume, and open interest.","inputSchema":sch({"run_id":{"type":"string"},"ticker":{"type":"string"},"limit":{"type":"integer","minimum":1}},["run_id","ticker"])},
 {"name":"detect_unusual_flow","description":"Surface high-volume/OI, premium, and liquidity contracts. Side is inferred.","inputSchema":sch({"run_id":{"type":"string"},"ticker":{"type":"string"},"min_premium":{"type":"number"},"limit":{"type":"integer","minimum":1}},["run_id","ticker"])},
 {"name":"rank_research_candidates","description":"Rank research targets; never creates advice or orders.","inputSchema":sch({"run_id":{"type":"string"},"ticker":{"type":"string"},"limit":{"type":"integer","minimum":1}},["run_id","ticker"])},
 {"name":"compare_scan_to_previous","description":"Compare this snapshot with the latest prior saved scan.","inputSchema":sch({"run_id":{"type":"string"},"ticker":{"type":"string"},"limit":{"type":"integer","minimum":1}},["run_id","ticker"])},
 {"name":"record_outcome","description":"Save later observed outcomes for research calibration.","inputSchema":sch({"run_id":{"type":"string"},"ticker":{"type":"string"},"contract_symbol":{"type":"string"},"outcome_label":{"type":"string"},"notes":{"type":"string"}},["run_id","ticker","outcome_label","notes"])},
 {"name":"workflow_status","description":"Return run progress, captures, and data counts.","inputSchema":sch({"run_id":{"type":"string"}},["run_id"])},
 {"name":"build_research_card","description":"Assemble a neutral evidence-linked research card.","inputSchema":sch({"run_id":{"type":"string"}},["run_id"])},
 {"name":"export_daily_report","description":"Export a Markdown daily research report.","inputSchema":sch({"run_id":{"type":"string"},"filename":{"type":"string"}},["run_id"])},
 {"name":"export_research_brief","description":"Export a ticker-specific Markdown research brief.","inputSchema":sch({"run_id":{"type":"string"},"ticker":{"type":"string"},"filename":{"type":"string"}},["run_id","ticker"])}]

def status(c,r):
 run=need(c,r)
 return {"run":dict(run),"steps":rows(c,"SELECT ordinal,screen,action,status,details FROM steps WHERE run_id=? ORDER BY ordinal",(r,)),"observations":rows(c,"SELECT screen,ticker,values_json,note,confidence,observed_at FROM observations WHERE run_id=?",(r,)),"captures":rows(c,"SELECT screen,source_path,label,attached_at FROM captures WHERE run_id=?",(r,)),"market_data":rows(c,"SELECT ticker,feed,COUNT(*) count,MAX(snapshot_at) latest FROM option_snapshots WHERE run_id=? GROUP BY ticker,feed",(r,))}

def make_card(c,r):
 d=status(c,r)
 for x in d["observations"]:x["values"]=json.loads(x.pop("values_json"))
 d["candidates"]=rows(c,"SELECT ticker,contract_symbol,score,confidence,rationale_json FROM candidates WHERE run_id=? ORDER BY score DESC",(r,))
 for x in d["candidates"]:x["rationale"]=json.loads(x.pop("rationale_json"))
 d["required_caveat"]="Research context only. Data can be delayed or incomplete; quote-side is inferred and never verified intent. No trade instruction is produced."
 return d

def export(c,r,ticker,filename):
 d=make_card(c,r)
 if ticker:
  d["market_data"]=[x for x in d["market_data"] if x["ticker"]==ticker];d["candidates"]=[x for x in d["candidates"] if x["ticker"]==ticker];d["observations"]=[x for x in d["observations"] if x.get("ticker") in (None,ticker)]
 OUT.mkdir(parents=True,exist_ok=True);path=OUT/Path(filename or "%s-%s.md"%(r,ticker.lower() if ticker else "daily")).name
 lines=["# Cipher Research "+(ticker or "Daily"),"","Run: "+r,"","## Market data"]+["- %s: %s contracts, %s feed"%(x["ticker"],x["count"],x["feed"]) for x in d["market_data"]]+["","## Research candidates"]
 for x in d["candidates"]:lines+=["- %s %s: score %s (%s)"%(x["ticker"],x["contract_symbol"],x["score"],x["confidence"])]
 lines+=["","## Evidence"]+["- %s: %s"%(x["screen"],x["note"]) for x in d["observations"]]+["","## Caveat",d["required_caveat"]]
 path.write_text("\n".join(lines),encoding="utf-8");return str(path)

def call(name,a):
 c=db()
 try:
  if name=="configure_ai_profile":
   p={"provider":a["provider"],"model":a["model"],"instructions":a.get("instructions","")};c.execute("INSERT OR REPLACE INTO profiles VALUES('active',?,?)",(js(p),now()));c.commit();return ret({"saved":True,"profile":p})
  if name=="start_daily_workflow":
   tickers=sorted({str(x).upper().strip() for x in a["tickers"] if str(x).strip()})
   if not tickers:return err("at least one ticker is required")
   r="run_"+uuid.uuid4().hex[:12];stamp=now();c.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",(r,a.get("trading_date") or date.today().isoformat(),js(tickers),a.get("mode","premarket"),"active",stamp,stamp))
   for n,(screen,action) in enumerate(STEPS,1):c.execute("INSERT INTO steps(run_id,ordinal,screen,action,rationale,status) VALUES(?,?,?,?,?,'pending')",(r,n,screen,action,"visible evidence first"))
   c.commit();return ret({"run_id":r,"tickers":tickers,"steps":rows(c,"SELECT ordinal,screen,action,status FROM steps WHERE run_id=?",(r,))})
  if name=="get_next_cipher_step":
   need(c,a["run_id"]);x=c.execute("SELECT * FROM steps WHERE run_id=? AND status='pending' ORDER BY ordinal LIMIT 1",(a["run_id"],)).fetchone();return ret({"complete":x is None,"next_step":dict(x) if x else None})
  if name=="record_observation":
   need(c,a["run_id"]);stamp=now();c.execute("INSERT INTO observations(run_id,screen,ticker,values_json,note,confidence,observed_at) VALUES(?,?,?,?,?,?,?)",(a["run_id"],a["screen"],a.get("ticker","").upper() or None,js(a["values"]),a["note"],a.get("confidence","observed"),stamp))
   if a.get("complete_step"):c.execute("UPDATE steps SET status='complete',details=?,completed_at=? WHERE run_id=? AND screen=? AND status='pending'",(a["note"],stamp,a["run_id"],a["screen"]))
   c.execute("UPDATE runs SET updated_at=? WHERE id=?",(stamp,a["run_id"]));c.commit();return ret({"saved":True,"observed_at":stamp})
  if name=="attach_screenshot":
   need(c,a["run_id"]);p=Path(a["path"])
   if not p.is_file():return err("screenshot path does not exist")
   c.execute("INSERT INTO captures VALUES(NULL,?,?,?,?,?)",(a["run_id"],a["screen"],str(p),a.get("label",p.name),now()));c.commit();return ret({"attached":True,"path":str(p)})
  if name=="pull_option_chain":
   need(c,a["run_id"]);ticker=a["ticker"].upper();items,feed=chain(ticker,a.get("feed"));limit=int(a.get("limit") or 0);items=items[:limit] if limit else items
   if not items:return err("Alpaca returned no option snapshots for this ticker")
   save_chain(c,a["run_id"],ticker,items);c.commit();return ret({"saved":True,"ticker":ticker,"feed":feed,"contract_count":len(items),"note":"OPRA availability depends on the configured Alpaca subscription."})
  if name=="pull_underlying_quote":
   need(c,a["run_id"]);ticker=a["ticker"].upper();q=underlying_quote(ticker)
   c.execute("INSERT INTO underlying_quotes(run_id,ticker,as_of,feed,bid,ask,mid,last,raw_json) VALUES(?,?,?,?,?,?,?,?,?)",(a["run_id"],ticker,q["at"],q["feed"],q["bid"],q["ask"],q["mid"],q["last"],js(q["raw"])));c.commit()
   return ret({"saved":True,"ticker":ticker,"feed":q["feed"],"bid":q["bid"],"ask":q["ask"],"mid":q["mid"],"as_of":q["at"]})
  if name in ("score_option_liquidity","detect_unusual_flow","rank_research_candidates"):
   need(c,a["run_id"]);ticker=a["ticker"].upper();items=calc(c,a["run_id"],ticker)
   if name=="score_option_liquidity":return ret({"ticker":ticker,"scored_contracts":len(items),"contracts":sorted(items,key=lambda x:x["liquidity_score"],reverse=True)[:int(a.get("limit") or 50)],"method":"Spread, volume, and OI are research quality signals, not advice."})
   if name=="detect_unusual_flow":
    minimum=float(a.get("min_premium") or 5000);x=[z for z in items if z["notional_estimate"]>=minimum and z["volume_open_interest_ratio"]>=.5];x.sort(key=lambda z:(z["flow_score"],z["liquidity_score"]),reverse=True);return ret({"ticker":ticker,"contracts":x[:int(a.get("limit") or 25)],"caveat":"Premium is latest price or mid times volume times 100; side is inferred."})
   observed=c.execute("SELECT COUNT(*) FROM observations WHERE run_id=? AND (ticker=? OR ticker IS NULL)",(a["run_id"],ticker)).fetchone()[0];c.execute("DELETE FROM candidates WHERE run_id=? AND ticker=?",(a["run_id"],ticker));out=[]
   for z in items:
    if z["liquidity_score"]<40:continue
    value=round(z["liquidity_score"]*.45+z["flow_score"]*.55+min(5,observed),1);conf="A" if value>=70 and observed>=2 else "B" if value>=55 else "C"
    rationale={"liquidity":z["liquidity_score"],"flow":z["flow_score"],"side":z["quote_side"],"flags":z["flags"],"browser_evidence":observed};c.execute("INSERT INTO candidates VALUES(NULL,?,?,?,?,?,?,?)",(a["run_id"],ticker,z["contract_symbol"],value,conf,js(rationale),now()));out.append({"contract_symbol":z["contract_symbol"],"score":value,"confidence":conf,"rationale":rationale})
   c.commit();return ret({"ticker":ticker,"candidates":sorted(out,key=lambda z:z["score"],reverse=True)[:int(a.get("limit") or 15)],"caveat":"Prioritized research targets only, never recommendations or order instructions."})
  if name=="compare_scan_to_previous":
   need(c,a["run_id"]);ticker=a["ticker"].upper();p=c.execute("SELECT run_id FROM option_snapshots WHERE ticker=? AND run_id<>? ORDER BY id DESC LIMIT 1",(ticker,a["run_id"])).fetchone()
   if not p:return ret({"ticker":ticker,"previous_run_id":None,"changes":[],"note":"No prior saved scan exists."})
   cur={x["contract_symbol"]:x for x in recent(c,a["run_id"],ticker)};old={x["contract_symbol"]:x for x in recent(c,p["run_id"],ticker)};changes=[]
   for k,x in cur.items():
    if k not in old:continue
    y=old[k]
    def d(f):
     q,w=num(x[f]),num(y[f]);return round(q-w,6) if q is not None and w is not None else None
    changes.append({"contract_symbol":k,"mid_change":d("mid"),"volume_change":d("volume"),"open_interest_change":d("open_interest"),"iv_change":d("iv")})
   changes.sort(key=lambda x:abs(x["volume_change"] or 0)+abs(x["open_interest_change"] or 0),reverse=True);return ret({"ticker":ticker,"previous_run_id":p["run_id"],"changes":changes[:int(a.get("limit") or 25)],"caveat":"Snapshot differences can reflect timing and feeds; they do not establish intent."})
  if name=="record_outcome":
   need(c,a["run_id"]);c.execute("INSERT INTO outcomes VALUES(NULL,?,?,?,?,?,?)",(a["run_id"],a["ticker"].upper(),a.get("contract_symbol"),a["outcome_label"],a["notes"],now()));c.commit();return ret({"saved":True})
  if name=="workflow_status":return ret(status(c,a["run_id"]))
  if name=="build_research_card":return ret(make_card(c,a["run_id"]))
  if name in ("export_daily_report","export_research_brief"):
   need(c,a["run_id"]);ticker=a.get("ticker","").upper();return ret({"exported":True,"path":export(c,a["run_id"],ticker,a.get("filename"))})
  return err("unknown tool: "+name)
 except ValueError as e:return err(str(e))
 finally:c.close()

def handle(m,p):
 if m=="initialize":return {"protocolVersion":"2025-06-18","capabilities":{"tools":{"listChanged":False},"prompts":{"listChanged":False},"resources":{"subscribe":False,"listChanged":False}},"serverInfo":{"name":"cipher-research-mcp","version":"0.3.0"}}
 if m=="ping":return {}
 if m=="tools/list":return {"tools":tools()}
 if m=="tools/call":return call(p.get("name",""),p.get("arguments") or {})
 if m=="prompts/list":return {"prompts":[{"name":"premarket","description":"Run premarket research.","arguments":[{"name":"tickers","required":True}]},{"name":"analyze_ticker","description":"Analyze one ticker.","arguments":[{"name":"run_id","required":True},{"name":"ticker","required":True}]}]}
 if m=="resources/list":return {"resources":[{"uri":"cipher://workflow/default","name":"Default workflow","mimeType":"application/json"}]}
 if m=="resources/read" and p.get("uri")=="cipher://workflow/default":return {"contents":[{"uri":"cipher://workflow/default","mimeType":"application/json","text":js(STEPS)}]}
 if m.startswith("notifications/") or m=="logging/setLevel":return None
 raise ValueError("method not found: "+m)

for line in sys.stdin:
 try:
  q=json.loads(line);rid=q.get("id")
  try:payload=handle(q.get("method",""),q.get("params") or {})
  except Exception as e:payload={"jsonrpc":"2.0","id":rid,"error":{"code":-32603,"message":str(e)}}
  else:
   if rid is None:continue
   payload={"jsonrpc":"2.0","id":rid,"result":payload}
  print(js(payload),flush=True)
 except Exception as e:print(js({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":str(e)}}),flush=True)
