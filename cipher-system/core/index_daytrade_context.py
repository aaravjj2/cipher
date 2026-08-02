"""Build an index-options daytrade context report from local captures.

This is research-only. It combines public-OI GEX snapshots for SPY/QQQ/IWM with
the visible AccessObsidian scanner captures we store locally. It does not place
orders or use proprietary internals.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_GEX_DB = DATA / "gex_history.sqlite"
DEFAULT_SCAN_DIR = DATA / "accessobsidian_scans"
DEFAULT_OUT_DIR = DATA / "index_daytrade_context"
INDEX_TICKERS = ("SPY", "QQQ", "IWM")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def latest_scan_run(scan_dir: Path) -> Path | None:
    summaries = sorted(scan_dir.glob("20*/20*/summary.json"))
    return summaries[-1].parent if summaries else None


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_index_snapshot(db: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    db.row_factory = sqlite3.Row
    row = db.execute(
        """
        select *
        from gex_snapshots
        where ticker = ?
        order by captured_at desc, id desc
        limit 1
        """,
        (ticker,),
    ).fetchone()
    return dict(row) if row else None


def prior_index_snapshot(db: sqlite3.Connection, ticker: str, latest_id: int) -> dict[str, Any] | None:
    row = db.execute(
        """
        select *
        from gex_snapshots
        where ticker = ? and id < ?
        order by captured_at desc, id desc
        limit 1
        """,
        (ticker, latest_id),
    ).fetchone()
    return dict(row) if row else None


def top_cells(db: sqlite3.Connection, snapshot_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            """
            select expiration, strike, call_oi, put_oi, call_gex, put_gex, net_gex,
                   volume, call_mid, put_mid
            from gex_strike_cells
            where snapshot_id = ? and available = 1
            order by abs(net_gex) desc
            limit ?
            """,
            (snapshot_id, limit),
        )
    ]


def local_cells(db: sqlite3.Connection, snapshot_id: int, spot: float, limit: int = 10) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            """
            select expiration, strike, call_oi, put_oi, call_gex, put_gex, net_gex,
                   volume, call_mid, put_mid
            from gex_strike_cells
            where snapshot_id = ? and available = 1
            order by abs(strike - ?), expiration
            limit ?
            """,
            (snapshot_id, spot, limit),
        )
    ]


def classify_index(snapshot: dict[str, Any], prior: dict[str, Any] | None, cells: list[dict[str, Any]]) -> dict[str, Any]:
    spot = as_float(snapshot.get("spot")) or 0.0
    call_wall = as_float(snapshot.get("call_wall_strike"))
    put_wall = as_float(snapshot.get("put_wall_strike"))
    flip = as_float(snapshot.get("gamma_flip_level"))
    global_max = as_float(snapshot.get("global_max_strike"))
    net_near_spot = sum(as_float(cell.get("net_gex")) or 0.0 for cell in cells[:5])
    above_flip = bool(flip is not None and spot >= flip)
    call_dist = ((call_wall - spot) / spot * 100.0) if call_wall and spot else None
    put_dist = ((spot - put_wall) / spot * 100.0) if put_wall and spot else None
    prior_spot = as_float(prior.get("spot")) if prior else None
    spot_change = (spot - prior_spot) if prior_spot is not None else None
    prior_flip = as_float(prior.get("gamma_flip_level")) if prior else None
    flip_change = (flip - prior_flip) if flip is not None and prior_flip is not None else None
    if above_flip and net_near_spot > 0:
        regime = "pin/range-biased unless walls break"
    elif above_flip:
        regime = "above flip but local gamma mixed"
    else:
        regime = "below flip / expansion risk"
    return {
        "ticker": snapshot.get("ticker"),
        "captured_at": snapshot.get("captured_at"),
        "spot": spot,
        "spot_change_from_prior": spot_change,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "gamma_flip": flip,
        "gamma_flip_change_from_prior": flip_change,
        "global_max_strike": global_max,
        "spot_above_gamma_flip": above_flip,
        "call_wall_distance_pct": call_dist,
        "put_wall_distance_pct": put_dist,
        "near_spot_net_gex": net_near_spot,
        "regime": regime,
        "top_abs_gex_cells": cells,
    }


def load_scan_rows(run_dir: Path, name: str) -> list[dict[str, Any]]:
    path = run_dir / f"{name}.json"
    if not path.is_file():
        return []
    payload = load_json(path)
    return list(payload.get("rows") or [])


def overlap_tickers(*row_sets: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rows in row_sets:
        seen = {str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")}
        for ticker in seen:
            counts[ticker] = counts.get(ticker, 0) + 1
    return {ticker: count for ticker, count in sorted(counts.items()) if count >= 2}


def build_report(scan_dir: Path, gex_db: Path, out_dir: Path) -> dict[str, Any]:
    run_dir = latest_scan_run(scan_dir)
    if not run_dir:
        raise FileNotFoundError(f"No AccessObsidian scan summary found under {scan_dir}")
    cluster = load_scan_rows(run_dir, "cluster")
    liq = load_scan_rows(run_dir, "liq")
    model = load_scan_rows(run_dir, "cipher_model")
    new_quad_path = run_dir / "new_quad_entries.json"
    new_quads = (load_json(new_quad_path).get("new_quad_entries") if new_quad_path.is_file() else []) or []

    with sqlite3.connect(gex_db) as db:
        indices = []
        for ticker in INDEX_TICKERS:
            latest = latest_index_snapshot(db, ticker)
            if not latest:
                continue
            prior = prior_index_snapshot(db, ticker, int(latest["id"]))
            abs_cells = top_cells(db, int(latest["id"]), limit=10)
            spot = as_float(latest.get("spot")) or 0.0
            near_cells = local_cells(db, int(latest["id"]), spot, limit=10)
            classified = classify_index(latest, prior, near_cells)
            classified["top_abs_gex_cells"] = abs_cells
            classified["near_spot_cells"] = near_cells
            indices.append(classified)

    cluster_top = cluster[:15]
    quads = [row for row in cluster if "QUAD" in str(row.get("setup") or "").upper()]
    overlaps = overlap_tickers(cluster[:20], liq[:20], model[:20])
    report = {
        "generated_at": utcnow(),
        "latest_scan_run": str(run_dir),
        "latest_scan_captured_at": load_json(run_dir / "summary.json").get("captured_at"),
        "index_context": indices,
        "cluster_top_15": cluster_top,
        "current_quads": quads,
        "new_quads": new_quads,
        "cross_scan_overlaps": overlaps,
        "daytrade_playbook": {
            "index_first_rule": "Use SPY/QQQ/IWM regime as the market tape filter before trading single-name options.",
            "range_play": "If spot is above gamma flip and trapped between nearby put/call walls, prefer quick mean-reversion or premium-defined structures; avoid chasing into a wall.",
            "breakout_play": "If price accepts beyond a call wall/put wall with breadth and scanner names aligned, treat the next high-GEX strike as the next target/risk reference.",
            "quad_focus": "New or repeated QUAD entries are the priority watchlist; repeated quads carry more weight than one-off appearances.",
            "confirmation": "Best candidates have cluster direction aligned with Liq runway or Cipher Model bias, plus index ETF context in the same direction.",
            "risk_boundary": "Use wall/flip invalidation levels as research stop references; this app remains read-only and does not submit orders.",
        },
    }
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"index_daytrade_context_{stamp}.json"
    md_path = out_dir / f"index_daytrade_context_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    report["paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Index Daytrade Context",
        "",
        f"Generated: {report['generated_at']}",
        f"Scanner run: `{report['latest_scan_run']}`",
        "",
        "## Index ETF Context",
        "",
        "| ETF | Spot | Flip | Call Wall | Put Wall | Regime |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in report["index_context"]:
        lines.append(
            f"| {item['ticker']} | {fmt(item['spot'])} | {fmt(item['gamma_flip'])} | "
            f"{fmt(item['call_wall'])} | {fmt(item['put_wall'])} | {item['regime']} |"
        )
    lines += ["", "## Current Quad Watchlist", "", "| Ticker | Setup | Target | Strength | Seen |", "|---|---|---:|---:|---:|"]
    for row in report["current_quads"]:
        lines.append(
            f"| {row.get('ticker')} | {row.get('setup')} | {fmt(row.get('cluster_target'))} | "
            f"{fmt(row.get('strength'), 0)} | {fmt(row.get('seen_count'), 0)} |"
        )
    lines += ["", "## New Quad Entries", "", "| Ticker | Setup | Target | Strength |", "|---|---|---:|---:|"]
    for row in report["new_quads"]:
        lines.append(
            f"| {row.get('ticker')} | {row.get('setup')} | {fmt(row.get('latest_cluster_target'))} | "
            f"{fmt(row.get('latest_strength'), 0)} |"
        )
    lines += ["", "## Cross-Scan Overlaps", ""]
    if report["cross_scan_overlaps"]:
        lines.append(", ".join(f"{ticker} ({count})" for ticker, count in report["cross_scan_overlaps"].items()))
    else:
        lines.append("No top-20 overlaps in the latest run.")
    lines += [
        "",
        "## Playbook",
        "",
    ]
    for key, value in report["daytrade_playbook"].items():
        lines.append(f"- **{key.replace('_', ' ').title()}**: {value}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an index daytrade context report from local captures.")
    parser.add_argument("--scan-dir", type=Path, default=DEFAULT_SCAN_DIR)
    parser.add_argument("--gex-db", type=Path, default=DEFAULT_GEX_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    report = build_report(args.scan_dir, args.gex_db, args.out_dir)
    print(json.dumps({
        "generated_at": report["generated_at"],
        "latest_scan_run": report["latest_scan_run"],
        "paths": report["paths"],
        "index_context": [
            {
                "ticker": row["ticker"],
                "spot": row["spot"],
                "gamma_flip": row["gamma_flip"],
                "call_wall": row["call_wall"],
                "put_wall": row["put_wall"],
                "regime": row["regime"],
            }
            for row in report["index_context"]
        ],
        "new_quads": [
            {
                "ticker": row.get("ticker"),
                "setup": row.get("setup"),
                "target": row.get("latest_cluster_target"),
                "strength": row.get("latest_strength"),
            }
            for row in report["new_quads"]
        ],
        "overlaps": report["cross_scan_overlaps"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
