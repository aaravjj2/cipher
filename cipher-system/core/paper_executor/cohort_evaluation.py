"""Read-only, provenance-aware diagnostics for internal paper experiments.

Historical rows remain observable but never acquire prospective provenance by
being evaluated. Bootstrap samples whole NY sessions, preserving intraday
dependence. Replay uses observed quotes and the executor's actual fill functions.
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
from contextlib import closing
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .promotion_gate import eligible_strategies
from core.exchange_calendar import is_session


def _object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed


def _session(value: str) -> str:
    try:
        day = _time(value).astimezone(ZoneInfo("America/New_York")).date()
        return day.isoformat() if is_session(day) else "unknown"
    except (ValueError, TypeError, AttributeError):
        return "unknown"


def _read(path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        return tuple([dict(r) for r in db.execute(f"select * from {table}")]
                     if table in tables else [] for table in ("paper_positions", "paper_marks", "contract_mark_tape"))


def _evidence(row: dict) -> dict:
    return _object(_object(row.get("payload_json")).get("entry_evidence"))


def _version(row: dict) -> str:
    ev = _evidence(row)
    if not all(ev.get(k) for k in ("cohort_id", "version", "config_hash", "decision_at")) or ev.get("cohort_id") == "legacy":
        return "legacy/unknown"
    try:
        if _time(ev["decision_at"]) > _time(row["opened_at"]):
            return "legacy/unknown"
    except (ValueError, TypeError):
        return "legacy/unknown"
    return "/".join(str(ev[k]) for k in ("cohort_id", "version", "config_hash"))


def _pnl(row: dict) -> float | None:
    try:
        value = (float(row["exit_price"]) - float(row["entry_price"])) * int(row["quantity"]) * 100
        payload = _object(row.get("payload_json"))
        # Explicit separately charged costs only; fill-price slippage is already included.
        value -= float(payload.get("fees_usd") or 0)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, KeyError):
        return None


def _bootstrap(rows: list[dict]) -> float | None:
    sessions: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if _session(row["opened_at"]) != "unknown" and _pnl(row) is not None:
            sessions[_session(row["opened_at"])].append(_pnl(row))
    if len(sessions) < 2:
        return None
    blocks = [(sum(v), len(v)) for v in sessions.values()]
    rng = random.Random(20260909)
    estimates = []
    for _ in range(3000):
        sample = rng.choices(blocks, k=len(blocks))
        estimates.append(sum(x[0] for x in sample) / sum(x[1] for x in sample))
    # Two-sided 95% family confidence, Bonferroni across three candidates.
    return round(sorted(estimates)[int(len(estimates) * .05 / (2 * 3))], 4)


def _drawdown(rows: list[dict], marks: list[dict]) -> dict:
    ids = {row["id"] for row in rows}
    by_id = {row["id"]: row for row in rows}
    events = []
    for row in rows:
        events.append((_time(row["opened_at"]).timestamp(), 0, row["id"], _entry_liquidation_pnl(row)))
        if row.get("status") == "CLOSED" and _pnl(row) is not None:
            events.append((_time(row["closed_at"]).timestamp(), 2, row["id"], _pnl(row)))
    for mark in marks:
        if mark.get("position_id") not in ids:
            continue
        payload = _object(mark.get("payload_json"))
        value = payload.get("liquidation_value")
        if isinstance(value, (int, float)) and math.isfinite(value):
            row = by_id[mark["position_id"]]
            events.append((_time(mark["marked_at"]).timestamp(), 1, row["id"], value - row["entry_price"] * row["quantity"] * 100))
    active, realized, peak, drawdown, covered, missing = {}, 0.0, 0.0, 0.0, 0, 0
    for _, kind, pid, value in sorted(events):
        if kind == 0:
            active[pid] = value
        elif kind == 1 and pid in active:
            active[pid] = value
        elif kind == 2:
            active.pop(pid, None)
            realized += value
        if any(v is None for v in active.values()):
            missing += 1
            continue
        equity = realized + sum(active.values())
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        covered += 1
    return {"observed_liquidation_drawdown_usd": round(drawdown, 2) if covered else None,
            "drawdown_complete": missing == 0, "unpriced_observations": missing,
            "caveat": "Observed marks only; between-mark losses and stale overlapping marks may be unobserved."}


def _entry_liquidation_pnl(row):
    """Price the immediate exit using recorded entry quotes and cost assumptions."""
    from .config import SimulationConfig, ContractConfig
    from .fill_simulator import simulate_exit, simulate_spread_exit
    from .models import Quote
    evidence = _evidence(row)
    raw_quotes = evidence.get("quotes") or []
    if not raw_quotes or not evidence.get("execution_assumptions"):
        return None
    try:
        quotes = [Quote(**{**q, "timestamp": _time(q["timestamp"])}) for q in raw_quotes]
        sim = SimulationConfig(**evidence["execution_assumptions"])
        args = (sim, ContractConfig(), int(row["quantity"]), 75, _time(row["opened_at"]))
        price = (simulate_spread_exit(quotes[0], quotes[1], *args)["fill_price"] if len(quotes) == 2
                 else simulate_exit(quotes[0], *args).fill_price)
        return (price - row["entry_price"]) * row["quantity"] * 100
    except (ValueError, TypeError, KeyError):
        return None


def _metrics(rows: list[dict], marks: list[dict]) -> dict:
    closed = [r for r in rows if r.get("status") == "CLOSED" and _pnl(r) is not None]
    values = [_pnl(r) for r in closed]
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    sessions = {_session(r["opened_at"]) for r in rows} - {"unknown"}
    costs = [-value for row in rows if (value := _entry_liquidation_pnl(row)) is not None]
    return {"trades": len(closed), "open_positions": sum(r.get("status") != "CLOSED" for r in rows),
            "wins": len(wins), "win_rate_pct": round(100 * len(wins) / len(values), 2) if values else None,
            "pnl_usd": round(sum(values), 2), "expectancy_usd": round(sum(values) / len(values), 4) if values else None,
            "average_win_usd": round(sum(wins) / len(wins), 2) if wins else None,
            "average_loss_usd": round(sum(losses) / len(losses), 2) if losses else None,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else None,
            "no_realized_losses": bool(values) and not losses,
            "active_sessions": len(sessions), "entries_per_active_session": len(rows) / len(sessions) if sessions else None,
            "invalid_closed_rows": sum(r.get("status") == "CLOSED" and _pnl(r) is None for r in rows),
            "entry_cost_observations": len(costs), "mean_immediate_round_trip_cost_usd": sum(costs) / len(costs) if costs else None,
            **_drawdown(rows, marks)}


def _mark_coverage(rows: list[dict], marks: list[dict]) -> dict:
    """Require priced observations throughout each completed holding interval.

    No interpolation: a gap larger than the frozen quote-age limit blocks
    promotion even when entry and exit alone happen to show a profit.
    """
    missing, largest = 0, 0.0
    for row in rows:
        if row.get("status") != "CLOSED":
            continue
        start, end = _time(row["opened_at"]), _time(row["closed_at"])
        stamps = [start, end]
        for mark in marks:
            payload = _object(mark.get("payload_json"))
            value = payload.get("liquidation_value")
            if (mark.get("position_id") == row["id"] and payload.get("liquidation_basis") == "validated_exit_fill_after_costs"
                    and isinstance(value, (int, float)) and math.isfinite(value)):
                stamp = _time(mark["marked_at"])
                if start <= stamp <= end:
                    stamps.append(stamp)
        stamps.sort()
        gap = max((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
        largest = max(largest, gap)
        if gap > _evidence(row).get("quote_maximum_age_seconds", 75) or end < start:
            missing += 1
    return {"positions_with_monitoring_gaps": missing, "maximum_monitoring_gap_seconds": largest}


def evaluate_cohort(db_path: str | Path, baseline_path: str | Path | None = None) -> dict:
    rows, marks, tape = _read(Path(db_path))
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[_version(row)].append(row)
    baseline_rows, baseline_marks, _ = _read(Path(baseline_path)) if baseline_path else ([], [], [])
    eligible = eligible_strategies()
    versions = {}
    for version, group in sorted(groups.items()):
        result = _metrics(group, marks)
        result.update(_mark_coverage(group, marks))
        closed = [r for r in group if r.get("status") == "CLOSED" and _pnl(r) is not None]
        lower = _bootstrap(closed)
        blockers = []
        if version == "legacy/unknown": blockers.append("missing_prospective_provenance")
        if len(closed) < 60: blockers.append("fewer_than_60_closed_trades")
        if len({_session(r["opened_at"]) for r in closed} - {"unknown"}) < 20: blockers.append("fewer_than_20_closed_trade_sessions")
        if not result["expectancy_usd"] or result["expectancy_usd"] <= 0: blockers.append("nonpositive_expectancy")
        if not result["no_realized_losses"] and (result["profit_factor"] is None or result["profit_factor"] <= 1.1): blockers.append("profit_factor_not_above_1_1")
        if lower is None or lower <= 0: blockers.append("adjusted_bootstrap_lower_bound_not_positive")
        if any(_evidence(r).get("registry_strategy_id") not in eligible for r in group): blockers.append("registry_requirements_not_verified")
        if any(not _evidence(r).get("execution_assumptions") for r in group): blockers.append("execution_cost_provenance_missing")
        if result["invalid_closed_rows"]: blockers.append("invalid_accounting_rows")
        if any(_session(r["opened_at"]) == "unknown" for r in group): blockers.append("non_session_or_invalid_entry_timestamp")
        if result["entry_cost_observations"] != len(group): blockers.append("entry_liquidation_evidence_missing")
        if not result["drawdown_complete"]: blockers.append("liquidation_drawdown_incomplete")
        if result["positions_with_monitoring_gaps"]: blockers.append("monitoring_quote_coverage_incomplete")
        # Operational reconciliation is intentionally external to a read-only performance audit.
        blockers.append("operational_reconciliation_required")
        sessions = {_session(r["opened_at"]) for r in group}
        matched = [r for r in baseline_rows if _session(r["opened_at"]) in sessions and _version(r) != "legacy/unknown"]
        matched_metrics = _metrics(matched, baseline_marks) if matched else None
        if baseline_path:
            if not matched_metrics:
                blockers.append("matched_baseline_missing")
            else:
                if len({_version(r) for r in matched}) != 1:
                    blockers.append("mixed_baseline_versions")
                if _mark_coverage(matched, baseline_marks)["positions_with_monitoring_gaps"]:
                    blockers.append("baseline_monitoring_quote_coverage_incomplete")
                if result["expectancy_usd"] is None or matched_metrics["expectancy_usd"] is None or result["expectancy_usd"] <= matched_metrics["expectancy_usd"]:
                    blockers.append("expectancy_not_above_baseline")
                if not result["drawdown_complete"] or not matched_metrics["drawdown_complete"]:
                    blockers.append("drawdown_comparison_incomplete")
                elif result["observed_liquidation_drawdown_usd"] > matched_metrics["observed_liquidation_drawdown_usd"]:
                    blockers.append("drawdown_worse_than_baseline")
        result.update({"bootstrap_expectancy_lower_usd": lower, "bootstrap_comparisons": 3,
                       "promotion_eligible": False, "promotion_blockers": blockers,
                       "matched_baseline": matched_metrics})
        versions[version] = result
    tape_positions = {r["position_id"] for r in tape}
    return {"database": str(Path(db_path).resolve()), "evidence_grade": "OBSERVED_LOCAL_PAPER",
            "overall": _metrics(rows, marks), "per_version": versions,
            "evidence_coverage": {"positions": len(rows), "positions_with_quote_tape": len(tape_positions),
                                  "positions_with_prospective_provenance": sum(_version(r) != "legacy/unknown" for r in rows)},
            "promotion_blockers": sorted({b for v in versions.values() for b in v["promotion_blockers"]}) or ["no_prospective_trades"],
            "claims": {"live_trading_readiness": False, "historical_replay_is_prospective": False}}


def observation_activity(db_path: str | Path, since: str | None = None) -> dict:
    """Include observed sessions with zero fills in the activity denominator."""
    path = Path(db_path)
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        if "signal_batches" not in tables:
            return {"observed_sessions": 0, "entries_per_observed_session": None, "rejection_reasons": {}}
        received = db.execute("select received_at from signal_batches").fetchall()
        sessions = {_session(r[0]) for r in received if since is None or _time(r[0]) >= _time(since)} - {"unknown"}
        entries = db.execute("select opened_at from paper_positions").fetchall()
        count = sum(_session(r[0]) in sessions and (since is None or _time(r[0]) >= _time(since)) for r in entries)
        reasons = {}
        if "system_events" in tables:
            for stamp, raw in db.execute("select event_time,payload_json from system_events where event_type='ENTRY_BLOCKED'"):
                if since is None or _time(stamp) >= _time(since):
                    reason = _object(raw).get("reason", "unknown")
                    reasons[reason] = reasons.get(reason, 0) + 1
        return {"observed_sessions": len(sessions), "entries_per_observed_session": count / len(sessions) if sessions else None,
                "rejection_reasons": reasons}
    finally:
        db.close()


def replay_recorded_quotes(observations: list[dict], *, simulation: Any, contract: Any,
                           quantity: int = 1, max_age_seconds: int = 30) -> dict:
    """Replay explicit entry/exit decisions against their captured quote snapshots.

Each observation supplies decision_at, side and long_quote (optional short_quote).
No interpolation, carried-forward prices, or inferred missing decisions are used.
"""
    from .fill_simulator import simulate_entry, simulate_exit, simulate_spread_entry, simulate_spread_exit
    from .models import Quote
    fills, gaps = [], []
    for index, item in enumerate(observations):
        try:
            side = item["side"]
            if side not in ("entry", "exit"):
                raise ValueError("unsupported decision")
            now = _time(item["decision_at"])
            def quote(raw: dict) -> Quote:
                return Quote(**{**raw, "timestamp": _time(raw["timestamp"])})
            long_quote = quote(item["long_quote"])
            args = (simulation, contract, quantity, max_age_seconds, now)
            if item.get("instrument_model") == "debit_spread" or item.get("short_quote"):
                short_quote = quote(item["short_quote"])
                if side == "entry":
                    width = item.get("width")
                    if width is None:
                        raise ValueError("spread width missing")
                    fill = simulate_spread_entry(long_quote, short_quote, *args, width=width)
                else:
                    fill = simulate_spread_exit(long_quote, short_quote, *args)
                price, filled = fill["fill_price"], fill["quantity"]
            else:
                fill = (simulate_entry if side == "entry" else simulate_exit)(long_quote, *args)
                price, filled = fill.fill_price, fill.quantity
            fills.append({"observation": index, "side": side, "fill_price": price, "quantity": filled})
        except (ValueError, KeyError, TypeError) as exc:
            gaps.append({"observation": index, "reason": str(exc)})
    return {"fills": fills, "gaps": gaps, "coverage_fraction": len(fills) / len(observations) if observations else None,
            "method": "recorded_decisions_and_quotes", "synthetic_prices": False}


def replay_ledger(db_path: str | Path) -> dict:
    """Reproduce recorded decisions; never substitute a new strategy's outcomes."""
    from .config import SimulationConfig, ContractConfig
    rows, _, tape = _read(Path(db_path))
    results, gaps = [], []
    for row in rows:
        evidence, payload = _evidence(row), _object(row.get("payload_json"))
        if not evidence.get("execution_assumptions") or not evidence.get("contract_policy") or not evidence.get("quotes"):
            gaps.append({"position_id": row["id"], "reason": "entry_execution_evidence_missing"})
            continue
        spread = payload.get("instrument_model") == "debit_spread"
        quote_list = evidence["quotes"]
        sides = ["entry", "exit"] if row.get("status") == "CLOSED" else ["entry"]
        for side in sides:
            try:
                decision = row["opened_at"] if side == "entry" else row["closed_at"]
                selected = quote_list
                if side == "exit":
                    selected = []
                    for quote in quote_list:
                        observed = [r for r in tape if r["position_id"] == row["id"] and r.get("symbol") == quote["symbol"]
                                    and _time(r["captured_at"]) <= _time(decision)]
                        if not observed:
                            raise ValueError("exit_quote_missing")
                        latest = max(observed, key=lambda r: _time(r["captured_at"]))
                        selected.append({"symbol": latest["symbol"], "timestamp": latest["observed_at"],
                                         **{k: latest.get(k) for k in ("bid", "ask", "bid_size", "ask_size", "last", "volume", "open_interest")}})
                observation = {"side": side, "decision_at": decision, "long_quote": selected[0],
                               "instrument_model": "debit_spread" if spread else "long_option"}
                if spread:
                    observation.update(short_quote=selected[1], width=payload["spread"]["width"])
                replay = replay_recorded_quotes([observation], simulation=SimulationConfig(**evidence["execution_assumptions"]),
                                               contract=ContractConfig(**evidence["contract_policy"]), quantity=row["quantity"],
                                               max_age_seconds=evidence.get("quote_maximum_age_seconds", 75))
                if replay["gaps"]:
                    raise ValueError(replay["gaps"][0]["reason"])
                expected = row["entry_price"] if side == "entry" else row["exit_price"]
                actual = replay["fills"][0]["fill_price"]
                results.append({"position_id": row["id"], "side": side, "recorded": expected,
                                "replayed": actual, "matches": abs(expected - actual) <= 0.0001})
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                gaps.append({"position_id": row["id"], "side": side, "reason": str(exc)})
    return {"decisions": results, "gaps": gaps, "all_covered_decisions_match": all(r["matches"] for r in results) if results else None,
            "claims": {"new_strategy_backtest": False, "synthetic_prices": False}}
