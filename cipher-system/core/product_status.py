"""Shared market-session and input-freshness contract for the trader UI."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo
from core.exchange_calendar import is_session, previous_session

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
    session_day = is_session(moment.date())
    clock = moment.time().replace(tzinfo=None)
    if not session_day:
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
        "last_session_date": (
            moment.date() if session_day and clock >= time(16)
            else previous_session(moment.date())
        ).isoformat(),
        "exchange_time": moment.isoformat(),
        "timezone": "America/New_York",
    }


def freshness(name: str, observed_at: str | None, *, now: datetime, session: dict,
              stale_after_seconds: int, source: str, detail: str | None = None,
              market_bound: bool = True) -> dict:
    parsed = _parse(observed_at)
    age = max(0.0, (now.astimezone(timezone.utc) - parsed).total_seconds()) if parsed else None
    if parsed is None:
        state = "unavailable"
    elif parsed > now.astimezone(timezone.utc):
        state = "unavailable"
    elif not market_bound:
        state = "current" if age <= stale_after_seconds else "stale"
    elif session["phase"] != "regular":
        observed_day = parsed.astimezone(NY).date()
        expected_day = date.fromisoformat(session["last_session_date"])
        state = "current" if age <= stale_after_seconds else "last_session" if observed_day == expected_day else "stale"
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
        (
            freshness("flow", flow_session.get("newest_event_at"), now=moment, session=session,
                      stale_after_seconds=120, source="tradier_stream", detail=flow_session.get("session_date"))
            if flow_session else {
                "name": "flow", "observed_at": None, "age_seconds": None,
                "state": "snapshot_only", "source": "alpaca_chain_snapshot",
                "detail": "Event-time Tradier tape retired; API fallback is one latest trade per contract.",
            }
        ),
        freshness("gex", gex_at, now=moment, session=session, stale_after_seconds=1200,
                  source="local_gex_capture", detail="public-OI heuristic; not dealer positioning"),
        freshness("scanner_universe", universe_meta.get("as_of"), now=moment, session=session,
                  stale_after_seconds=14 * 86400, source=universe_meta.get("source") or "fallback", market_bound=False),
        freshness("research_ranking", _json_timestamp(AUTOPILOT_LAST), now=moment, session=session,
                  stale_after_seconds=36 * 3600, source="autopilot", market_bound=False),
        freshness("paper_portfolios", paper_at, now=moment, session=session,
                  stale_after_seconds=20 * 60, source="shadow_simulator", detail=paper_detail),
    ]
    exceptions = [item for item in items if item["state"] in {"stale", "unavailable"}]
    return {
        "generated_at": moment.isoformat(), "ticker": ticker.upper(), "session": session,
        "items": items, "exceptions": exceptions, "healthy": not exceptions,
        "read_only": True,
    }
