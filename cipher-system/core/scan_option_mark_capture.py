"""Capture scan-time option marks for cluster setups.

This turns a scanner snapshot into auditable option-entry marks while the setup
is still fresh. It only calls Tradier market-data quote endpoints; no account or
order endpoints are used.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from first_quad_outcome import mid, occ_symbol, quote_width_pct, tradier_quotes


ROOT = Path(__file__).resolve().parents[1]
SCAN_DIR = ROOT / "data" / "accessobsidian_scans"
OUT_DIR = ROOT / "data" / "scan_option_marks"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_scan_run(scan_dir: Path) -> Path:
    summaries = sorted(scan_dir.glob("20*/20*/summary.json"), reverse=True)
    for summary in summaries:
        cluster = summary.parent / "cluster.json"
        if not cluster.is_file():
            continue
        try:
            rows = load_json(cluster).get("rows") or []
        except (OSError, json.JSONDecodeError):
            continue
        if rows:
            return summary.parent
    raise FileNotFoundError(f"No usable cluster scan found under {scan_dir}")


def infer_direction(row: dict[str, Any]) -> str:
    raw = str(row.get("setup") or "").upper()
    if "DOWNSIDE" in raw:
        return "down"
    if "UPSIDE" in raw:
        return "up"
    spot = num(row.get("spot"))
    target = num(row.get("cluster_target"))
    if spot is not None and target is not None and target < spot:
        return "down"
    return "up"


def default_expiry() -> str:
    # Day-trade/weekly research default: next calendar day, with Friday held
    # through Monday if this is run after a Friday scan.
    today = datetime.now().astimezone().date()
    days = 3 if today.weekday() == 4 else 1
    return (today + timedelta(days=days)).isoformat()


def cluster_levels(row: dict[str, Any]) -> list[float]:
    levels = sorted({float(level["strike"]) for level in row.get("levels") or [] if num(level.get("strike")) is not None})
    if levels:
        return levels
    target = num(row.get("cluster_target"))
    return [target] if target is not None else []


def strike_increment(levels: list[float]) -> float:
    diffs = sorted({round(abs(a - b), 4) for a in levels for b in levels if abs(a - b) > 0})
    return diffs[0] if diffs else 1.0


def choose_long_option(row: dict[str, Any]) -> tuple[str, float]:
    direction = infer_direction(row)
    spot = float(row["spot"])
    levels = cluster_levels(row)
    if direction == "down":
        below = [strike for strike in levels if strike <= spot]
        return "put", max(below) if below else float(row["cluster_target"])
    above = [strike for strike in levels if strike >= spot]
    return "call", min(above) if above else float(row["cluster_target"])


def choose_spread(row: dict[str, Any]) -> tuple[str, float, float]:
    direction = infer_direction(row)
    target = float(row["cluster_target"])
    levels = cluster_levels(row)
    increment = strike_increment(levels)
    if direction == "down":
        higher = [strike for strike in levels if strike > target]
        return "put", min(higher) if higher else target + increment, target
    lower = [strike for strike in levels if strike < target]
    return "call", max(lower) if lower else target - increment, target


def quote_many(symbols: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    deduped = []
    seen = set()
    for symbol in symbols:
        symbol = symbol.upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            deduped.append(symbol)
    for idx in range(0, len(deduped), 80):
        out.update(tradier_quotes(deduped[idx:idx + 80], greeks=True))
    return out


def build_report(run_dir: Path, expiry: str, limit: int) -> dict[str, Any]:
    summary = load_json(run_dir / "summary.json")
    rows = list((load_json(run_dir / "cluster.json").get("rows") or [])[:limit])
    specs = []
    symbols = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        option_type, long_strike = choose_long_option(row)
        spread_type, spread_long, spread_short = choose_spread(row)
        long_symbol = occ_symbol(ticker, expiry, option_type, long_strike)
        spread_long_symbol = occ_symbol(ticker, expiry, spread_type, spread_long)
        spread_short_symbol = occ_symbol(ticker, expiry, spread_type, spread_short)
        symbols.extend([ticker, long_symbol, spread_long_symbol, spread_short_symbol])
        specs.append({
            "row": row,
            "ticker": ticker,
            "option_type": option_type,
            "long_strike": long_strike,
            "long_symbol": long_symbol,
            "spread_type": spread_type,
            "spread_long": spread_long,
            "spread_short": spread_short,
            "spread_long_symbol": spread_long_symbol,
            "spread_short_symbol": spread_short_symbol,
        })
    quotes = quote_many(symbols)
    out_rows = []
    for spec in specs:
        row = spec["row"]
        ticker = spec["ticker"]
        long_quote = quotes.get(spec["long_symbol"]) or {}
        spread_long_quote = quotes.get(spec["spread_long_symbol"]) or {}
        spread_short_quote = quotes.get(spec["spread_short_symbol"]) or {}
        long_mark = mid(long_quote)
        spread_long_mark = mid(spread_long_quote)
        spread_short_mark = mid(spread_short_quote)
        spread_mark = None
        if spread_long_mark is not None and spread_short_mark is not None:
            spread_mark = max(spread_long_mark - spread_short_mark, 0.0)
        target = num(row.get("cluster_target"))
        spot = num(row.get("spot"))
        out_rows.append({
            "captured_at": now_utc(),
            "scan_captured_at": summary.get("captured_at"),
            "run_dir": str(run_dir),
            "ticker": ticker,
            "rank": row.get("rank"),
            "setup": row.get("setup"),
            "dte": row.get("dte"),
            "scan_spot": spot,
            "latest_spot": num((quotes.get(ticker) or {}).get("last")),
            "target": target,
            "target_distance_pct": round(abs(target - spot) / spot * 100, 3) if spot and target is not None else None,
            "long_option": f"{expiry} {spec['long_strike']:g} {spec['option_type']}",
            "long_symbol": spec["long_symbol"],
            "long_mark": round(long_mark, 4) if long_mark is not None else None,
            "long_bid": num(long_quote.get("bid")),
            "long_ask": num(long_quote.get("ask")),
            "long_width_pct": round(quote_width_pct(long_quote), 3) if quote_width_pct(long_quote) is not None else None,
            "long_volume": long_quote.get("volume"),
            "long_oi": long_quote.get("open_interest"),
            "long_delta": (long_quote.get("greeks") or {}).get("delta"),
            "spread": f"{expiry} {spec['spread_long']:g}/{spec['spread_short']:g} {spec['spread_type']} debit spread",
            "spread_long_symbol": spec["spread_long_symbol"],
            "spread_short_symbol": spec["spread_short_symbol"],
            "spread_mark": round(spread_mark, 4) if spread_mark is not None else None,
            "read_only": True,
        })
    return {
        "generated_at": now_utc(),
        "expiry": expiry,
        "source_run_dir": str(run_dir),
        "scan_captured_at": summary.get("captured_at"),
        "rows": out_rows,
        "caveat": "Scan-time option marks from Tradier market-data quotes only. No order/account endpoints used.",
    }


def write_outputs(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"scan_option_marks_{stamp}.json"
    csv_path = out_dir / f"scan_option_marks_{stamp}.csv"
    md_path = out_dir / f"scan_option_marks_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    fields = sorted({key for row in report["rows"] for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["rows"])
    lines = [
        "# Scan Option Marks",
        "",
        f"Generated: {report['generated_at']}",
        f"Scan: `{report['source_run_dir']}`",
        f"Expiry: {report['expiry']}",
        "",
        "| Rank | Ticker | Setup | Target Dist % | Long Option | Long Mark | Delta | OI | Spread | Spread Mark |",
        "|---:|---|---|---:|---|---:|---:|---:|---|---:|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row.get('rank')} | {row.get('ticker')} | {row.get('setup')} | {row.get('target_distance_pct')} | "
            f"{row.get('long_option')} | {row.get('long_mark')} | {row.get('long_delta')} | {row.get('long_oi')} | "
            f"{row.get('spread')} | {row.get('spread_mark')} |"
        )
    lines += ["", report["caveat"], ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--expiry", default=default_expiry())
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    run_dir = args.run_dir or latest_scan_run(SCAN_DIR)
    report = build_report(run_dir, args.expiry, args.limit)
    paths = write_outputs(report, args.out_dir)
    print(json.dumps({
        "generated_at": report["generated_at"],
        "scan_captured_at": report["scan_captured_at"],
        "expiry": report["expiry"],
        "paths": paths,
        "top": report["rows"][:8],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
