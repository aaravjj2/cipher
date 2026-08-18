"""Idempotent daily Discord digest for the six option shadow portfolios."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Callable

from core.fronttest_portfolios import DEFAULT_DB, NY, ACTIVE_SPECS, connect, portfolio_status
from core.paper_portfolio_api import _open_mark
from core.prospective_fronttests import DEFAULT_DB as DEFAULT_PROSPECTIVE_DB


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute("""
      create table if not exists daily_reports (
        report_day text primary key, generated_at text not null,
        delivered_at text, message text not null, snapshot_json text not null
      )
    """)
    db.commit()


def _prospective_snapshot(path: Path, report_day: date) -> list[dict]:
    if not path.is_file():
        return []
    day = report_day.isoformat()
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0) as db:
        db.row_factory = sqlite3.Row
        programs = []
        for program in db.execute("select program_id,name,minimum_sample from programs order by program_id"):
            totals = db.execute(
                """select count(*) signals,
                          coalesce(sum(case when substr(signal_bar_at,1,10)=? then 1 else 0 end),0) signals_today,
                          coalesce(sum(case when status!='VOID' then 1 else 0 end),0) eligible_signals,
                          coalesce(sum(case when status='OPEN' then 1 else 0 end),0) open_signals,
                          coalesce(sum(case when status='CLOSED' then 1 else 0 end),0) closed_signals,
                          coalesce(sum(case when status='VOID' then 1 else 0 end),0) void_signals,
                          coalesce(sum(case when status='CLOSED' and gross_underlying_return_pct>0 then 1 else 0 end),0) wins
                     from signals where program_id=?""",
                (day, program["program_id"]),
            ).fetchone()
            option_pnl_today = db.execute(
                """select coalesce(sum(pnl_per_contract),0)
                     from option_legs where status='CLOSED' and substr(exit_at,1,10)=?
                      and signal_id in (select signal_id from signals where program_id=?)""",
                (day, program["program_id"]),
            ).fetchone()[0]
            programs.append({
                "program_id": program["program_id"], "name": program["name"],
                "minimum_sample": int(program["minimum_sample"]),
                "signals": int(totals["signals"]), "signals_today": int(totals["signals_today"]),
                "eligible_signals": int(totals["eligible_signals"]),
                "open_signals": int(totals["open_signals"]), "closed_signals": int(totals["closed_signals"]),
                "void_signals": int(totals["void_signals"]),
                "wins": int(totals["wins"]), "option_pnl_today": float(option_pnl_today or 0),
            })
        return programs


def snapshot(
    db: sqlite3.Connection, report_day: date,
    prospective_db_path: Path = DEFAULT_PROSPECTIVE_DB,
) -> dict:
    day = report_day.isoformat()
    mark_now = datetime.now(timezone.utc)
    status = {row["portfolio_id"]: row for row in portfolio_status(db)}
    portfolios = []
    for spec in ACTIVE_SPECS:
        trades, pnl, wins = db.execute(
            """select count(*),coalesce(sum(pnl),0),
                      coalesce(sum(case when pnl>0 then 1 else 0 end),0)
                 from positions where portfolio_id=? and status='CLOSED'
                  and substr(exit_at,1,10)=?""",
            (spec.portfolio_id, day),
        ).fetchone()
        signals, opened, skipped = db.execute(
            """select count(*),
                      coalesce(sum(case when disposition='OPENED' then 1 else 0 end),0),
                      coalesce(sum(case when disposition='SKIPPED' then 1 else 0 end),0)
                 from signals where portfolio_id=? and substr(detected_at,1,10)=?""",
            (spec.portfolio_id, day),
        ).fetchone()
        skipped_target, skipped_invalidated, skipped_expired, pending_outcomes = db.execute(
            """select
                      coalesce(sum(case when s.disposition='SKIPPED' and o.outcome='TARGET' then 1 else 0 end),0),
                      coalesce(sum(case when s.disposition='SKIPPED' and o.outcome='INVALIDATED' then 1 else 0 end),0),
                      coalesce(sum(case when s.disposition='SKIPPED' and o.outcome='SESSION_EXPIRED' then 1 else 0 end),0),
                      coalesce(sum(case when o.status is null or o.status!='RESOLVED' then 1 else 0 end),0)
                 from signals s left join signal_outcomes o on o.signal_id=s.signal_id
                where s.portfolio_id=? and substr(s.signal_at,1,10)=?""",
            (spec.portfolio_id, day),
        ).fetchone()
        current = status[spec.portfolio_id]
        open_marks = [
            _open_mark(dict(row), now=mark_now)
            for row in db.execute(
                "select * from positions where portfolio_id=? and status='OPEN'",
                (spec.portfolio_id,),
            )
        ]
        unrealized_mid = sum(float(row["unrealized_pnl_mid"] or 0) for row in open_marks)
        liquidation_pnl = sum(float(row["liquidation_pnl"] or 0) for row in open_marks)
        portfolios.append({
            "portfolio_id": spec.portfolio_id, "strategy": spec.strategy,
            "daily_pnl": float(pnl or 0), "daily_trades": int(trades),
            "daily_wins": int(wins), "daily_losses": int(trades) - int(wins),
            "signals": int(signals), "opened_today": int(opened), "skipped": int(skipped),
            "skipped_targets": int(skipped_target),
            "skipped_invalidations": int(skipped_invalidated),
            "skipped_expired": int(skipped_expired),
            "pending_outcomes": int(pending_outcomes),
            "open_positions": current["open_positions"],
            "realized_equity": current["realized_equity"],
            "total_pnl": current["realized_pnl"],
            "unrealized_pnl_mid": round(unrealized_mid, 2),
            "liquidation_pnl": round(liquidation_pnl, 2),
            "marked_equity": round(current["realized_equity"] + unrealized_mid, 2),
            "daily_loss_locked": int(trades) - int(wins) >= spec.stop_after_daily_losses,
        })
    return {
        "report_day": day, "paper_only": True,
        "daily_pnl": sum(row["daily_pnl"] for row in portfolios),
        "combined_equity": sum(row["realized_equity"] for row in portfolios),
        "combined_marked_equity": sum(row["marked_equity"] for row in portfolios),
        "combined_unrealized_pnl_mid": sum(row["unrealized_pnl_mid"] for row in portfolios),
        "combined_liquidation_pnl": sum(row["liquidation_pnl"] for row in portfolios),
        "combined_starting_cash": sum(spec.starting_cash for spec in ACTIVE_SPECS),
        "daily_trades": sum(row["daily_trades"] for row in portfolios),
        "portfolios": portfolios,
        "prospective_programs": _prospective_snapshot(prospective_db_path, report_day),
    }


def format_message(data: dict) -> str:
    lines = [
        f"📊 Cipher Shadow Portfolios — {data['report_day']}",
        f"Daily realized: ${data['daily_pnl']:+,.2f} | Open mid: ${data['combined_unrealized_pnl_mid']:+,.2f} | Marked eq: ${data['combined_marked_equity']:,.2f}",
    ]
    ranked = sorted(data["portfolios"], key=lambda row: (-row["daily_pnl"], row["portfolio_id"]))
    for row in ranked:
        record = f"{row['daily_wins']}W/{row['daily_losses']}L" if row["daily_trades"] else "no closes"
        lines.append(
            f"• {row['portfolio_id']}: ${row['daily_pnl']:+,.2f} ({record}) | "
            f"mark ${row['marked_equity']:,.2f} | sig {row['signals']} "
            f"open {row['open_positions']} skip {row['skipped']}"
        )
        if row["daily_loss_locked"]:
            lines.append("  ↳ RISK LOCK: daily loss limit reached")
        if row["skipped"]:
            lines.append(
                f"  ↳ skipped path: {row['skipped_targets']} target / "
                f"{row['skipped_invalidations']} invalid / {row['skipped_expired']} expired / "
                f"{row['pending_outcomes']} pending"
            )
    if data.get("prospective_programs"):
        lines.append("Prospective cohorts (no backfill):")
        for row in data["prospective_programs"]:
            lines.append(
                f"• {row['program_id']}: +{row['signals_today']} signals | "
                f"{row['eligible_signals']} eligible / {row['void_signals']} void | "
                f"{row['closed_signals']}/{row['minimum_sample']} closed | "
                f"{row['wins']} positive | option Δ ${row['option_pnl_today']:+,.2f}"
            )
    lines.append("Paper simulation only — no broker orders.")
    message = "\n".join(lines)
    if len(message) > 1900:
        raise ValueError("Discord daily report exceeds the safe message limit")
    return message


def deliver(
    sender: Callable[[str], None], *, db_path: Path = DEFAULT_DB,
    prospective_db_path: Path = DEFAULT_PROSPECTIVE_DB,
    now: datetime | None = None, force: bool = False,
) -> dict:
    moment = (now or datetime.now(timezone.utc)).astimezone(NY)
    db = connect(db_path)
    try:
        ensure_schema(db)
        existing = db.execute(
            "select delivered_at,message from daily_reports where report_day=?",
            (moment.date().isoformat(),),
        ).fetchone()
        if existing and existing["delivered_at"] and not force:
            return {"status": "already_delivered", "report_day": moment.date().isoformat(),
                    "delivered_at": existing["delivered_at"]}
        data = snapshot(db, moment.date(), prospective_db_path)
        message = format_message(data)
        generated = datetime.now(timezone.utc).isoformat()
        db.execute(
            """insert into daily_reports(report_day,generated_at,message,snapshot_json)
               values (?,?,?,?) on conflict(report_day) do update set
               generated_at=excluded.generated_at,message=excluded.message,
               snapshot_json=excluded.snapshot_json""",
            (moment.date().isoformat(), generated, message, json.dumps(data, sort_keys=True)),
        )
        db.commit()
        sender(message)
        delivered_at = datetime.now(timezone.utc).isoformat()
        db.execute("update daily_reports set delivered_at=? where report_day=?",
                   (delivered_at, moment.date().isoformat()))
        db.commit()
        return {"status": "delivered", "report_day": moment.date().isoformat(),
                "delivered_at": delivered_at, "snapshot": data}
    finally:
        db.close()


def preview(
    db_path: Path = DEFAULT_DB, now: datetime | None = None,
    prospective_db_path: Path = DEFAULT_PROSPECTIVE_DB,
) -> dict:
    moment = (now or datetime.now(timezone.utc)).astimezone(NY)
    db = connect(db_path)
    try:
        ensure_schema(db)
        data = snapshot(db, moment.date(), prospective_db_path)
        return {"snapshot": data, "message": format_message(data)}
    finally:
        db.close()
