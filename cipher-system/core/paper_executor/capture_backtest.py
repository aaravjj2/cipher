from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .capture_files import iter_capture_files, parse_capture_time, read_payload
from .validation import normalize_scanner, normalize_setup


@dataclass(frozen=True)
class Observation:
    captured_at: datetime
    ticker: str
    scan_type: str
    setup: str
    direction: str | None
    spot: float
    target: float | None
    invalidation: float | None
    score: float | None
    strength: float | None
    rank: int | None
    source_file: str


CHANGE_DEDUPED_SCANS = {"flash", "flash_agentic"}
CLUSTER_TOP_RANK = 5


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def intraday_capture_date(root: Path, requested: str) -> date:
    if requested.lower() == "today":
        return datetime.now().astimezone().date()
    return date.fromisoformat(requested)


def load_observations(root: Path, trade_date: date) -> list[Observation]:
    out: list[Observation] = []
    for path in iter_capture_files(root):
        try:
            payload = read_payload(path)
        except Exception:
            continue
        captured = parse_capture_time(payload, path)
        if not captured or captured.astimezone().date() != trade_date:
            continue
        scan_type = normalize_scanner(payload.get("scan_type") or payload.get("scanner_type") or "unknown")
        for card in payload.get("cards") or []:
            if not isinstance(card, dict):
                continue
            ticker = str(card.get("ticker") or "").upper().strip()
            spot = num(card.get("spot"))
            if not ticker or spot is None or spot <= 0:
                continue
            direction = str(card.get("direction") or "").lower().strip() or None
            if direction not in {"bullish", "bearish"}:
                direction = None
            out.append(Observation(
                captured_at=captured,
                ticker=ticker,
                scan_type=scan_type,
                setup=normalize_setup(card.get("setup_type") or card.get("setup") or "unknown"),
                direction=direction,
                spot=spot,
                target=num(card.get("target") or card.get("cluster_target")),
                invalidation=num(card.get("invalidation") or card.get("stop") or card.get("invalid")),
                score=num(card.get("score")),
                strength=num(card.get("strength")),
                rank=int(num(card.get("rank")) or 0) or None,
                source_file=str(path),
            ))
    return sorted(out, key=lambda obs: obs.captured_at)


def dedupe_entries(observations: list[Observation], cooldown_minutes: int) -> list[Observation]:
    chosen: list[Observation] = []
    last_seen: dict[tuple[Any, ...], datetime] = {}
    last_core_state: dict[tuple[str, str], tuple[Any, ...]] = {}
    cooldown = timedelta(minutes=cooldown_minutes)
    for obs in observations:
        if obs.direction not in {"bullish", "bearish"} or obs.target is None:
            continue
        if obs.scan_type == "cluster" and not cluster_allowed(obs):
            continue
        core = core_strategy_state(obs)
        if obs.scan_type in CHANGE_DEDUPED_SCANS:
            state_key = (obs.scan_type, obs.ticker)
            if last_core_state.get(state_key) == core:
                continue
            last_core_state[state_key] = core
        key = (
            obs.scan_type,
            obs.ticker,
            core,
        )
        previous = last_seen.get(key)
        if previous and obs.captured_at - previous <= cooldown:
            continue
        last_seen[key] = obs.captured_at
        chosen.append(obs)
    return chosen


def price_band(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def core_strategy_state(obs: Observation) -> tuple[Any, ...]:
    if obs.scan_type in CHANGE_DEDUPED_SCANS:
        return (
            obs.direction,
            obs.setup,
        )
    return (
        obs.direction,
        obs.setup,
        price_band(obs.target),
        price_band(obs.invalidation),
        obs.rank,
    )


def cluster_allowed(obs: Observation) -> bool:
    setup_text = obs.setup.lower()
    if "quad" in setup_text:
        return True
    return obs.rank is not None and obs.rank <= CLUSTER_TOP_RANK


def target_hit(direction: str, spot: float, target: float) -> bool:
    return spot >= target if direction == "bullish" else spot <= target


def invalidation_hit(direction: str, spot: float, invalidation: float) -> bool:
    return spot <= invalidation if direction == "bullish" else spot >= invalidation


def option_proxy(entry_spot: float, exit_spot: float, direction: str, target: float | None) -> dict[str, float]:
    directional_move = exit_spot - entry_spot if direction == "bullish" else entry_spot - exit_spot
    target_distance = abs((target or entry_spot) - entry_spot)
    entry_premium = max(0.50, entry_spot * 0.01, target_distance * 0.35)
    delta = 0.55
    exit_premium = max(0.05, entry_premium + directional_move * delta)
    pnl_dollars = (exit_premium - entry_premium) * 100
    return {
        "entry_option_mark": round(entry_premium, 4),
        "exit_option_mark": round(exit_premium, 4),
        "option_pnl_dollars": round(pnl_dollars, 2),
        "option_pnl_pct": round((exit_premium - entry_premium) / entry_premium * 100, 2),
    }


def debit_spread_proxy(entry_spot: float, exit_spot: float, direction: str, target: float | None) -> dict[str, float | str]:
    directional_move = exit_spot - entry_spot if direction == "bullish" else entry_spot - exit_spot
    target_distance = abs((target or entry_spot) - entry_spot)
    width = max(1.0, min(10.0, target_distance or entry_spot * 0.01))
    width = round(width * 2) / 2
    long_delta = 0.55
    short_delta = 0.30
    entry_debit = max(0.30, min(width * 0.65, width * 0.42))
    exit_value = max(0.02, min(width, entry_debit + directional_move * (long_delta - short_delta)))
    pnl_dollars = (exit_value - entry_debit) * 100
    return {
        "spread_structure": "call_debit_spread" if direction == "bullish" else "put_debit_spread",
        "spread_width": round(width, 2),
        "entry_spread_mark": round(entry_debit, 4),
        "exit_spread_mark": round(exit_value, 4),
        "spread_pnl_dollars": round(pnl_dollars, 2),
        "spread_pnl_pct": round((exit_value - entry_debit) / entry_debit * 100, 2),
        "spread_max_loss_dollars": round(entry_debit * 100, 2),
        "spread_max_profit_dollars": round((width - entry_debit) * 100, 2),
    }


def score_trade(entry: Observation, future: list[Observation], max_hold_minutes: int) -> dict[str, Any]:
    deadline = entry.captured_at + timedelta(minutes=max_hold_minutes)
    path = [obs for obs in future if entry.captured_at < obs.captured_at <= deadline]
    exit_obs = path[-1] if path else entry
    exit_reason = "end_of_capture_path" if path else "no_future_snapshot"
    invalidation = entry.invalidation
    if invalidation is None and entry.setup == "cluster" and entry.target is not None:
        distance = abs(entry.target - entry.spot)
        invalidation = entry.spot - distance if entry.direction == "bullish" else entry.spot + distance
    for obs in path:
        if entry.target is not None and target_hit(entry.direction or "", obs.spot, entry.target):
            exit_obs = obs
            exit_reason = "target_touched_snapshot"
            break
        if invalidation is not None and invalidation_hit(entry.direction or "", obs.spot, invalidation):
            exit_obs = obs
            exit_reason = "invalidation_touched_snapshot"
            break
    if exit_reason == "end_of_capture_path" and exit_obs.captured_at >= deadline:
        exit_reason = "max_hold_reached"
    underlying_move = exit_obs.spot - entry.spot
    directional_move = underlying_move if entry.direction == "bullish" else -underlying_move
    proxy = option_proxy(entry.spot, exit_obs.spot, entry.direction or "bullish", entry.target)
    spread_proxy = debit_spread_proxy(entry.spot, exit_obs.spot, entry.direction or "bullish", entry.target)
    return {
        "entry_time": entry.captured_at.isoformat(),
        "exit_time": exit_obs.captured_at.isoformat(),
        "ticker": entry.ticker,
        "scan_type": entry.scan_type,
        "setup": entry.setup,
        "direction": entry.direction,
        "rank": entry.rank,
        "score": entry.score,
        "strength": entry.strength,
        "entry_spot": entry.spot,
        "exit_spot": exit_obs.spot,
        "target": entry.target,
        "invalidation": entry.invalidation,
        "model_invalidation": invalidation if entry.invalidation is None else None,
        "exit_reason": exit_reason,
        "underlying_move": round(underlying_move, 4),
        "directional_move": round(directional_move, 4),
        "directional_move_pct": round(directional_move / entry.spot * 100, 4),
        "win": directional_move > 0,
        **proxy,
        **spread_proxy,
        "source_file": entry.source_file,
        "option_model": "atm_debit_spread_proxy",
        "reference_option_model": "atm_long_option_delta_proxy",
    }


def aggregate(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(k) for k in keys)].append(row)
    out = []
    for key, items in groups.items():
        pnl = [float(item.get("spread_pnl_dollars", item["option_pnl_dollars"])) for item in items]
        pct = [float(item.get("spread_pnl_pct", item["option_pnl_pct"])) for item in items]
        wins = sum(1 for item in items if item["win"])
        out.append({
            **{keys[idx]: key[idx] for idx in range(len(keys))},
            "trades": len(items),
            "wins": wins,
            "win_rate": round(wins / len(items) * 100, 2) if items else 0,
            "total_spread_pnl_dollars": round(sum(pnl), 2),
            "average_spread_pnl_pct": round(sum(pct) / len(pct), 2) if pct else 0,
            "total_option_pnl_dollars": round(sum(float(item["option_pnl_dollars"]) for item in items), 2),
            "average_option_pnl_pct": round(sum(float(item["option_pnl_pct"]) for item in items) / len(items), 2) if items else 0,
        })
    return sorted(out, key=lambda row: (row["total_spread_pnl_dollars"], row["win_rate"]), reverse=True)


def run_backtest(root: Path, trade_date: date, cooldown_minutes: int, max_hold_minutes: int) -> dict[str, Any]:
    observations = load_observations(root, trade_date)
    by_ticker: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        by_ticker[obs.ticker].append(obs)
    entries = dedupe_entries(observations, cooldown_minutes)
    trades = [score_trade(entry, by_ticker[entry.ticker], max_hold_minutes) for entry in entries]
    skipped = Counter()
    for obs in observations:
        if obs.direction not in {"bullish", "bearish"}:
            skipped["missing_direction"] += 1
        elif obs.target is None:
            skipped["missing_target"] += 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capture_root": str(root),
        "trade_date": trade_date.isoformat(),
        "mode": "capture_replay_debit_spread_proxy",
        "inputs": {
            "observations": len(observations),
            "deduped_entries": len(entries),
            "dedupe_mode": "flash_and_flash_agentic_core_strategy_changes_only",
            "cluster_filter": "quad_or_rank_top_5",
            "primary_instrument_model": "atm_debit_spread_proxy",
            "reference_instrument_model": "atm_long_option_delta_proxy",
            "cooldown_minutes": cooldown_minutes,
            "max_hold_minutes": max_hold_minutes,
            "skipped_raw_observations": dict(skipped),
        },
        "summary": aggregate(trades, ["scan_type"]),
        "summary_by_pattern": aggregate(trades, ["scan_type", "setup", "direction"]),
        "summary_by_ticker": aggregate(trades, ["ticker"]),
        "trades": trades,
        "caveats": [
            "Uses captured underlying spot snapshots, not full intraday bars; target/invalidation hits between captures are unknowable.",
            "Primary P/L uses an ATM debit-spread proxy; long-option P/L is retained as a reference because no historical option mark archive was found under CipherCapture.",
            "Cluster rows usually do not include invalidation; the model uses a symmetric adverse move equal to target distance for cluster stop modelling.",
            "This is research only and does not place, route, or recommend live orders.",
        ],
    }


def write_outputs(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"today_options_capture_backtest_{stamp}.json"
    csv_path = out_dir / f"today_options_capture_backtest_{stamp}.csv"
    md_path = out_dir / f"today_options_capture_backtest_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    fields = sorted({key for row in report["trades"] for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["trades"])
    lines = [
        "# Today Options Capture Backtest",
        "",
        f"Generated: {report['generated_at']}",
        f"Trade date: {report['trade_date']}",
        f"Mode: `{report['mode']}`",
        "",
        "## Summary By Scan",
        "",
        "| Scan | Trades | Win Rate | Total Spread P/L | Avg Spread P/L % | Long Option Ref P/L |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["summary"]:
        lines.append(
            f"| {row['scan_type']} | {row['trades']} | {row['win_rate']} | "
            f"{row['total_spread_pnl_dollars']} | {row['average_spread_pnl_pct']} | "
            f"{row['total_option_pnl_dollars']} |"
        )
    lines += [
        "",
        "## Top Patterns",
        "",
        "| Scan | Setup | Direction | Trades | Win Rate | Total Spread P/L | Avg Spread P/L % | Long Option Ref P/L |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["summary_by_pattern"][:25]:
        lines.append(
            f"| {row['scan_type']} | {row['setup']} | {row['direction']} | {row['trades']} | "
            f"{row['win_rate']} | {row['total_spread_pnl_dollars']} | {row['average_spread_pnl_pct']} | "
            f"{row['total_option_pnl_dollars']} |"
        )
    lines += ["", "## Caveats", ""]
    lines.extend(f"- {caveat}" for caveat in report["caveats"])
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest today's Cipher captures with an options proxy model.")
    parser.add_argument("--root", default=r"C:\Aarav\cipher-system\CipherCapture")
    parser.add_argument("--date", default="today")
    parser.add_argument("--cooldown-minutes", type=int, default=10)
    parser.add_argument("--max-hold-minutes", type=int, default=45)
    parser.add_argument("--out-dir", default=r"C:\Aarav\cipher-system\CipherCapture\data\backtests")
    args = parser.parse_args()
    root = Path(args.root)
    trade_date = intraday_capture_date(root, args.date)
    report = run_backtest(root, trade_date, args.cooldown_minutes, args.max_hold_minutes)
    paths = write_outputs(report, Path(args.out_dir))
    print(json.dumps({
        "generated_at": report["generated_at"],
        "trade_date": report["trade_date"],
        "inputs": report["inputs"],
        "summary": report["summary"],
        "top_patterns": report["summary_by_pattern"][:10],
        "paths": paths,
        "caveats": report["caveats"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
