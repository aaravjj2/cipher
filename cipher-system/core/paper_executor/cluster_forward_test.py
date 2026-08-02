from __future__ import annotations

import argparse
import os
import json
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .capture_backtest import Observation, cluster_allowed, debit_spread_proxy
from .capture_files import iter_capture_files, parse_capture_time, read_payload

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
try:
    ET = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    ET = datetime.now().astimezone().tzinfo or timezone.utc


@dataclass(frozen=True)
class ExitProfile:
    name: str
    maximum_hold_minutes: int
    take_profit_pct: float | None = None
    stop_loss_pct: float | None = None
    time_stop_minutes: int | None = None
    time_stop_max_pct: float | None = None
    trailing_activate_pct: float | None = None
    trailing_drawdown_pct: float | None = None
    force_close_time_et: str = "15:45"


@dataclass
class VirtualPosition:
    id: str
    profile: str
    ticker: str
    direction: str
    setup: str
    rank: int | None
    strength: float | None
    target: float | None
    invalidation: float | None
    entry_time: str
    entry_spot: float
    entry_spread_mark: float
    spread_width: float
    source_file: str
    status: str = "OPEN"
    exit_time: str | None = None
    exit_spot: float | None = None
    exit_spread_mark: float | None = None
    exit_reason: str | None = None
    pnl_dollars: float = 0.0
    pnl_pct: float = 0.0
    max_pnl_pct: float = 0.0
    min_pnl_pct: float = 0.0
    last_mark_time: str | None = None
    last_spot: float | None = None
    last_spread_mark: float | None = None
    research_context: dict[str, Any] = field(default_factory=dict)


PROFILES: tuple[ExitProfile, ...] = (
    ExitProfile(
        name="patient_120_tp40_sl25_time30",
        maximum_hold_minutes=120,
        take_profit_pct=40,
        stop_loss_pct=25,
        time_stop_minutes=30,
        time_stop_max_pct=-5,
    ),
    ExitProfile(
        name="longer_180_tp50_sl35_time45",
        maximum_hold_minutes=180,
        take_profit_pct=50,
        stop_loss_pct=35,
        time_stop_minutes=45,
        time_stop_max_pct=-8,
    ),
    ExitProfile(
        name="runner_240_tp60_sl50_trail",
        maximum_hold_minutes=240,
        take_profit_pct=60,
        stop_loss_pct=50,
        trailing_activate_pct=30,
        trailing_drawdown_pct=15,
    ),
    ExitProfile(
        name="wide_180_tp40_sl50_time60",
        maximum_hold_minutes=180,
        take_profit_pct=40,
        stop_loss_pct=50,
        time_stop_minutes=60,
        time_stop_max_pct=-10,
    ),
    ExitProfile(
        name="eod_runner_tp70_sl50_trail20",
        maximum_hold_minutes=360,
        take_profit_pct=70,
        stop_loss_pct=50,
        trailing_activate_pct=35,
        trailing_drawdown_pct=20,
    ),
)


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "created_at": utc_now_iso(),
            "processed_files": [],
            "open_positions": [],
            "closed_positions": [],
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    data.setdefault("processed_files", [])
    data.setdefault("open_positions", [])
    data.setdefault("closed_positions", [])
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def position_id(profile: ExitProfile, obs: Observation) -> str:
    target = round(obs.target or 0.0, 2)
    captured = obs.captured_at.isoformat()
    return "|".join([profile.name, obs.ticker, obs.direction or "", obs.setup, str(target), captured])


def profile_map() -> dict[str, ExitProfile]:
    return {profile.name: profile for profile in PROFILES}


def latest_json(folder: Path, pattern: str) -> dict[str, Any] | None:
    paths = sorted(folder.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["_source_file"] = str(path)
            return payload
    return None


def latest_row_by_ticker(folder: Path, pattern: str, row_key: str, ticker: str) -> dict[str, Any] | None:
    payload = latest_json(folder, pattern)
    if not payload:
        return None
    rows = payload.get(row_key) or payload.get("rows") or []
    if not isinstance(rows, list):
        return None
    normalized = ticker.upper()
    for row in rows:
        if isinstance(row, dict) and str(row.get("ticker") or "").upper() == normalized:
            out = dict(row)
            out["_source_file"] = payload.get("_source_file")
            out["_generated_at"] = payload.get("generated_at")
            return out
    return None


def kronos_status_snapshot() -> dict[str, Any]:
    try:
        from core import kronos_research

        status = kronos_research.status()
        deps = status.get("deps") or {}
        missing = [name for name, present in deps.items() if not present]
        return {
            "available": bool(status.get("ready_for_inference") and status.get("repo_present")),
            "ready_for_inference": bool(status.get("ready_for_inference")),
            "repo_present": bool(status.get("repo_present")),
            "missing_dependencies": missing,
            "role": status.get("role"),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def timesfm_status_snapshot() -> dict[str, Any]:
    model_dir = PROJECT_ROOT / "data" / "timesfm_model"
    manifest_path = model_dir / "manifest.json"
    try:
        from core import timesfm_walkforward

        status = timesfm_walkforward.runtime_status(model_dir=model_dir, manifest_path=manifest_path)
        return {
            "available": bool(status.get("ready_for_prospective_forecast")),
            "runtime_available": bool(status.get("runtime_available")),
            "manifest_present": bool(status.get("manifest_present")),
            "weights_present": bool(status.get("weights_present")),
            "blockers": status.get("blockers") or [],
            "warnings": status.get("warnings") or [],
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def ticker_research_context(ticker: str) -> dict[str, Any]:
    setup = latest_row_by_ticker(PROJECT_ROOT / "data" / "setup_research", "setup_research_*.json", "ranked", ticker)
    company = latest_row_by_ticker(PROJECT_ROOT / "data" / "company_research", "company_research_*.json", "rows", ticker)
    context: dict[str, Any] = {}
    context["setup_research"] = {"available": False}
    if setup:
        context["setup_research"] = {
            "available": True,
            "grade": setup.get("grade"),
            "score": setup.get("score"),
            "direction": setup.get("direction"),
            "setup": setup.get("setup"),
            "reasons": setup.get("reasons") or [],
            "source_file": setup.get("_source_file"),
            "generated_at": setup.get("_generated_at"),
        }
    context["company_news"] = {"available": False}
    if company:
        week = company.get("week") if isinstance(company.get("week"), dict) else {}
        headlines = company.get("headlines") if isinstance(company.get("headlines"), list) else []
        context["company_news"] = {
            "available": True,
            "grade": company.get("grade"),
            "alignment": company.get("alignment"),
            "today_change_pct": company.get("today_change_pct"),
            "week_return_pct": week.get("week_return_pct"),
            "headline_count": len(headlines),
            "latest_headlines": [
                {
                    "title": item.get("title"),
                    "published": item.get("published"),
                    "link": item.get("link"),
                }
                for item in headlines[:3]
                if isinstance(item, dict)
            ],
            "source_file": company.get("_source_file"),
            "generated_at": company.get("_generated_at"),
        }
    return context


def build_research_context(ticker: str, decision_time: datetime) -> dict[str, Any]:
    return {
        "captured_at": decision_time.isoformat(),
        "kronos": kronos_status_snapshot(),
        "timesfm": timesfm_status_snapshot(),
        **ticker_research_context(ticker),
        "caveat": "Context-only forward-test enrichment; does not place orders or gate entries yet.",
    }


def mark_for_position(position: VirtualPosition, spot: float) -> tuple[float, float, float]:
    proxy = debit_spread_proxy(position.entry_spot, spot, position.direction, position.target)
    mark = float(proxy["exit_spread_mark"])
    pnl_dollars = round((mark - position.entry_spread_mark) * 100, 2)
    pnl_pct = round((mark - position.entry_spread_mark) / position.entry_spread_mark * 100, 2)
    return mark, pnl_dollars, pnl_pct


def force_close_reached(now: datetime, profile: ExitProfile) -> bool:
    hh, mm = [int(part) for part in profile.force_close_time_et.split(":", 1)]
    return now.astimezone(ET).time() >= dt_time(hh, mm)


def exit_reason(position: VirtualPosition, profile: ExitProfile, now: datetime, pnl_pct: float) -> str | None:
    opened = parse_iso(position.entry_time)
    age_minutes = (now - opened).total_seconds() / 60
    if profile.take_profit_pct is not None and pnl_pct >= profile.take_profit_pct:
        return f"take_profit_{profile.take_profit_pct:g}"
    if profile.trailing_activate_pct is not None and position.max_pnl_pct >= profile.trailing_activate_pct:
        drawdown = position.max_pnl_pct - pnl_pct
        if profile.trailing_drawdown_pct is not None and drawdown >= profile.trailing_drawdown_pct:
            return f"trailing_{profile.trailing_activate_pct:g}_{profile.trailing_drawdown_pct:g}"
    if profile.stop_loss_pct is not None and pnl_pct <= -abs(profile.stop_loss_pct):
        return f"stop_loss_{profile.stop_loss_pct:g}"
    if profile.time_stop_minutes is not None and age_minutes >= profile.time_stop_minutes:
        threshold = profile.time_stop_max_pct if profile.time_stop_max_pct is not None else 0
        if pnl_pct <= threshold:
            return f"time_stop_{profile.time_stop_minutes}"
    if age_minutes >= profile.maximum_hold_minutes:
        return f"max_hold_{profile.maximum_hold_minutes}"
    if force_close_reached(now, profile):
        return "force_close"
    return None


def observation_cards(payload: dict[str, Any], path: Path) -> list[Observation]:
    captured = parse_capture_time(payload, path)
    if not captured:
        return []
    scan_type = str(payload.get("scan_type") or payload.get("scanner_type") or "cluster").lower()
    if scan_type != "cluster" and not path.name.lower().startswith("cluster_"):
        return []
    out: list[Observation] = []
    for card in payload.get("cards") or []:
        if not isinstance(card, dict):
            continue
        ticker = str(card.get("ticker") or "").upper().strip()
        spot = num(card.get("spot"))
        direction = str(card.get("direction") or "").lower().strip() or None
        if not ticker or spot is None or spot <= 0 or direction not in {"bullish", "bearish"}:
            continue
        rank_value = num(card.get("rank"))
        out.append(
            Observation(
                captured_at=captured,
                ticker=ticker,
                scan_type="cluster",
                setup=str(card.get("setup_type") or card.get("setup") or "cluster").lower(),
                direction=direction,
                spot=spot,
                target=num(card.get("target") or card.get("cluster_target")),
                invalidation=num(card.get("invalidation") or card.get("stop") or card.get("invalid")),
                score=num(card.get("score")),
                strength=num(card.get("strength")),
                rank=int(rank_value) if rank_value else None,
                source_file=str(path),
            )
        )
    return out


def open_position(profile: ExitProfile, obs: Observation, research_context: dict[str, Any] | None = None) -> VirtualPosition:
    proxy = debit_spread_proxy(obs.spot, obs.spot, obs.direction or "bullish", obs.target)
    return VirtualPosition(
        id=position_id(profile, obs),
        profile=profile.name,
        ticker=obs.ticker,
        direction=obs.direction or "bullish",
        setup=obs.setup,
        rank=obs.rank,
        strength=obs.strength,
        target=obs.target,
        invalidation=obs.invalidation,
        entry_time=obs.captured_at.isoformat(),
        entry_spot=obs.spot,
        entry_spread_mark=float(proxy["entry_spread_mark"]),
        spread_width=float(proxy["spread_width"]),
        source_file=obs.source_file,
        last_mark_time=obs.captured_at.isoformat(),
        last_spot=obs.spot,
        last_spread_mark=float(proxy["entry_spread_mark"]),
        research_context=research_context or {},
    )


def should_open(obs: Observation, latest_entry_by_key: dict[str, datetime], cooldown_minutes: int) -> bool:
    if obs.target is None or obs.direction not in {"bullish", "bearish"}:
        return False
    if not cluster_allowed(obs):
        return False
    key = "|".join([
        obs.ticker,
        obs.direction,
        obs.setup,
        str(round(obs.target or 0, 2)),
        str(obs.rank or ""),
    ])
    previous = latest_entry_by_key.get(key)
    if previous and obs.captured_at - previous <= timedelta(minutes=cooldown_minutes):
        return False
    latest_entry_by_key[key] = obs.captured_at
    return True


def process_file(path: Path, state: dict[str, Any], cooldown_minutes: int) -> dict[str, int]:
    payload = read_payload(path)
    observations = observation_cards(payload, path)
    if not observations:
        return {"observations": 0, "opened": 0, "closed": 0, "marked": 0}
    profiles = profile_map()
    open_positions = [VirtualPosition(**item) for item in state.get("open_positions", [])]
    closed_positions = list(state.get("closed_positions", []))
    latest_entry_by_key: dict[str, datetime] = {}
    for item in open_positions:
        opened = parse_iso(item.entry_time)
        latest_entry_by_key["|".join([
            item.ticker,
            item.direction,
            item.setup,
            str(round(item.target or 0, 2)),
            str(item.rank or ""),
        ])] = opened
    for item in closed_positions:
        latest_entry_by_key["|".join([
            str(item.get("ticker") or ""),
            str(item.get("direction") or ""),
            str(item.get("setup") or ""),
            str(round(float(item.get("target") or 0), 2)),
            str(item.get("rank") or ""),
        ])] = parse_iso(str(item.get("entry_time")))

    by_ticker = {obs.ticker: obs for obs in observations}
    opened = closed = marked = 0
    still_open: list[VirtualPosition] = []
    for position in open_positions:
        obs = by_ticker.get(position.ticker)
        if not obs:
            still_open.append(position)
            continue
        profile = profiles[position.profile]
        mark, pnl_dollars, pnl_pct = mark_for_position(position, obs.spot)
        position.last_mark_time = obs.captured_at.isoformat()
        position.last_spot = obs.spot
        position.last_spread_mark = mark
        position.pnl_dollars = pnl_dollars
        position.pnl_pct = pnl_pct
        position.max_pnl_pct = max(position.max_pnl_pct, pnl_pct)
        position.min_pnl_pct = min(position.min_pnl_pct, pnl_pct)
        marked += 1
        reason = exit_reason(position, profile, obs.captured_at, pnl_pct)
        if reason:
            position.status = "CLOSED"
            position.exit_time = obs.captured_at.isoformat()
            position.exit_spot = obs.spot
            position.exit_spread_mark = mark
            position.exit_reason = reason
            closed_positions.append(asdict(position))
            closed += 1
        else:
            still_open.append(position)

    for obs in observations:
        if not should_open(obs, latest_entry_by_key, cooldown_minutes):
            continue
        research_context = build_research_context(obs.ticker, obs.captured_at)
        for profile in PROFILES:
            position = open_position(profile, obs, research_context)
            if position.id not in {item.id for item in still_open}:
                still_open.append(position)
                opened += 1

    state["open_positions"] = [asdict(item) for item in still_open]
    state["closed_positions"] = closed_positions
    return {"observations": len(observations), "opened": opened, "closed": closed, "marked": marked}


def summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    rows = state.get("closed_positions", [])
    open_rows = state.get("open_positions", [])
    by_profile: dict[str, dict[str, Any]] = {}
    context_counts = Counter()
    for row in list(open_rows) + list(rows):
        context = row.get("research_context") or {}
        if (context.get("kronos") or {}).get("available"):
            context_counts["kronos_available"] += 1
        if (context.get("timesfm") or {}).get("available"):
            context_counts["timesfm_available"] += 1
        if (context.get("setup_research") or {}).get("available"):
            context_counts["setup_research_available"] += 1
        if (context.get("company_news") or {}).get("available"):
            context_counts["company_news_available"] += 1
    for profile in PROFILES:
        items = [row for row in rows if row.get("profile") == profile.name]
        open_items = [row for row in open_rows if row.get("profile") == profile.name]
        pnl = [float(row.get("pnl_dollars") or 0) for row in items]
        wins = sum(1 for value in pnl if value > 0)
        losses = sum(1 for value in pnl if value < 0)
        gross_win = sum(value for value in pnl if value > 0)
        gross_loss = abs(sum(value for value in pnl if value < 0))
        reasons = Counter(str(row.get("exit_reason") or "unknown") for row in items)
        tickers: defaultdict[str, float] = defaultdict(float)
        for row in items:
            tickers[str(row.get("ticker"))] += float(row.get("pnl_dollars") or 0)
        by_profile[profile.name] = {
            "closed": len(items),
            "open": len(open_items),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(items) * 100, 2) if items else 0,
            "total_pnl_dollars": round(sum(pnl), 2),
            "average_pnl_dollars": round(sum(pnl) / len(pnl), 2) if pnl else 0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "exit_reasons": dict(reasons.most_common()),
            "top_tickers": dict(sorted(tickers.items(), key=lambda kv: kv[1], reverse=True)[:8]),
        }
    return {
        "profiles": [asdict(profile) for profile in PROFILES],
        "capture_root": state.get("capture_root"),
        "processed_files": len(state.get("processed_files", [])),
        "open_positions": len(open_rows),
        "closed_positions": len(rows),
        "context_counts": dict(context_counts),
        "by_profile": by_profile,
        "updated_at": state.get("updated_at"),
    }


def write_report(runtime_root: Path, state: dict[str, Any]) -> Path:
    report_dir = runtime_root / "data" / "cluster_forward_tests"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_state(state)
    latest_json = report_dir / "latest_cluster_forward_test.json"
    latest_md = report_dir / "latest_cluster_forward_test.md"
    latest_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    lines = [
        "# Cluster Forward Test",
        "",
        f"- Updated: {summary.get('updated_at')}",
        f"- Capture root: {summary.get('capture_root') or runtime_root}",
        f"- Processed files: {summary['processed_files']}",
        f"- Open virtual positions: {summary['open_positions']}",
        f"- Closed virtual positions: {summary['closed_positions']}",
        f"- Context counts: {summary.get('context_counts') or {}}",
        "",
        "## Exit Profiles",
        "",
    ]
    for profile in summary["profiles"]:
        lines.append(
            "- {name}: hold {hold}m, TP {tp}, SL {sl}, time stop {ts}/{tsp}, trail {ta}/{td}".format(
                name=profile["name"],
                hold=profile["maximum_hold_minutes"],
                tp=profile.get("take_profit_pct"),
                sl=profile.get("stop_loss_pct"),
                ts=profile.get("time_stop_minutes"),
                tsp=profile.get("time_stop_max_pct"),
                ta=profile.get("trailing_activate_pct"),
                td=profile.get("trailing_drawdown_pct"),
            )
        )
    lines.extend(["", "## Results", ""])
    lines.append("| Profile | Closed | Open | Win Rate | Total P/L | Avg P/L | PF | Top Exit Reasons |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for name, row in summary["by_profile"].items():
        reasons = ", ".join(f"{k}:{v}" for k, v in list(row["exit_reasons"].items())[:4])
        pf = "" if row["profit_factor"] is None else row["profit_factor"]
        lines.append(
            f"| {name} | {row['closed']} | {row['open']} | {row['win_rate']}% | "
            f"{row['total_pnl_dollars']} | {row['average_pnl_dollars']} | {pf} | {reasons} |"
        )
    latest_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return latest_md


def acquire_singleton_lock(runtime_root: Path):
    lock_path = runtime_root / "state" / "cluster_forward_test.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        handle.seek(0)
        if msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise OSError("no supported file-locking module available")
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode("ascii"))
    handle.flush()
    return handle


def process_once(
    runtime_root: Path,
    state_path: Path,
    cooldown_minutes: int,
    capture_root: Path | None = None,
) -> dict[str, Any]:
    capture_root = capture_root or runtime_root
    state = load_state(state_path)
    state["capture_root"] = str(capture_root.resolve())
    processed = set(state.get("processed_files", []))
    totals = Counter()
    for path in iter_capture_files(capture_root):
        if not path.name.lower().startswith("cluster_"):
            continue
        key = str(path.resolve())
        if key in processed:
            continue
        try:
            result = process_file(path, state, cooldown_minutes)
            totals.update(result)
            processed.add(key)
        except Exception as exc:
            state.setdefault("errors", []).append({"file": str(path), "error": str(exc), "at": utc_now_iso()})
    state["processed_files"] = sorted(processed)
    save_state(state_path, state)
    report = write_report(runtime_root, state)
    return {"report": str(report), **dict(totals), **summarize_state(state)}


def seed_existing_state(
    runtime_root: Path,
    state_path: Path,
    reset: bool = False,
    capture_root: Path | None = None,
) -> dict[str, Any]:
    capture_root = capture_root or runtime_root
    state = {
        "schema_version": 1,
        "created_at": utc_now_iso(),
        "processed_files": [],
        "open_positions": [],
        "closed_positions": [],
    } if reset else load_state(state_path)
    state["capture_root"] = str(capture_root.resolve())
    processed = set(state.get("processed_files", []))
    cluster_files = 0
    for path in iter_capture_files(capture_root):
        if path.name.lower().startswith("cluster_"):
            cluster_files += 1
            processed.add(str(path.resolve()))
    state["processed_files"] = sorted(processed)
    save_state(state_path, state)
    write_report(runtime_root, state)
    return {
        "seeded_existing_cluster_files": cluster_files,
        "processed_files": len(processed),
        "reset": reset,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward-test cluster-only virtual spread exits from capture files.")
    parser.add_argument("--root", default=r"C:\Aarav\cipher-system\CipherCapture")
    parser.add_argument("--state", default=None)
    parser.add_argument(
        "--capture-root",
        default=None,
        help="Optional input root containing ready/ and uploaded/ capture folders; state and reports remain under --root.",
    )
    parser.add_argument("--cooldown-minutes", type=int, default=10)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=30)
    parser.add_argument("--seed-existing", action="store_true", help="Mark all existing cluster files processed before watching.")
    parser.add_argument("--seed-today", action="store_true", help="Deprecated alias for --seed-existing.")
    parser.add_argument("--reset-state", action="store_true", help="Clear virtual positions before seeding existing files.")
    args = parser.parse_args()

    runtime_root = Path(args.root)
    capture_root = Path(args.capture_root) if args.capture_root else runtime_root
    state_path = Path(args.state) if args.state else runtime_root / "state" / "cluster_forward_test_state.json"
    lock_handle = acquire_singleton_lock(runtime_root) if args.watch else None
    if args.watch and lock_handle is None:
        print(json.dumps({"status": "already_running"}, indent=2))
        return 0
    if args.seed_existing or args.seed_today:
        print(
            json.dumps(
                seed_existing_state(
                    runtime_root,
                    state_path,
                    args.reset_state,
                    capture_root=capture_root,
                ),
                indent=2,
            )
        )
    while True:
        print(
            json.dumps(
                process_once(
                    runtime_root,
                    state_path,
                    args.cooldown_minutes,
                    capture_root=capture_root,
                ),
                indent=2,
            )
        )
        if not args.watch:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
