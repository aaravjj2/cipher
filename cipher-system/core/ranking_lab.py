"""Fast rank-signal extraction from saved Cipher scanner artifacts.

This is a local surrogate model helper: it does not claim to recover private
Access Obsidian weights. It turns saved current rankings into explainable
feature signals that can guide the partial reconstruction.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKTESTS = ROOT / "data" / "backtests"
SNAPSHOTS = BACKTESTS / "cluster_snapshots"

KIND_TIER = {
    "quad": 1.0,
    "triple": 0.82,
    "battle": 0.58,
    "golden": 0.42,
    "call_wall": 0.28,
    "put_floor": 0.28,
}

FEATURE_LABELS = {
    "kind_quad": "Quad cluster",
    "kind_triple": "Triple cluster",
    "kind_battle": "Battle zone",
    "kind_golden": "Golden/top-pull tag",
    "peak_count": "Stacked peak count",
    "setup_count": "Setup tag count",
    "strength_log": "Cluster strength",
    "oi_log": "Cluster OI",
    "score": "Scanner score",
    "abs_score": "Hard-tier score",
    "vacuum_count": "Vacuum count",
    "support_count": "Support count",
    "resistance_count": "Resistance count",
    "coverage_cells_log": "Matrix cell coverage",
    "contracts_log": "Contract coverage",
    "dist_to_pull_pct": "Distance to pull",
    "dist_to_cluster_pct": "Distance to cluster",
    "flash_arming": "Flash arming state",
    "flash_triggered": "Flash triggered state",
    "direction_bullish": "Bullish direction",
    "direction_bearish": "Bearish direction",
}

_CACHE = {"ts": 0.0, "data": None}


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _num(value, default=0.0):
    try:
        if value in (None, "", "nan"):
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def _log1p(value):
    return math.log1p(max(0.0, _num(value)))


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": str(exc)}


def _source_files(limit=40):
    files = []
    if BACKTESTS.is_dir():
        files.extend(BACKTESTS.glob("fullscan_*.json"))
        files.extend(BACKTESTS.glob("cluster_report_*.json"))
    if SNAPSHOTS.is_dir():
        files.extend(SNAPSHOTS.glob("cluster_*.json"))
    files = sorted((p for p in files if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def _pick_kind(item):
    cluster = item.get("cluster") if isinstance(item, dict) else None
    if isinstance(cluster, dict) and cluster.get("kind"):
        return str(cluster.get("kind")).lower()
    for setup in item.get("setups") or []:
        if isinstance(setup, dict) and setup.get("kind"):
            return str(setup["kind"]).lower()
        if isinstance(setup, str):
            return setup.lower()
    kind = item.get("kind")
    return str(kind).lower() if kind else None


def _iter_items(payload):
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("top"), list):
        return payload["top"]
    if isinstance(payload.get("picks"), list):
        return payload["picks"]
    if isinstance(payload.get("top20"), list):
        return payload["top20"]
    rows = []
    for idx, row in enumerate(payload.get("top_scores") or [], start=1):
        if isinstance(row, list) and row:
            rows.append(
                {
                    "ticker": row[0],
                    "rank": idx,
                    "score": row[1] if len(row) > 1 else None,
                    "direction": row[2] if len(row) > 2 else None,
                }
            )
    return rows


def _primary_setup(item, kind):
    cluster = item.get("cluster")
    if isinstance(cluster, dict):
        return cluster
    for setup in item.get("setups") or []:
        if isinstance(setup, dict) and (not kind or setup.get("kind") == kind):
            return setup
    return {}


def _features(item):
    kind = _pick_kind(item)
    setup_count = len(item.get("setups") or [])
    setup = _primary_setup(item, kind)
    spot = _num(item.get("spot"))
    pull = _num(item.get("pull_target"))
    center = _num(setup.get("center"))
    direction = str(item.get("direction") or "").upper()
    agent_state = str(item.get("agent_state") or (item.get("flash") or {}).get("agent_state") or "").lower()
    return {
        "kind_quad": 1.0 if kind == "quad" else 0.0,
        "kind_triple": 1.0 if kind == "triple" else 0.0,
        "kind_battle": 1.0 if kind == "battle" else 0.0,
        "kind_golden": 1.0 if kind == "golden" else 0.0,
        "peak_count": max(_num(item.get("peak_count")), _num(setup.get("peak_count"))),
        "setup_count": float(setup_count),
        "strength_log": _log1p(setup.get("strength")),
        "oi_log": _log1p(setup.get("oi")),
        "score": _num(item.get("score")),
        "abs_score": _num(item.get("abs_score") or item.get("score")),
        "vacuum_count": _num(item.get("vacuum_count")),
        "support_count": _num(item.get("support_count")),
        "resistance_count": _num(item.get("resistance_count")),
        "coverage_cells_log": _log1p(item.get("coverage_cells")),
        "contracts_log": _log1p(item.get("contracts")),
        "dist_to_pull_pct": abs(pull - spot) / spot if spot and pull else 0.0,
        "dist_to_cluster_pct": abs(center - spot) / spot if spot and center else 0.0,
        "flash_arming": 1.0 if agent_state == "arming" else 0.0,
        "flash_triggered": 1.0 if agent_state == "triggered" else 0.0,
        "direction_bullish": 1.0 if direction == "BULLISH" else 0.0,
        "direction_bearish": 1.0 if direction == "BEARISH" else 0.0,
    }


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def _rows_from_file(path):
    payload = _load_json(path)
    if payload.get("_error"):
        return [], {"file": path.name, "error": payload["_error"], "rows": 0}
    items = _iter_items(payload)
    n = len(items)
    rows = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        rank = int(_num(item.get("rank"), idx) or idx)
        label = 1.0 if n <= 1 else 1.0 - ((rank - 1) / max(n - 1, 1))
        kind = _pick_kind(item)
        rows.append(
            {
                "ticker": item.get("ticker"),
                "rank": rank,
                "target": max(0.0, min(1.0, label)),
                "strategy": payload.get("strategy") or ("cluster" if "cluster" in path.name else "scan"),
                "kind": kind,
                "features": _features(item),
                "source": path.name,
                "as_of": payload.get("as_of") or payload.get("saved_at"),
            }
        )
    return rows, {"file": path.name, "strategy": payload.get("strategy"), "rows": len(rows), "as_of": payload.get("as_of")}


def status(force=False):
    if not force and _CACHE["data"] and time.time() - _CACHE["ts"] < 15:
        return _CACHE["data"]

    files = _source_files()
    rows = []
    sources = []
    for path in files:
        part, meta = _rows_from_file(path)
        sources.append(meta)
        rows.extend(part)

    warnings = []
    if not rows:
        warnings.append("No saved scans found yet. Run Setup Scanner and save/ingest snapshots to improve the surrogate.")

    targets = [r["target"] for r in rows]
    feature_names = sorted(FEATURE_LABELS)
    weights = []
    for name in feature_names:
        values = [r["features"].get(name, 0.0) for r in rows]
        c = _corr(values, targets)
        nonzero = sum(1 for v in values if abs(v) > 1e-9)
        if nonzero or abs(c) >= 0.02:
            weights.append(
                {
                    "feature": name,
                    "label": FEATURE_LABELS.get(name, name),
                    "correlation": round(c, 4),
                    "coverage": round(nonzero / max(len(values), 1), 3),
                    "direction": "positive" if c >= 0 else "negative",
                    "raw_weight": abs(c) * (0.5 + 0.5 * nonzero / max(len(values), 1)),
                }
            )
    total = sum(w["raw_weight"] for w in weights) or 1.0
    for weight in weights:
        weight["weight"] = round(weight.pop("raw_weight") / total, 4)
    weights.sort(key=lambda w: w["weight"], reverse=True)

    kind_groups = defaultdict(lambda: {"count": 0, "rank_sum": 0.0, "target_sum": 0.0})
    for row in rows:
        kind = row.get("kind") or "unknown"
        kind_groups[kind]["count"] += 1
        kind_groups[kind]["rank_sum"] += row["rank"]
        kind_groups[kind]["target_sum"] += row["target"]
    cluster_order = []
    for kind, group in kind_groups.items():
        count = group["count"]
        cluster_order.append(
            {
                "kind": kind,
                "tier_prior": KIND_TIER.get(kind, 0.0),
                "count": count,
                "avg_rank": round(group["rank_sum"] / max(count, 1), 2),
                "rank_signal": round(group["target_sum"] / max(count, 1), 4),
            }
        )
    cluster_order.sort(key=lambda item: (item["tier_prior"], item["rank_signal"]), reverse=True)

    latest = next((s for s in sources if s.get("as_of")), None)
    result = {
        "as_of": _utcnow(),
        "latest_source_as_of": latest.get("as_of") if latest else None,
        "rows": len(rows),
        "files": len(files),
        "sources": sources[:16],
        "rank_signal_weights": weights[:14],
        "cluster_kind_order": cluster_order,
        "formula": (
            "Surrogate from saved ranked outputs: target = reciprocal rank position; "
            "features = cluster kind/tier, peak stack, strength/OI logs, score fields, "
            "distance-to-pull/cluster, scanner coverage, flash state, and direction."
        ),
        "caveat": (
            "Partial local reconstruction only. It learns from observed rankings and saved snapshots, "
            "not proprietary Access Obsidian internals."
        ),
        "warnings": warnings,
    }
    _CACHE.update({"ts": time.time(), "data": result})
    return result
