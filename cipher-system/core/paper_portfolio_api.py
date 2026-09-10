"""Read-only presentation model for the six local shadow portfolios."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from core.fronttest_portfolios import DEFAULT_DB, NY, SPECS
from core.paper_executor.config import load_config
from core.exchange_calendar import is_session


AUTOPILOT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "paper_autopilot_shadow.yaml"
AUTOPILOT_DB = Path("/home/aarav/Aarav/cipher/runtime/data/paper_runtime/data/paper_trades/autopilot_shadow.sqlite")


def _decode(value: str | None) -> dict:
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}


def _open_mark(row: dict, *, now: datetime, stale_after_seconds: float = 120.0) -> dict[str, Any]:
    """Value an open position at midpoint and at a conservative liquidation fill."""
    try:
        long_bid, long_ask = float(row["last_bid"]), float(row["last_ask"])
        entry_fill, quantity = float(row["entry_fill"]), int(row["quantity"])
    except (TypeError, ValueError):
        return {
            "mark_status": "unavailable", "mark_age_seconds": None,
            "mark_mid": None, "unrealized_pnl_mid": None,
            "liquidation_fill": None, "liquidation_pnl": None,
        }
    long_mid = (long_bid + long_ask) / 2
    long_liquidation = max(0.0, long_bid * .995 - .01)
    if row.get("structure") == "debit_spread":
        try:
            short_bid, short_ask = float(row["short_last_bid"]), float(row["short_last_ask"])
        except (TypeError, ValueError):
            return {
                "mark_status": "unavailable", "mark_age_seconds": None,
                "mark_mid": None, "unrealized_pnl_mid": None,
                "liquidation_fill": None, "liquidation_pnl": None,
            }
        mark_mid = max(0.0, long_mid - (short_bid + short_ask) / 2)
        liquidation = max(0.0, long_liquidation - (short_ask * 1.005 + .01))
    else:
        mark_mid = long_mid
        liquidation = long_liquidation
    try:
        stamp = datetime.fromisoformat(str(row.get("last_mark_at")).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("naive mark")
        age = max(0.0, (now - stamp.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        age = None
    return {
        "mark_status": "unavailable" if age is None else ("stale" if age > stale_after_seconds else "current"),
        "mark_age_seconds": round(age, 3) if age is not None else None,
        "mark_mid": round(mark_mid, 6),
        "unrealized_pnl_mid": round((mark_mid - entry_fill) * quantity * 100, 2),
        "liquidation_fill": round(liquidation, 6),
        "liquidation_pnl": round((liquidation - entry_fill) * quantity * 100, 2),
    }


def _autopilot_portfolio(db_path: Path, *, now: datetime, recent_limit: int) -> dict[str, Any] | None:
    """Adapt Autopilot's canonical local ledger to the Paper Portfolios view."""
    if not db_path.is_file():
        return None
    cfg = load_config(AUTOPILOT_CONFIG)
    starting_cash = cfg.portfolio.starting_cash
    market_day = now.astimezone(NY).date().isoformat()
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=2.0) as db:
        db.row_factory = sqlite3.Row
        raw = [dict(row) for row in db.execute(
            """select p.*,
                      (select m.marked_at from paper_marks m where m.position_id=p.id order by m.marked_at desc limit 1) last_mark_at,
                      (select m.bid from paper_marks m where m.position_id=p.id order by m.marked_at desc limit 1) last_bid,
                      (select m.ask from paper_marks m where m.position_id=p.id order by m.marked_at desc limit 1) last_ask
                 from paper_positions p order by p.opened_at desc limit ?""",
            (recent_limit,),
        )]
        totals = db.execute(
            """select count(*) closed,coalesce(sum((exit_price-entry_price)*quantity*100),0) pnl,
                      coalesce(sum(case when exit_price>entry_price then 1 else 0 end),0) wins
                 from paper_positions where status='CLOSED'"""
        ).fetchone()
        counts = db.execute(
            """select count(*) signals,
                      (select count(*) from paper_positions where status in ('OPEN','SHADOW_OPEN')) opens,
                      (select count(*) from paper_positions where substr(opened_at,1,10)=?) entries_today,
                      (select count(*) from paper_positions where status='CLOSED' and substr(closed_at,1,10)=?) closes_today,
                      (select count(*) from paper_positions where status='CLOSED' and substr(closed_at,1,10)=? and exit_price>entry_price) wins_today,
                      (select count(*) from paper_positions where status='CLOSED' and substr(closed_at,1,10)=? and exit_price<entry_price) losses_today,
                      (select coalesce(sum((exit_price-entry_price)*quantity*100),0) from paper_positions where status='CLOSED' and substr(closed_at,1,10)=?) pnl_today
                 from signal_cards""",
            (market_day, market_day, market_day, market_day, market_day),
        ).fetchone()
        curve = [{"at": "autopilot_local", "equity": starting_cash}]
        equity = starting_cash
        for row in db.execute(
            """select closed_at,(exit_price-entry_price)*quantity*100 pnl
                 from paper_positions where status='CLOSED' order by closed_at"""
        ):
            equity += float(row["pnl"] or 0)
            curve.append({"at": row["closed_at"], "equity": round(equity, 2)})

    positions = []
    unrealized_mid = liquidation_pnl = 0.0
    stale_marks = unavailable_marks = 0
    for row in raw:
        quantity, entry = int(row["quantity"]), float(row["entry_price"])
        payload = _decode(row.get("payload_json"))
        execution_backend = str(payload.get("execution_backend") or "simulated")
        execution_mode = str(payload.get("mode") or "shadow")
        actual_broker_fill = execution_backend == "alpaca_paper" and execution_mode == "paper" and bool(payload.get("broker_order"))
        item = {
            "position_id": row["id"], "status": row["status"], "ticker": row["ticker"],
            "direction": row["direction"], "structure": "long_option", "contract": row["symbol"],
            "short_contract": None, "quantity": quantity, "entry_fill": entry,
            "entry_at": row["opened_at"], "exit_at": row["closed_at"],
            "exit_fill": row["exit_price"], "exit_reason": row["exit_reason"],
            "execution_backend": execution_backend, "execution_mode": execution_mode,
            "fill_provenance": "alpaca_paper" if actual_broker_fill else "cipher_modeled",
        }
        if row["status"] == "CLOSED":
            pnl = (float(row["exit_price"]) - entry) * quantity * 100
            item.update({"pnl": round(pnl, 2), "return_pct": round(pnl / (entry * quantity * 100) * 100, 2),
                         "mark_status": "closed", "mark_mid": None, "unrealized_pnl_mid": None,
                         "liquidation_pnl": None})
        else:
            mark = _open_mark({"last_bid": row["last_bid"], "last_ask": row["last_ask"],
                               "entry_fill": entry, "quantity": quantity,
                               "last_mark_at": row["last_mark_at"], "structure": "long_option"}, now=now)
            item.update(mark)
            unrealized_mid += float(mark["unrealized_pnl_mid"] or 0)
            liquidation_pnl += float(mark["liquidation_pnl"] or 0)
            stale_marks += mark["mark_status"] == "stale"
            unavailable_marks += mark["mark_status"] == "unavailable"
        positions.append(item)

    realized = float(totals["pnl"] or 0)
    open_count = int(counts["opens"] or 0)
    open_cost = sum(
        float(row["entry_price"]) * int(row["quantity"]) * 100
        for row in raw if row["status"] in {"OPEN", "SHADOW_OPEN"}
    )
    current_time = now.astimezone(NY).time().replace(tzinfo=None)
    start = datetime.strptime(cfg.strategy.entry_window_et_start or "00:00", "%H:%M").time()
    end = datetime.strptime(cfg.strategy.entry_window_et_end or "23:59", "%H:%M").time()
    entry_window_open = is_session(now.astimezone(NY).date()) and start <= current_time < end
    daily_loss_locked = int(counts["losses_today"]) >= cfg.portfolio.stop_after_daily_losses
    return {
        "portfolio_id": "autopilot_local", "strategy": "Cipher Autopilot · Local Paper",
        "starting_cash": starting_cash, "realized_equity": round(starting_cash + realized, 2),
        "realized_pnl": round(realized, 2), "closed_trades": int(totals["closed"]),
        "wins": int(totals["wins"]), "open_positions": open_count,
        "unrealized_pnl_mid": round(unrealized_mid, 2), "liquidation_pnl": round(liquidation_pnl, 2),
        "marked_equity": round(starting_cash + realized + unrealized_mid, 2),
        "liquidation_equity": round(starting_cash + realized + liquidation_pnl, 2),
        "cash_balance": round(starting_cash + realized - open_cost, 2),
        "daily_realized_pnl": round(float(counts["pnl_today"] or 0), 2),
        "daily_closed_trades": int(counts["closes_today"]), "daily_wins": int(counts["wins_today"]),
        "daily_losses": int(counts["losses_today"]),
        "daily_entries": int(counts["entries_today"]),
        "risk_state": {"daily_loss_locked": daily_loss_locked, "entry_window_open": entry_window_open,
                       "stale_open_marks": stale_marks, "unavailable_open_marks": unavailable_marks,
                       "new_entries_allowed": entry_window_open and not daily_loss_locked
                                              and int(counts["entries_today"]) < cfg.portfolio.maximum_new_positions_per_day},
        "config": {"symbol": "MULTI", "execution_backend": "simulated",
                   "external_order_capability": False},
        "positions": positions, "signals": [], "equity_curve": curve,
        "opportunity_summary": {"signals": int(counts["signals"]), "resolved": int(totals["closed"]),
                                "tracking": open_count, "targets": 0, "invalidations": 0,
                                "session_expired": 0, "skipped_targets": 0, "skipped_invalidations": 0,
                                "scope": "autopilot_local_option_ledger"},
        "bounded_recent_realized_pnl": round(sum(float(row.get("pnl") or 0) for row in positions), 2),
        "description": "Autopilot now executes in Cipher's internal quote-driven paper ledger. Historical Alpaca-paper rows remain labeled by provenance; new brokerage orders are disabled.",
        "enabled": True,
    }


def snapshot(db_path: Path = DEFAULT_DB, *, recent_limit: int = 30,
             autopilot_db_path: Path | None = None) -> dict[str, Any]:
    from core.theta_portfolio import dashboard
    theta = dashboard()
    if not db_path.exists():
        empty_opportunity = {
            "signals": 0, "resolved": 0, "tracking": 0, "targets": 0,
            "invalidations": 0, "session_expired": 0,
            "skipped_targets": 0, "skipped_invalidations": 0,
            "scope": "underlying_path_counterfactual",
        }
        return {
            "as_of": None, "last_simulation_run_at": None,
            "monitor_state": "not_initialized", "paper_only": True, "read_only": True,
            "execution_capability": False, "portfolio_count": 0,
            "combined_starting_cash": 0, "combined_equity": 0,
            "combined_realized_pnl": 0, "combined_marked_equity": 0,
            "combined_liquidation_equity": 0, "combined_unrealized_pnl_mid": 0,
            "combined_liquidation_pnl": 0, "daily_realized_pnl": 0,
            "opportunity_summary": empty_opportunity,
            "normalized_comparison": {
                "method": "equal_weight_closed_option_return_observations",
                "minimum_sample": 20, "ranked": False, "rows": [],
                "caveat": "No local paper database is initialized.",
            },
            "portfolios": [], "runs": [], "theta": theta,
            "caveat": "Local paper database is not initialized. No broker orders.",
        }
    now = datetime.now(timezone.utc)
    market_day = now.astimezone(NY).date().isoformat()
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as db:
        db.row_factory = sqlite3.Row
        portfolios = []
        comparison_rows = []
        for spec in SPECS:
            positions = [dict(row) for row in db.execute(
                "select * from positions where portfolio_id=? order by entry_at desc limit ?",
                (spec.portfolio_id, recent_limit),
            )]
            for row in positions:
                if row["status"] == "OPEN":
                    row.update(_open_mark(row, now=now))
            signals = [dict(row) for row in db.execute(
                """select s.signal_id,s.setup_id,s.direction,s.signal_at,s.detected_at,
                          s.disposition,s.skip_reason,o.status as outcome_status,
                          o.outcome,o.resolved_at,o.entry_underlying,o.exit_underlying,
                          o.target,o.stop,o.bars_observed,o.mfe_pct,o.mae_pct,o.methodology,
                          s.payload_json
                     from signals s left join signal_outcomes o on o.signal_id=s.signal_id
                    where s.portfolio_id=? order by s.detected_at desc limit ?""",
                (spec.portfolio_id, recent_limit),
            )]
            for signal in signals:
                payload = _decode(signal.pop("payload_json", None))
                if isinstance(payload.get("signal_record"), dict):
                    signal["signal_record"] = payload["signal_record"]
                    signal["canonical_signal_id"] = payload["signal_record"].get("signal_id")
                    signal["evidence_snapshot_ids"] = payload["signal_record"].get("evidence_snapshot_ids", [])
                if isinstance(payload.get("evidence_contract"), dict):
                    signal["evidence_contract"] = payload["evidence_contract"]
            realized = sum(float(row.get("pnl") or 0) for row in positions if row["status"] == "CLOSED")
            # Summary must cover all closed rows, not just the bounded audit trail above.
            totals = db.execute(
                """select count(*),coalesce(sum(pnl),0),
                          coalesce(sum(case when pnl>0 then 1 else 0 end),0)
                   from positions where portfolio_id=? and status='CLOSED'""",
                (spec.portfolio_id,),
            ).fetchone()
            normalized = db.execute(
                """select count(*) n,coalesce(avg(return_pct),0) avg_return,
                          coalesce(sum(case when return_pct>0 then return_pct else 0 end),0) gross_wins,
                          coalesce(sum(case when return_pct<0 then -return_pct else 0 end),0) gross_losses
                     from positions where portfolio_id=? and status='CLOSED'""",
                (spec.portfolio_id,),
            ).fetchone()
            open_count = int(db.execute(
                "select count(*) from positions where portfolio_id=? and status='OPEN'",
                (spec.portfolio_id,),
            ).fetchone()[0])
            open_rows = [dict(row) for row in db.execute(
                "select * from positions where portfolio_id=? and status='OPEN'", (spec.portfolio_id,),
            )]
            open_marks = [_open_mark(row, now=now) for row in open_rows]
            unrealized_mid = sum(float(row["unrealized_pnl_mid"] or 0) for row in open_marks)
            liquidation_pnl = sum(float(row["liquidation_pnl"] or 0) for row in open_marks)
            stale_marks = sum(row["mark_status"] == "stale" for row in open_marks)
            unavailable_marks = sum(row["mark_status"] == "unavailable" for row in open_marks)
            daily = db.execute(
                """select count(*),coalesce(sum(pnl),0),
                          coalesce(sum(case when pnl>0 then 1 else 0 end),0),
                          coalesce(sum(case when pnl<0 then 1 else 0 end),0)
                     from positions where portfolio_id=? and status='CLOSED'
                      and substr(exit_at,1,10)=?""", (spec.portfolio_id, market_day),
            ).fetchone()
            daily_entries = int(db.execute(
                "select count(*) from positions where portfolio_id=? and substr(entry_at,1,10)=?",
                (spec.portfolio_id, market_day),
            ).fetchone()[0])
            curve = [{"at": spec.portfolio_id, "equity": spec.starting_cash}]
            equity = spec.starting_cash
            for row in db.execute(
                "select exit_at,pnl from positions where portfolio_id=? and status='CLOSED' order by exit_at",
                (spec.portfolio_id,),
            ):
                equity += float(row["pnl"] or 0)
                curve.append({"at": row["exit_at"], "equity": equity})
            config = {
                "symbol": spec.symbol, "setup_ids": list(spec.setup_ids),
                "timeframe_minutes": spec.timeframe_minutes,
                "risk_fraction": spec.risk_fraction, "min_dte": spec.min_dte,
                "target_dte": spec.target_dte, "max_dte": spec.max_dte,
                "max_spread_pct": spec.maximum_spread_pct,
                "maximum_new_positions_per_day": spec.maximum_new_positions_per_day,
                "stop_after_daily_losses": spec.stop_after_daily_losses,
                "entry_start_et": spec.entry_start_et.strftime("%H:%M"),
                "entry_cutoff_et": spec.entry_cutoff_et.strftime("%H:%M"),
                "direction_flip_cooldown_minutes": spec.direction_flip_cooldown_minutes,
                "enabled": spec.enabled,
            }
            opportunity = db.execute(
                """select count(*),
                          coalesce(sum(case when o.status='RESOLVED' then 1 else 0 end),0),
                          coalesce(sum(case when o.status!='RESOLVED' then 1 else 0 end),0),
                          coalesce(sum(case when o.outcome='TARGET' then 1 else 0 end),0),
                          coalesce(sum(case when o.outcome='INVALIDATED' then 1 else 0 end),0),
                          coalesce(sum(case when o.outcome='SESSION_EXPIRED' then 1 else 0 end),0),
                          coalesce(sum(case when s.disposition='SKIPPED' and o.outcome='TARGET' then 1 else 0 end),0),
                          coalesce(sum(case when s.disposition='SKIPPED' and o.outcome='INVALIDATED' then 1 else 0 end),0)
                     from signals s left join signal_outcomes o on o.signal_id=s.signal_id
                    where s.portfolio_id=?""",
                (spec.portfolio_id,),
            ).fetchone()
            opportunity_summary = {
                "signals": int(opportunity[0]), "resolved": int(opportunity[1]),
                "tracking": int(opportunity[2]), "targets": int(opportunity[3]),
                "invalidations": int(opportunity[4]), "session_expired": int(opportunity[5]),
                "skipped_targets": int(opportunity[6]),
                "skipped_invalidations": int(opportunity[7]),
                "scope": "underlying_path_counterfactual",
            }
            portfolios.append({
                "portfolio_id": spec.portfolio_id, "strategy": spec.strategy,
                "starting_cash": spec.starting_cash,
                "realized_equity": spec.starting_cash + float(totals[1] or 0),
                "realized_pnl": float(totals[1] or 0), "closed_trades": int(totals[0]),
                "wins": int(totals[2] or 0),
                "open_positions": open_count,
                "unrealized_pnl_mid": round(unrealized_mid, 2),
                "liquidation_pnl": round(liquidation_pnl, 2),
                "marked_equity": round(spec.starting_cash + float(totals[1] or 0) + unrealized_mid, 2),
                "liquidation_equity": round(spec.starting_cash + float(totals[1] or 0) + liquidation_pnl, 2),
                "daily_realized_pnl": round(float(daily[1] or 0), 2),
                "daily_closed_trades": int(daily[0]), "daily_wins": int(daily[2]),
                "daily_losses": int(daily[3]), "daily_entries": daily_entries,
                "risk_state": {
                    "daily_loss_locked": int(daily[3]) >= spec.stop_after_daily_losses,
                    "entry_window_open": spec.entry_start_et <= now.astimezone(NY).time().replace(tzinfo=None) < spec.entry_cutoff_et,
                    "stale_open_marks": stale_marks,
                    "unavailable_open_marks": unavailable_marks,
                    "new_entries_allowed": (
                        int(daily[3]) < spec.stop_after_daily_losses
                        and daily_entries < spec.maximum_new_positions_per_day
                        and spec.entry_start_et <= now.astimezone(NY).time().replace(tzinfo=None) < spec.entry_cutoff_et
                    ),
                },
                "config": config, "positions": positions, "signals": signals,
                "equity_curve": curve,
                "opportunity_summary": opportunity_summary,
                "bounded_recent_realized_pnl": realized,
                "description": spec.description,
            })
            sample = int(normalized["n"] or 0)
            comparison_rows.append({
                "portfolio_id": spec.portfolio_id, "strategy": spec.strategy,
                "closed_sample": sample, "minimum_sample": 20,
                "sample_status": "USABLE" if sample >= 20 else ("EARLY" if sample >= 10 else "TINY"),
                "win_rate": round(int(totals[2] or 0) / sample * 100, 2) if sample else None,
                "average_option_return_pct": round(float(normalized["avg_return"] or 0), 2) if sample else None,
                "profit_factor_on_return_units": (
                    round(float(normalized["gross_wins"]) / float(normalized["gross_losses"]), 3)
                    if float(normalized["gross_losses"] or 0) > 0 else None
                ),
                "rank_eligible": sample >= 20,
            })
        runs = [dict(row) for row in db.execute(
            "select run_id,started_at,completed_at,status,error from runs order by run_id desc limit 20"
        )]
    autopilot = _autopilot_portfolio(autopilot_db_path or AUTOPILOT_DB, now=now, recent_limit=recent_limit)
    if autopilot:
        portfolios.append(autopilot)
        comparison_rows.append({
            "portfolio_id": autopilot["portfolio_id"], "strategy": autopilot["strategy"],
            "closed_sample": autopilot["closed_trades"], "minimum_sample": 20,
            "sample_status": "USABLE" if autopilot["closed_trades"] >= 20 else ("EARLY" if autopilot["closed_trades"] >= 10 else "TINY"),
            "win_rate": round(autopilot["wins"] / autopilot["closed_trades"] * 100, 2) if autopilot["closed_trades"] else None,
            "average_option_return_pct": None, "profit_factor_on_return_units": None,
            "rank_eligible": autopilot["closed_trades"] >= 20,
        })
        from core.paper_executor.cohort_evaluation import evaluate_cohort
        baseline_path = autopilot_db_path or AUTOPILOT_DB
        for name in ("confirmation", "cost", "exit"):
            candidate_path = baseline_path.parents[2] / "cohorts" / name / "paper.sqlite"
            if not candidate_path.is_file():
                continue
            candidate = _autopilot_portfolio(candidate_path, now=now, recent_limit=recent_limit)
            if candidate:
                candidate.update(portfolio_id=f"autopilot_{name}", strategy=f"Cipher Autopilot · {name.title()}",
                                 evaluation=evaluate_cohort(candidate_path, baseline_path))
                portfolios.append(candidate)
    last_run_at = max((row.get("completed_at") or row.get("started_at") or "" for row in runs), default="") or None
    as_of = last_run_at or datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat()
    opportunity_summary = {
        key: sum(int(row["opportunity_summary"].get(key) or 0) for row in portfolios)
        for key in (
            "signals", "resolved", "tracking", "targets", "invalidations",
            "session_expired", "skipped_targets", "skipped_invalidations",
        )
    }
    opportunity_summary["scope"] = "underlying_path_counterfactual"
    return {
        "as_of": as_of, "last_simulation_run_at": last_run_at,
        "monitor_state": "evaluated" if last_run_at else "initialized_waiting_for_session",
        "paper_only": True, "read_only": True,
        "execution_capability": False, "portfolio_count": len(portfolios),
        "combined_starting_cash": sum(row["starting_cash"] for row in portfolios),
        "combined_equity": sum(row["realized_equity"] for row in portfolios),
        "combined_realized_pnl": sum(row["realized_pnl"] for row in portfolios),
        "combined_marked_equity": round(sum(row["marked_equity"] for row in portfolios), 2),
        "combined_liquidation_equity": round(sum(row["liquidation_equity"] for row in portfolios), 2),
        "combined_unrealized_pnl_mid": round(sum(row["unrealized_pnl_mid"] for row in portfolios), 2),
        "combined_liquidation_pnl": round(sum(row["liquidation_pnl"] for row in portfolios), 2),
        "daily_realized_pnl": round(sum(row["daily_realized_pnl"] for row in portfolios), 2),
        "opportunity_summary": opportunity_summary,
        "normalized_comparison": {
            "method": "equal_weight_closed_option_return_observations",
            "minimum_sample": 20, "ranked": False,
            "rows": comparison_rows,
            "caveat": "Dollar P/L is not compared because portfolio risk fractions differ. No strategy is rank-eligible before 20 prospective closes.",
        },
        "portfolios": portfolios, "runs": runs, "theta": theta,
        "caveat": (
            "Local paper simulation with modeled spread crossing and slippage. "
            "Skipped-signal outcomes describe the subsequent underlying path only, not option P/L. "
            "Marked equity uses observed option midpoints; liquidation equity crosses the displayed spread with modeled slippage. "
            "Stale or missing marks are labeled. No broker orders."
        ),
    }
