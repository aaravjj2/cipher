"""Shared market-session and input-freshness contract for the trader UI."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
GEX_DB = Path(__file__).resolve().parents[1] / "data" / "gex_history.sqlite"
FRONTTEST_DB = Path("/home/aarav/Aarav/cipher/runtime/data/fronttest_portfolios/fronttest.sqlite")
AUTOPILOT_LAST = Path("/home/aarav/Aarav/cipher/runtime/governance/autopilot_last_run.json")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(str(value)), time(), NY).astimezone(timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def market_session(now: datetime | None = None) -> dict[str, Any]:
    moment = (now or datetime.now(timezone.utc)).astimezone(NY)
    weekday = moment.weekday() < 5
    clock = moment.time().replace(tzinfo=None)
    if not weekday:
        phase = "closed"
    elif time(4) <= clock < time(9, 30):
        phase = "premarket"
    elif time(9, 30) <= clock < time(16):
        phase = "regular"
    elif time(16) <= clock < time(20):
        phase = "postmarket"
    else:
        phase = "closed"
    return {
        "phase": phase,
        "is_regular": phase == "regular",
        "market_date": moment.date().isoformat(),
        "exchange_time": moment.isoformat(),
        "timezone": "America/New_York",
    }


def freshness(name: str, observed_at: str | None, *, now: datetime, session: dict,
              stale_after_seconds: int, source: str, detail: str | None = None) -> dict:
    parsed = _parse(observed_at)
    age = max(0.0, (now.astimezone(timezone.utc) - parsed).total_seconds()) if parsed else None
    if parsed is None:
        state = "unavailable"
    elif session["phase"] != "regular" and parsed.astimezone(NY).date().isoformat() <= session["market_date"]:
        state = "last_session"
    else:
        state = "current" if age is not None and age <= stale_after_seconds else "stale"
    return {
        "name": name, "observed_at": observed_at, "age_seconds": age,
        "state": state, "source": source, "detail": detail,
    }


def _scalar(db_path: Path, sql: str, args: tuple = ()) -> Any:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0) as db:
            row = db.execute(sql, args).fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


def _paper_clock() -> tuple[str | None, str]:
    latest = _scalar(FRONTTEST_DB, "select max(coalesce(completed_at,started_at)) from runs")
    if latest:
        return str(latest), "latest evaluated simulation pass"
    if FRONTTEST_DB.exists():
        return datetime.fromtimestamp(FRONTTEST_DB.stat().st_mtime, timezone.utc).isoformat(), "monitor initialized; no in-session evaluation run recorded yet"
    return None, "portfolio database unavailable"


def _json_timestamp(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for key in ("completed_at", "generated_at", "as_of", "started_at"):
        if payload.get(key):
            return str(payload[key])
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def status(*, ticker: str, quote: dict | None, flow_session: dict | None,
           universe_meta: dict, now: datetime | None = None) -> dict:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    session = market_session(moment)
    gex_at = _scalar(GEX_DB, "select max(captured_at) from gex_snapshots where ticker=?", (ticker.upper(),))
    paper_at, paper_detail = _paper_clock()
    items = [
        freshness("quote", (quote or {}).get("as_of"), now=moment, session=session,
                  stale_after_seconds=30, source=(quote or {}).get("feed") or "alpaca"),
        freshness("flow", (flow_session or {}).get("newest_event_at"), now=moment, session=session,
                  stale_after_seconds=120, source="tradier_stream",
                  detail=(flow_session or {}).get("session_date")),
        freshness("gex", gex_at, now=moment, session=session, stale_after_seconds=1200,
                  source="local_gex_capture", detail="public-OI heuristic; not dealer positioning"),
        freshness("scanner_universe", universe_meta.get("as_of"), now=moment, session=session,
                  stale_after_seconds=14 * 86400, source=universe_meta.get("source") or "fallback"),
        freshness("research_ranking", _json_timestamp(AUTOPILOT_LAST), now=moment, session=session,
                  stale_after_seconds=36 * 3600, source="autopilot"),
        freshness("paper_portfolios", paper_at, now=moment, session=session,
                  stale_after_seconds=20 * 60, source="shadow_simulator", detail=paper_detail),
    ]
    exceptions = [item for item in items if item["state"] in {"stale", "unavailable"}]
    return {
        "generated_at": moment.isoformat(), "ticker": ticker.upper(), "session": session,
        "items": items, "exceptions": exceptions, "healthy": not exceptions,
        "read_only": True,
    }
