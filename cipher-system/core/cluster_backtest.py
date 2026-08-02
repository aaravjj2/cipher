"""Cluster accuracy backtesting.

Evaluates Setup Scanner cluster levels against subsequent daily bars.

Modes
-----
1. **snapshot + forward** — save live cluster picks, later score whether price
   touched the level within `horizon` trading days after `as_of`.
2. **magnetism (retro)** — with today's cluster levels, measure how often the
   last N daily ranges already interacted with those levels (sticky-level proxy;
   not a true historical GEX replay — historical OPRA chains are unavailable).

Forward auto-rescore
--------------------
`rescore_due` walks saved snapshots and scores any aged enough for horizons
1 / 3 / 5 when post-`as_of` daily bars exist. Reports land under
`data/backtests/forward/` with an explicit `mode` of `forward` (or `magnetism`
only when insufficient forward bars remain).

GEX/cluster structure is a public-OI heuristic — not verified dealer positioning.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
BT_DIR = ROOT / "data" / "backtests"
SNAP_DIR = BT_DIR / "cluster_snapshots"
FORWARD_DIR = BT_DIR / "forward"
MATRIX_DAILY_DIR = BT_DIR / "matrix_daily"

CLUSTER_KINDS = ("quad", "triple", "battle", "golden", "call_wall", "put_floor")
FORWARD_HORIZONS = (1, 3, 5)

# Universe discipline — skip thin options chains at capture time.
DEFAULT_MIN_CONTRACTS = 500
DEFAULT_MIN_COVERAGE_CELLS = 20

# Retention caps (newest kept).
MAX_SNAPSHOTS = 50
MAX_FORWARD_REPORTS = 100
MAX_CLUSTER_REPORTS = 100


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs():
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    BT_DIR.mkdir(parents=True, exist_ok=True)
    FORWARD_DIR.mkdir(parents=True, exist_ok=True)
    MATRIX_DAILY_DIR.mkdir(parents=True, exist_ok=True)


def _bar_day(bar: dict) -> str:
    t = bar.get("time") or ""
    return str(t)[:10]


def _parse_kinds(kind: str | Iterable[str] | None) -> set[str] | None:
    """Return allowed kinds, or None for all."""
    if kind is None or kind == "" or kind == "*":
        return None
    if isinstance(kind, str):
        parts = [p.strip().lower() for p in kind.replace(";", ",").split(",") if p.strip()]
    else:
        parts = [str(p).strip().lower() for p in kind if str(p).strip()]
    return set(parts) if parts else None


def _prune_dir(directory: Path, pattern: str, keep: int) -> int:
    """Delete oldest matching files beyond `keep`. Returns count removed."""
    ensure_dirs()
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for path in files[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def prune_retention(
    *,
    max_snapshots: int = MAX_SNAPSHOTS,
    max_forward: int = MAX_FORWARD_REPORTS,
    max_reports: int = MAX_CLUSTER_REPORTS,
) -> dict:
    """Cap snapshot / forward / cluster report files to newest N."""
    return {
        "snapshots_pruned": _prune_dir(SNAP_DIR, "cluster_*.json", max_snapshots),
        "forward_pruned": _prune_dir(FORWARD_DIR, "forward_*.json", max_forward),
        "reports_pruned": _prune_dir(BT_DIR, "cluster_report_*.json", max_reports),
    }


def _depth_ok(
    row: dict,
    *,
    min_contracts: int = DEFAULT_MIN_CONTRACTS,
    min_coverage_cells: int = DEFAULT_MIN_COVERAGE_CELLS,
) -> tuple[bool, dict]:
    """Universe discipline: require enough option contracts / coverage cells."""
    contracts = row.get("contracts")
    cells = row.get("coverage_cells")
    try:
        contracts_n = int(contracts) if contracts is not None else None
    except (TypeError, ValueError):
        contracts_n = None
    try:
        cells_n = int(cells) if cells is not None else None
    except (TypeError, ValueError):
        cells_n = None

    reasons = []
    ok = True
    if contracts_n is not None and contracts_n < min_contracts:
        ok = False
        reasons.append(f"contracts={contracts_n}<{min_contracts}")
    if cells_n is not None and cells_n < min_coverage_cells:
        ok = False
        reasons.append(f"coverage_cells={cells_n}<{min_coverage_cells}")
    # If neither field present, allow but flag unknown depth.
    if contracts_n is None and cells_n is None:
        reasons.append("depth_unknown")

    meta = {
        "contracts": contracts_n,
        "coverage_cells": cells_n,
        "min_contracts": min_contracts,
        "min_coverage_cells": min_coverage_cells,
        "ok": ok,
        "reasons": reasons,
    }
    return ok, meta


def _slim_peaks_setups(row: dict) -> dict:
    """Minimal ticker payload for walk-forward matrix_daily dump."""
    setups = []
    for s in row.get("setups") or []:
        setups.append(
            {
                "kind": s.get("kind"),
                "center": s.get("center"),
                "low": s.get("low"),
                "high": s.get("high"),
                "side": s.get("side"),
                "peak_count": s.get("peak_count"),
            }
        )
    cluster = row.get("cluster") or {}
    return {
        "spot": row.get("spot"),
        "direction": row.get("direction"),
        "score": row.get("score"),
        "pull_target": row.get("pull_target"),
        "close_under": row.get("close_under"),
        "reclaim": row.get("reclaim"),
        "call_wall": row.get("call_wall"),
        "put_wall": row.get("put_wall"),
        "contracts": row.get("contracts"),
        "coverage_cells": row.get("coverage_cells"),
        "cluster_kind": cluster.get("kind"),
        "cluster_center": cluster.get("center"),
        "setups": setups,
    }


def merge_matrix_daily(as_of_day: str, ticker_map: dict[str, dict]) -> Path:
    """Append/merge slim ticker→peaks/setups into matrix_daily/YYYY-MM-DD.json."""
    ensure_dirs()
    path = MATRIX_DAILY_DIR / f"{as_of_day}.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    tickers = dict(existing.get("tickers") or {})
    tickers.update(ticker_map)
    payload = {
        "date": as_of_day,
        "updated_at": _utcnow(),
        "n": len(tickers),
        "tickers": tickers,
        "caveat": (
            "Slim daily cluster snapshot for walk-forward classify prep. "
            "Not full GEX history; public-OI heuristic only."
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _invalidation_level(pick: dict, setup: dict) -> float | None:
    """Best-effort invalidation from pick/setup fields.

    Prefer close_under / reclaim on the pick; else opposite wall (put_floor for
    above-side setups, call_wall for below-side).
    """
    for key in ("invalidation", "close_under", "reclaim"):
        val = pick.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        val = setup.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    side = setup.get("side")
    if side == "above":
        for key in ("put_wall", "put_floor", "first_support"):
            val = pick.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    elif side == "below":
        for key in ("call_wall", "first_resistance"):
            val = pick.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return None


def _touch(
    level: float,
    bars: list[dict],
    *,
    spot: float | None = None,
    invalidation: float | None = None,
    tol_pct: float = 0.0025,
) -> dict:
    """Did any bar's range touch `level` (with small tolerance)?

    Also returns first_touch_direction (toward/away vs spot), time_to_touch_days,
    mfe/mae, and fail_if_invalidation (invalidation touched before level).
    """
    empty = {
        "hit": False,
        "hit_day": None,
        "time_to_touch_days": None,
        "first_touch_direction": None,
        "mfe_pct": None,
        "mae_pct": None,
        "fail_if_invalidation": None,
        "invalidation": invalidation,
    }
    if not bars or level is None:
        return empty
    first = bars[0]
    entry = spot if spot is not None else (first.get("open") or first.get("close"))
    if not entry:
        return empty
    tol = abs(level) * tol_pct
    hit_day = None
    time_to_touch = None
    first_touch_direction = None
    mfe = 0.0
    mae = 0.0
    toward_sign = 1.0 if level >= entry else -1.0

    for i, bar in enumerate(bars):
        hi = bar.get("high")
        lo = bar.get("low")
        cl = bar.get("close")
        if hi is None or lo is None:
            continue

        if lo - tol <= level <= hi + tol and hit_day is None:
            hit_day = _bar_day(bar)
            time_to_touch = i + 1  # 1-indexed trading sessions
            mid = (hi + lo) / 2.0
            moved = mid - entry
            first_touch_direction = "toward" if moved * toward_sign >= 0 else "away"

        if cl is not None and entry:
            move = (cl - entry) / entry
            toward = (level - entry) / entry
            aligned = move * toward
            mfe = max(mfe, aligned)
            mae = min(mae, aligned)

    fail_inv = None
    if invalidation is not None:
        fail_inv = _invalidation_before_hit(level, bars, invalidation, tol_pct)

    return {
        "hit": hit_day is not None,
        "hit_day": hit_day,
        "time_to_touch_days": time_to_touch,
        "first_touch_direction": first_touch_direction,
        "mfe_pct": round(mfe * 100, 3),
        "mae_pct": round(mae * 100, 3),
        "fail_if_invalidation": fail_inv,
        "invalidation": invalidation,
    }


def _invalidation_before_hit(
    level: float,
    bars: list[dict],
    invalidation: float,
    tol_pct: float,
) -> bool:
    """True only when invalidation is touched on an earlier bar than the level."""
    tol = abs(level) * tol_pct
    inv_tol = abs(invalidation) * tol_pct
    inv_i = None
    hit_i = None
    for i, bar in enumerate(bars):
        hi = bar.get("high")
        lo = bar.get("low")
        if hi is None or lo is None:
            continue
        if inv_i is None and lo - inv_tol <= invalidation <= hi + inv_tol:
            inv_i = i
        if hit_i is None and lo - tol <= level <= hi + tol:
            hit_i = i
        if inv_i is not None and hit_i is not None:
            break
    if inv_i is None:
        return False
    if hit_i is None:
        return True
    return inv_i < hit_i


def _direction_agree(spot: float, level: float, bars: list[dict]) -> bool | None:
    if not bars or spot is None or level is None:
        return None
    last = bars[-1].get("close")
    if last is None:
        return None
    want_up = level >= spot
    moved_up = last >= spot
    return want_up == moved_up


def _forward_bars(bars: list[dict], as_of: str, horizon: int) -> list[dict]:
    return [b for b in bars if _bar_day(b) > as_of][:horizon]


def _score_mode(forward: list[dict], horizon: int) -> str:
    """Explicit scoring mode label."""
    if len(forward) >= horizon:
        return "forward"
    if forward:
        return "forward"  # partial forward still counts as forward
    return "magnetism"


def capture_cluster_snapshot(
    matrix_fn,
    tickers,
    *,
    feed="opra",
    mode="short",
    cluster_exp="nearest",
    limit=40,
    kind=None,
    min_contracts: int = DEFAULT_MIN_CONTRACTS,
    min_coverage_cells: int = DEFAULT_MIN_COVERAGE_CELLS,
    skip_weak_depth: bool = True,
    dump_matrix_daily: bool = True,
) -> dict:
    """Run cluster analysis and persist a labeled snapshot for later forward scoring."""
    from scanner import analyze_ticker, build_cluster_groups

    ensure_dirs()
    kinds_filter = _parse_kinds(kind)
    picks = []
    skipped = []
    errors = []
    matrix_day: dict[str, dict] = {}

    for ticker in tickers:
        try:
            row = analyze_ticker(matrix_fn, ticker, feed, mode, "cluster", cluster_exp=cluster_exp)
            if not row or not row.get("setups"):
                continue

            ok, depth = _depth_ok(
                row,
                min_contracts=min_contracts,
                min_coverage_cells=min_coverage_cells,
            )
            if not ok and skip_weak_depth:
                skipped.append({"ticker": ticker, "reason": "weak_options_depth", "depth": depth})
                continue

            setups = row.get("setups") or []
            if kinds_filter:
                setups = [s for s in setups if (s.get("kind") or "").lower() in kinds_filter]
                if not setups:
                    continue

            pick = {
                "ticker": row["ticker"],
                "spot": row.get("spot"),
                "score": row.get("score"),
                "direction": row.get("direction"),
                "setups": setups,
                "cluster": row.get("cluster"),
                "pull_target": row.get("pull_target"),
                "close_under": row.get("close_under"),
                "reclaim": row.get("reclaim"),
                "call_wall": row.get("call_wall"),
                "put_wall": row.get("put_wall"),
                "first_support": row.get("first_support"),
                "first_resistance": row.get("first_resistance"),
                "contracts": row.get("contracts"),
                "coverage_cells": row.get("coverage_cells"),
                "depth": depth,
                "as_of": _utcnow(),
            }
            if not ok:
                pick["weak_depth"] = True
            picks.append(pick)
            matrix_day[ticker] = _slim_peaks_setups({**row, "setups": setups})
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
        if len(picks) >= limit:
            break

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    as_of = _utcnow()
    as_of_day = as_of[:10]
    path = SNAP_DIR / f"cluster_{stamp}.json"
    matrix_daily_path = None
    if dump_matrix_daily and matrix_day:
        matrix_daily_path = str(merge_matrix_daily(as_of_day, matrix_day))

    payload = {
        "as_of": as_of,
        "mode": mode,
        "feed": feed,
        "cluster_exp": cluster_exp,
        "kind_filter": sorted(kinds_filter) if kinds_filter else None,
        "n": len(picks),
        "picks": picks,
        "clusters": build_cluster_groups(picks),
        "skipped_weak_depth": skipped[:50],
        "universe_discipline": {
            "min_contracts": min_contracts,
            "min_coverage_cells": min_coverage_cells,
            "skip_weak_depth": skip_weak_depth,
            "skipped_n": len(skipped),
        },
        "matrix_daily_path": matrix_daily_path,
        "errors": errors[:30],
        "caveat": (
            "Cluster levels from public-OI GEX heuristic. Snapshot for forward hit-rate scoring — "
            "not proprietary Cipher and not verified dealer positioning. "
            f"Universe discipline: skip/flag tickers with contracts<{min_contracts} or "
            f"coverage_cells<{min_coverage_cells}."
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    prune_retention()
    payload["path"] = str(path)
    payload["retention"] = {
        "max_snapshots": MAX_SNAPSHOTS,
        "max_forward_reports": MAX_FORWARD_REPORTS,
    }
    return payload


def ingest_scan_snapshot(
    picks: list,
    *,
    mode="short",
    feed="opra",
    cluster_exp="nearest",
    meta=None,
) -> dict:
    """Persist Setup Scanner cluster picks as a snapshot without re-scanning."""
    from scanner import build_cluster_groups

    ensure_dirs()
    cleaned = []
    for raw in picks or []:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").upper().strip()
        setups = raw.get("setups") or []
        if not ticker or not setups:
            continue
        cleaned.append(
            {
                "ticker": ticker,
                "spot": raw.get("spot"),
                "score": raw.get("score"),
                "direction": raw.get("direction"),
                "setups": setups,
                "cluster": raw.get("cluster"),
                "pull_target": raw.get("pull_target"),
                "close_under": raw.get("close_under"),
                "reclaim": raw.get("reclaim"),
                "call_wall": raw.get("call_wall"),
                "put_wall": raw.get("put_wall"),
                "first_support": raw.get("first_support"),
                "first_resistance": raw.get("first_resistance"),
                "contracts": raw.get("contracts"),
                "coverage_cells": raw.get("coverage_cells"),
                "as_of": raw.get("as_of") or _utcnow(),
            }
        )
    if not cleaned:
        return {"error": "No cluster picks with setups to ingest", "n": 0}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    as_of = _utcnow()
    as_of_day = as_of[:10]
    path = SNAP_DIR / f"cluster_{stamp}.json"
    matrix_day = {p["ticker"]: _slim_peaks_setups(p) for p in cleaned}
    matrix_daily_path = str(merge_matrix_daily(as_of_day, matrix_day)) if matrix_day else None
    payload = {
        "as_of": as_of,
        "mode": mode,
        "feed": feed,
        "cluster_exp": cluster_exp,
        "source": "scanner_ingest",
        "n": len(cleaned),
        "picks": cleaned,
        "clusters": build_cluster_groups(cleaned),
        "meta": meta or {},
        "matrix_daily_path": matrix_daily_path,
        "caveat": (
            "Ingested from Setup Scanner cluster results (public-OI GEX heuristic). "
            "Snapshot for forward hit-rate scoring — not proprietary Cipher and not verified dealer positioning."
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    prune_retention()
    payload["path"] = str(path)
    return payload


def _init_kind_bucket() -> dict:
    return {
        "n": 0,
        "hits": 0,
        "dir_agree": 0,
        "dir_n": 0,
        "toward": 0,
        "away": 0,
        "touch_days": [],
        "mfe": [],
        "mae": [],
        "fail_inv": 0,
        "fail_inv_n": 0,
        "forward_n": 0,
        "magnetism_n": 0,
    }


def _summarize_kinds(by_kind: dict) -> dict:
    summary = {}
    for kind, b in by_kind.items():
        touch_days = b["touch_days"]
        mfe = b["mfe"]
        mae = b["mae"]
        toward_n = b["toward"] + b["away"]
        summary[kind] = {
            "n": b["n"],
            "hit_rate": round(b["hits"] / b["n"], 4) if b["n"] else None,
            "direction_agree_rate": round(b["dir_agree"] / b["dir_n"], 4) if b["dir_n"] else None,
            "toward_rate": round(b["toward"] / toward_n, 4) if toward_n else None,
            "away_rate": round(b["away"] / toward_n, 4) if toward_n else None,
            "avg_time_to_touch_days": round(sum(touch_days) / len(touch_days), 3) if touch_days else None,
            "avg_mfe_pct": round(sum(mfe) / len(mfe), 3) if mfe else None,
            "avg_mae_pct": round(sum(mae) / len(mae), 3) if mae else None,
            "fail_if_invalidation_rate": (
                round(b["fail_inv"] / b["fail_inv_n"], 4) if b["fail_inv_n"] else None
            ),
            "forward_n": b["forward_n"],
            "magnetism_n": b["magnetism_n"],
        }
    return summary


def score_snapshot(
    bars_fn,
    snapshot: dict,
    *,
    horizon=5,
    tol_pct=0.0025,
    kind=None,
    require_forward: bool = False,
    write: bool = True,
    out_dir: Path | None = None,
    report_prefix: str = "cluster_report",
) -> dict:
    """Score a saved snapshot against bars after its as_of date.

    When `require_forward` is True, skip setups that lack enough post-as_of bars
    (used by rescore-due). Otherwise falls back to magnetism on recent bars.
    """
    as_of = str(snapshot.get("as_of") or "")[:10]
    kinds_filter = _parse_kinds(kind)
    results = []
    by_kind = defaultdict(_init_kind_bucket)
    mode_counts = {"forward": 0, "magnetism": 0}
    skipped_insufficient = 0

    for pick in snapshot.get("picks") or []:
        ticker = pick["ticker"]
        spot = pick.get("spot")
        try:
            bar_payload = bars_fn(ticker, "1d", max(horizon + 40, 60))
            bars = bar_payload.get("bars") or []
        except Exception as exc:
            results.append({"ticker": ticker, "error": str(exc)})
            continue

        forward = _forward_bars(bars, as_of, horizon)
        if require_forward and len(forward) < horizon:
            skipped_insufficient += 1
            continue

        score_mode = _score_mode(forward, horizon)
        if require_forward:
            score_mode = "forward"
            eval_bars = forward[:horizon]
        else:
            eval_bars = forward if forward else bars[-horizon:]
            score_mode = "forward" if forward else "magnetism"

        for setup in pick.get("setups") or []:
            kind_name = (setup.get("kind") or "unknown").lower()
            if kinds_filter and kind_name not in kinds_filter:
                continue
            level = setup.get("center") or setup.get("high") or setup.get("low")
            inv = _invalidation_level(pick, setup)
            touch = _touch(level, eval_bars, spot=spot, invalidation=inv, tol_pct=tol_pct)
            agree = _direction_agree(spot, level, eval_bars)
            row = {
                "ticker": ticker,
                "kind": kind_name,
                "level": level,
                "spot": spot,
                "mode": score_mode,
                "horizon": horizon,
                "hit": touch["hit"],
                "hit_day": touch["hit_day"],
                "first_touch_direction": touch["first_touch_direction"],
                "time_to_touch_days": touch["time_to_touch_days"],
                "mfe_pct": touch["mfe_pct"],
                "mae_pct": touch["mae_pct"],
                "fail_if_invalidation": touch["fail_if_invalidation"],
                "invalidation": touch["invalidation"],
                "direction_agree": agree,
            }
            results.append(row)
            mode_counts[score_mode] = mode_counts.get(score_mode, 0) + 1

            bucket = by_kind[kind_name]
            bucket["n"] += 1
            if score_mode == "forward":
                bucket["forward_n"] += 1
            else:
                bucket["magnetism_n"] += 1
            if touch["hit"]:
                bucket["hits"] += 1
            if agree is not None:
                bucket["dir_n"] += 1
                if agree:
                    bucket["dir_agree"] += 1
            if touch["first_touch_direction"] == "toward":
                bucket["toward"] += 1
            elif touch["first_touch_direction"] == "away":
                bucket["away"] += 1
            if touch["time_to_touch_days"] is not None:
                bucket["touch_days"].append(touch["time_to_touch_days"])
            if touch["mfe_pct"] is not None:
                bucket["mfe"].append(touch["mfe_pct"])
            if touch["mae_pct"] is not None:
                bucket["mae"].append(touch["mae_pct"])
            if touch["fail_if_invalidation"] is not None:
                bucket["fail_inv_n"] += 1
                if touch["fail_if_invalidation"]:
                    bucket["fail_inv"] += 1

    summary = _summarize_kinds(by_kind)
    total_n = sum(b["n"] for b in by_kind.values())
    total_hits = sum(b["hits"] for b in by_kind.values())
    # Dominant mode for the report
    if mode_counts.get("forward", 0) and not mode_counts.get("magnetism", 0):
        report_mode = "forward"
    elif mode_counts.get("magnetism", 0) and not mode_counts.get("forward", 0):
        report_mode = "magnetism"
    elif mode_counts.get("forward", 0) >= mode_counts.get("magnetism", 0):
        report_mode = "forward"
    else:
        report_mode = "magnetism"

    report = {
        "as_of": _utcnow(),
        "snapshot_as_of": snapshot.get("as_of"),
        "mode": report_mode,
        "mode_counts": mode_counts,
        "horizon": horizon,
        "kind_filter": sorted(kinds_filter) if kinds_filter else None,
        "n_setups": total_n,
        "overall_hit_rate": round(total_hits / total_n, 4) if total_n else None,
        "by_kind": summary,
        "results": results[:200],
        "skipped_insufficient_forward": skipped_insufficient,
        "universe_discipline": snapshot.get("universe_discipline"),
        "caveat": (
            "Hit = daily range touched cluster level within horizon (±0.25% tol). "
            "mode=forward uses post-as_of sessions; mode=magnetism uses the last N sessions "
            "(sticky-level proxy, not true historical GEX replay — no historical OPRA GEX). "
            "first_touch_direction / mfe / mae / fail_if_invalidation are best-effort from "
            "daily OHLC and pick close_under/reclaim/opposite-wall fields. "
            "GEX is a public-OI heuristic, not verified dealer positioning."
        ),
    }

    if write:
        ensure_dirs()
        dest = out_dir or BT_DIR
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{report_prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["path"] = str(out)
        prune_retention()
    return report


def snapshot_forward_ready(bars_fn, snapshot: dict, horizon: int) -> bool:
    """True if at least one pick has `horizon` daily bars after snapshot as_of."""
    as_of = str(snapshot.get("as_of") or "")[:10]
    for pick in snapshot.get("picks") or []:
        ticker = pick.get("ticker")
        if not ticker:
            continue
        try:
            bar_payload = bars_fn(ticker, "1d", max(horizon + 40, 60))
            bars = bar_payload.get("bars") or []
        except Exception:
            continue
        if len(_forward_bars(bars, as_of, horizon)) >= horizon:
            return True
    return False


def rescore_due(
    bars_fn,
    *,
    horizons: Iterable[int] = FORWARD_HORIZONS,
    kind=None,
    tol_pct: float = 0.0025,
    limit_snapshots: int | None = None,
) -> dict:
    """Walk cluster_snapshots/; score any aged enough for horizons 1/3/5.

    Writes reports under data/backtests/forward/ with mode=forward.
    """
    ensure_dirs()
    horizons = tuple(sorted({int(h) for h in horizons if int(h) > 0}))
    files = sorted(SNAP_DIR.glob("cluster_*.json"), reverse=True)
    if limit_snapshots:
        files = files[:limit_snapshots]

    reports = []
    skipped = []
    for path in files:
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped.append({"path": str(path), "error": str(exc)})
            continue

        for horizon in horizons:
            if not snapshot_forward_ready(bars_fn, snap, horizon):
                skipped.append(
                    {
                        "path": str(path),
                        "snapshot_as_of": snap.get("as_of"),
                        "horizon": horizon,
                        "reason": "insufficient_forward_bars",
                    }
                )
                continue

            stamp = path.stem.replace("cluster_", "")
            # Avoid duplicate rescored files for same snap+horizon if already present
            existing = list(FORWARD_DIR.glob(f"forward_h{horizon}_{stamp}_*.json"))
            if existing:
                skipped.append(
                    {
                        "path": str(path),
                        "horizon": horizon,
                        "reason": "already_scored",
                        "existing": str(existing[0]),
                    }
                )
                continue

            report = score_snapshot(
                bars_fn,
                snap,
                horizon=horizon,
                tol_pct=tol_pct,
                kind=kind,
                require_forward=True,
                write=True,
                out_dir=FORWARD_DIR,
                report_prefix=f"forward_h{horizon}_{stamp}",
            )
            report["snapshot_path"] = str(path)
            report["mode"] = "forward"
            reports.append(
                {
                    "path": report.get("path"),
                    "snapshot_path": str(path),
                    "snapshot_as_of": snap.get("as_of"),
                    "horizon": horizon,
                    "mode": "forward",
                    "n_setups": report.get("n_setups"),
                    "overall_hit_rate": report.get("overall_hit_rate"),
                    "by_kind": report.get("by_kind"),
                }
            )

    prune_retention()
    return {
        "as_of": _utcnow(),
        "mode": "forward",
        "horizons": list(horizons),
        "kind_filter": sorted(_parse_kinds(kind) or []) or None,
        "n_reports": len(reports),
        "reports": reports,
        "skipped": skipped[:100],
        "caveat": (
            "Forward auto-rescore only. Requires post-as_of daily bars for each horizon. "
            "No historical OPRA GEX replay — levels come from the live snapshot at capture time. "
            "GEX is a public-OI heuristic, not verified dealer positioning."
        ),
    }


def list_snapshots(limit=20) -> list[dict]:
    ensure_dirs()
    files = sorted(SNAP_DIR.glob("cluster_*.json"), reverse=True)[:limit]
    out = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                {
                    "path": str(path),
                    "as_of": data.get("as_of"),
                    "n": data.get("n"),
                    "mode": data.get("mode"),
                    "kind_filter": data.get("kind_filter"),
                    "skipped_weak_depth": len(data.get("skipped_weak_depth") or []),
                }
            )
        except Exception:
            continue
    return out


def list_forward_reports(limit=20) -> list[dict]:
    ensure_dirs()
    files = sorted(FORWARD_DIR.glob("forward_*.json"), reverse=True)[:limit]
    out = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                {
                    "path": str(path),
                    "as_of": data.get("as_of"),
                    "snapshot_as_of": data.get("snapshot_as_of"),
                    "mode": data.get("mode"),
                    "horizon": data.get("horizon"),
                    "n_setups": data.get("n_setups"),
                    "overall_hit_rate": data.get("overall_hit_rate"),
                }
            )
        except Exception:
            continue
    return out


def run_cluster_backtest(
    matrix_fn,
    bars_fn,
    tickers,
    *,
    feed="opra",
    mode="short",
    cluster_exp="nearest",
    horizon=5,
    limit=30,
    snapshot_path=None,
    kind=None,
    min_contracts: int = DEFAULT_MIN_CONTRACTS,
    min_coverage_cells: int = DEFAULT_MIN_COVERAGE_CELLS,
    skip_weak_depth: bool = True,
    dump_matrix_daily: bool = True,
) -> dict:
    """Capture (unless snapshot_path given) then score cluster accuracy."""
    if snapshot_path:
        snap = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    else:
        snap = capture_cluster_snapshot(
            matrix_fn,
            tickers,
            feed=feed,
            mode=mode,
            cluster_exp=cluster_exp,
            limit=limit,
            kind=kind,
            min_contracts=min_contracts,
            min_coverage_cells=min_coverage_cells,
            skip_weak_depth=skip_weak_depth,
            dump_matrix_daily=dump_matrix_daily,
        )
    report = score_snapshot(bars_fn, snap, horizon=horizon, kind=kind)
    report["snapshot_path"] = snap.get("path") or snapshot_path
    report["snapshot_n"] = snap.get("n")
    report["universe_discipline"] = snap.get("universe_discipline")
    report["skipped_weak_depth"] = snap.get("skipped_weak_depth")
    report["matrix_daily_path"] = snap.get("matrix_daily_path")
    return report
