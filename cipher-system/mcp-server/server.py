#!/usr/bin/env python3
"""Cipher Research MCP — browser-first research workflow, local-only storage.

This server intentionally does not control a browser or trade.  An MCP host AI
uses Browser/Computer capabilities to carry out the stated Cipher step, then
calls this server to preserve structured evidence and progress.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "cipher_research.db"
EXPORTS = ROOT / "exports"

WORKFLOW = [
    ("strike_matrix", "Open Strike Matrix", "Load ticker; capture spot, GEX/VEX mode, gamma flip, call wall, put wall, and dominant expiration/strikes."),
    ("night_vision", "Open Night Vision + X-Ray", "Confirm price context, top pull, levels above/below spot, and chart time frame. Capture the visible chart."),
    ("spyglass", "Open Spyglass / flow", "Filter the intended date and premium tier. Record only observed prints, quote-side labels, and uncertainty."),
    ("scanner", "Run or review Setup Scanner", "Record setup rank, horizon, direction label, and the evidence—not a trade recommendation."),
    ("synthesis", "Write research synthesis", "Summarize agreement/conflict across matrix, chart, flow, and scanner. State invalidation and missing evidence."),
]

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def db() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS profiles (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, trading_date TEXT NOT NULL, tickers TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS steps (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, screen TEXT NOT NULL, action TEXT NOT NULL, rationale TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', details TEXT, completed_at TEXT);
    CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, screen TEXT NOT NULL, ticker TEXT, values_json TEXT NOT NULL, note TEXT NOT NULL, confidence TEXT NOT NULL, observed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS captures (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, screen TEXT NOT NULL, source_path TEXT NOT NULL, label TEXT NOT NULL, attached_at TEXT NOT NULL);
    """)
    return con

def rows(con: sqlite3.Connection, query: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(query, args).fetchall()]

def result(data: Any) -> dict[str, Any]:
    text = json.dumps(data, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], "structuredContent": data}

def fail(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}

def tool_specs() -> list[dict[str, Any]]:
    schema = lambda props, required=[]: {"type": "object", "properties": props, "required": required, "additionalProperties": False}
    return [
        {"name": "configure_ai_profile", "description": "Store the AI/provider/model profile used for a research workflow. This is metadata only; no API key is stored.", "inputSchema": schema({"provider": {"type":"string"}, "model": {"type":"string"}, "instructions": {"type":"string"}}, ["provider", "model"])},
        {"name": "start_daily_workflow", "description": "Create a daily Cipher research run and its ordered browser workflow steps.", "inputSchema": schema({"trading_date": {"type":"string", "description":"YYYY-MM-DD; defaults to today"}, "tickers": {"type":"array", "items":{"type":"string"}}, "mode": {"type":"string", "enum":["premarket","intraday","close"], "default":"premarket"}}, ["tickers"])},
        {"name": "get_next_cipher_step", "description": "Return the next incomplete browser/computer action for a run.", "inputSchema": schema({"run_id": {"type":"string"}}, ["run_id"])},
        {"name": "record_observation", "description": "Save visible/observed information from Cipher. Do not infer values that were not visible.", "inputSchema": schema({"run_id": {"type":"string"}, "screen": {"type":"string"}, "ticker": {"type":"string"}, "values": {"type":"object"}, "note": {"type":"string"}, "confidence": {"type":"string", "enum":["observed","inferred","uncertain"], "default":"observed"}, "complete_step": {"type":"boolean", "default":false}}, ["run_id", "screen", "values", "note"])},
        {"name": "attach_screenshot", "description": "Attach a local screenshot path as evidence for a run. The server stores the path and does not read browser secrets.", "inputSchema": schema({"run_id": {"type":"string"}, "screen": {"type":"string"}, "path": {"type":"string"}, "label": {"type":"string"}}, ["run_id", "screen", "path"])},
        {"name": "workflow_status", "description": "Return progress, observations, and capture references for a research run.", "inputSchema": schema({"run_id": {"type":"string"}}, ["run_id"])},
        {"name": "build_research_card", "description": "Assemble a neutral, evidence-linked research card from recorded observations. It never creates a trade instruction.", "inputSchema": schema({"run_id": {"type":"string"}}, ["run_id"])},
        {"name": "export_daily_report", "description": "Write a Markdown daily report from a completed or partial run.", "inputSchema": schema({"run_id": {"type":"string"}, "filename": {"type":"string"}}, ["run_id"])},
    ]

def handle_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    con = db()
    try:
        if name == "configure_ai_profile":
            profile = {"provider": args["provider"], "model": args["model"], "instructions": args.get("instructions", "")}
            con.execute("INSERT OR REPLACE INTO profiles(key,value,updated_at) VALUES('active',?,?)", (json.dumps(profile), now()))
            con.commit(); return result({"saved": True, "profile": profile})
        if name == "start_daily_workflow":
            tickers = sorted({str(x).upper().strip() for x in args["tickers"] if str(x).strip()})
            if not tickers: return fail("at least one ticker is required")
            run_id = f"run_{uuid.uuid4().hex[:12]}"; timestamp = now(); mode = args.get("mode", "premarket")
            con.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (run_id, args.get("trading_date") or date.today().isoformat(), json.dumps(tickers), mode, "active", timestamp, timestamp))
            for ordinal, (screen, action, rationale) in enumerate(WORKFLOW, 1): con.execute("INSERT INTO steps(run_id,ordinal,screen,action,rationale,status) VALUES(?,?,?,?,?,'pending')", (run_id, ordinal, screen, action, rationale))
            con.commit(); return result({"run_id": run_id, "tickers": tickers, "mode": mode, "steps": rows(con, "SELECT ordinal,screen,action,rationale,status FROM steps WHERE run_id=? ORDER BY ordinal", (run_id,))})
        if name == "get_next_cipher_step":
            step = con.execute("SELECT * FROM steps WHERE run_id=? AND status='pending' ORDER BY ordinal LIMIT 1", (args["run_id"],)).fetchone()
            return result({"complete": step is None, "next_step": dict(step) if step else None})
        if name == "record_observation":
            run = con.execute("SELECT id FROM runs WHERE id=?", (args["run_id"],)).fetchone()
            if not run: return fail("run not found")
            observed = now(); con.execute("INSERT INTO observations(run_id,screen,ticker,values_json,note,confidence,observed_at) VALUES(?,?,?,?,?,?,?)", (args["run_id"], args["screen"], args.get("ticker", "").upper() or None, json.dumps(args["values"]), args["note"], args.get("confidence", "observed"), observed))
            if args.get("complete_step"):
                con.execute("UPDATE steps SET status='complete',details=?,completed_at=? WHERE run_id=? AND screen=? AND status='pending'", (args["note"], observed, args["run_id"], args["screen"]))
            con.execute("UPDATE runs SET updated_at=? WHERE id=?", (observed, args["run_id"])); con.commit(); return result({"saved": True, "screen": args["screen"], "observed_at": observed})
        if name == "attach_screenshot":
            path = Path(args["path"]).expanduser()
            if not path.is_file(): return fail("screenshot path does not exist")
            con.execute("INSERT INTO captures(run_id,screen,source_path,label,attached_at) VALUES(?,?,?,?,?)", (args["run_id"], args["screen"], str(path), args.get("label", path.name), now())); con.commit(); return result({"attached": True, "path": str(path)})
        if name in {"workflow_status", "build_research_card", "export_daily_report"}:
            run = con.execute("SELECT * FROM runs WHERE id=?", (args["run_id"],)).fetchone()
            if not run: return fail("run not found")
            payload = {"run": dict(run), "steps": rows(con, "SELECT ordinal,screen,action,rationale,status,details,completed_at FROM steps WHERE run_id=? ORDER BY ordinal", (args["run_id"],)), "observations": rows(con, "SELECT screen,ticker,values_json,note,confidence,observed_at FROM observations WHERE run_id=? ORDER BY id", (args["run_id"],)), "captures": rows(con, "SELECT screen,source_path,label,attached_at FROM captures WHERE run_id=? ORDER BY id", (args["run_id"],))}
            if name == "workflow_status": return result(payload)
            card = {"run_id": args["run_id"], "tickers": json.loads(run["tickers"]), "as_of": run["updated_at"], "evidence": [{**o, "values": json.loads(o.pop("values_json"))} for o in payload["observations"]], "captures": payload["captures"], "required_caveat": "Research context only. OI-based exposure is a heuristic; quote-side labels are inference, not verified participant intent. No trade instruction is produced."}
            if name == "build_research_card": return result(card)
            EXPORTS.mkdir(parents=True, exist_ok=True); filename = args.get("filename") or f"{run['trading_date']}-{args['run_id']}.md"; out = EXPORTS / Path(filename).name
            lines = [f"# Cipher research — {run['trading_date']}", "", f"Run: `{args['run_id']}`", f"Tickers: {', '.join(json.loads(run['tickers']))}", "", "## Evidence"]
            for item in card["evidence"]: lines += [f"### {item['screen']} · {item.get('ticker') or 'workspace'}", item["note"], f"Values: `{json.dumps(item['values'])}`", f"Confidence: {item['confidence']}", ""]
            lines += ["## Captures"] + [f"- {c['screen']}: {c['label']} — `{c['source_path']}`" for c in card["captures"]] + ["", "## Caveat", card["required_caveat"]]
            out.write_text("\n".join(lines), encoding="utf-8"); return result({"exported": True, "path": str(out)})
        return fail(f"unknown tool: {name}")
    finally: con.close()

def prompts() -> list[dict[str, Any]]:
    return [
        {"name":"premarket","description":"Start a structured premarket Cipher research run.","arguments":[{"name":"tickers","required":True}]},
        {"name":"analyze_ticker","description":"Guide Browser/Computer through the evidence sequence for one ticker.","arguments":[{"name":"run_id","required":True},{"name":"ticker","required":True}]},
        {"name":"closing_review","description":"Turn recorded evidence into a neutral end-of-day research card.","arguments":[{"name":"run_id","required":True}]},
    ]

def handle(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize": return {"protocolVersion":"2025-06-18","capabilities":{"tools":{"listChanged":False},"prompts":{"listChanged":False},"resources":{"subscribe":False,"listChanged":False}},"serverInfo":{"name":"cipher-research-mcp","version":"0.1.0"}}
    if method == "ping": return {}
    if method == "tools/list": return {"tools": tool_specs()}
    if method == "tools/call": return handle_tool(params.get("name", ""), params.get("arguments") or {})
    if method == "prompts/list": return {"prompts": prompts()}
    if method == "prompts/get":
        name, a = params.get("name"), params.get("arguments") or {}
        if name == "premarket": text = f"Create a premarket run for {a.get('tickers')}. Use start_daily_workflow, then follow get_next_cipher_step. Drive the real Cipher tab with Browser/Computer when available. Record only visible evidence and attach captures."
        elif name == "analyze_ticker": text = f"For run {a.get('run_id')} and ticker {a.get('ticker')}, execute Matrix → Night Vision/X-Ray → Spyglass → Scanner. Use record_observation after each screen and mark steps complete only after evidence is saved."
        elif name == "closing_review": text = f"For run {a.get('run_id')}, call workflow_status, identify missing evidence, then call build_research_card and export_daily_report. Never turn the output into an order instruction."
        else: raise ValueError("prompt not found")
        return {"description": name, "messages":[{"role":"user","content":{"type":"text","text":text}}]}
    if method == "resources/list": return {"resources":[{"uri":"cipher://workflow/default","name":"Default Cipher workflow","mimeType":"application/json"},{"uri":"cipher://profile/active","name":"Active AI profile","mimeType":"application/json"}]}
    if method == "resources/templates/list": return {"resourceTemplates":[{"uriTemplate":"cipher://runs/{run_id}","name":"Research run","mimeType":"application/json"}]}
    if method == "resources/read":
        uri = params.get("uri", ""); con = db()
        try:
            if uri == "cipher://workflow/default": data = WORKFLOW
            elif uri == "cipher://profile/active":
                r = con.execute("SELECT value FROM profiles WHERE key='active'").fetchone(); data = json.loads(r[0]) if r else {}
            elif uri.startswith("cipher://runs/"):
                run_id = uri.rsplit("/",1)[1]; data = handle_tool("workflow_status", {"run_id": run_id}).get("structuredContent", {})
            else: raise ValueError("resource not found")
            return {"contents":[{"uri":uri,"mimeType":"application/json","text":json.dumps(data,indent=2)}]}
        finally: con.close()
    if method.startswith("notifications/") or method == "logging/setLevel": return None
    raise ValueError(f"method not found: {method}")

for line in sys.stdin:
    try:
        request = json.loads(line); response_id = request.get("id")
        try: payload = handle(request.get("method", ""), request.get("params") or {})
        except Exception as exc: payload = {"jsonrpc":"2.0","id":response_id,"error":{"code":-32603,"message":str(exc)}}
        else:
            if response_id is None: continue
            payload = {"jsonrpc":"2.0","id":response_id,"result":payload}
        sys.stdout.write(json.dumps(payload) + "\n"); sys.stdout.flush()
    except Exception as exc:
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":str(exc)}}) + "\n"); sys.stdout.flush()
