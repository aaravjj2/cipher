"""Cipher / Flash weight rediscovery lab.

Ingest commercial Cipher Model + Flash CSVs (labels), dump local matrix features,
fit ridge models + rank-quality reports, and optionally apply weights to scoring.

This cannot recover proprietary Access Obsidian weights exactly. It calibrates a
local reconstruction so scores/ranks track labeled commercial outputs.

GEX features (when used) are public-OI heuristics under retail assumptions — never
verified dealer positioning.
"""
from __future__ import annotations

import csv
import json
import math
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "data" / "weight_lab"
COMMERCIAL = LAB / "commercial"
COMMERCIAL_OTHER = COMMERCIAL / "other"
FEATURES = LAB / "features"
WEIGHTS_PATH = LAB / "weights.json"
WEIGHTS_FLASH_PATH = LAB / "weights_flash.json"
WEIGHTS_LIQ_PATH = LAB / "weights_liq.json"
WEIGHTS_CLUSTER_PATH = LAB / "weights_cluster.json"
CLUSTER_SCORE_WEIGHTS_PATH = LAB / "cluster_score_weights.json"
FLASH_INDEX_REF = LAB / "flash_index_ref.json"
ACTIVE_PATH = LAB / "active.json"

# Heuristic Cluster-scan ranking (not ridge-fit commercial labels).
# Hard tier from hard_rank_order, then weighted factors within tier.
_DEFAULT_CLUSTER_SCORE_WEIGHTS = {
    "version": 2,
    "hard_rank_order": ["quad", "triple", "battle", "golden", "call_wall", "put_floor"],
    "kind_boost": {
        "quad": 40,
        "triple": 25,
        "battle": 10,
        "golden": 8,
        "call_wall": 5,
        "put_floor": 5,
    },
    "tier_gap": 100,
    "factors": {
        "strength_norm": 0.25,
        "proximity": 0.20,
        "side_above": 0.10,
        "oi_log": 0.15,
        "vacuum_thin": 0.08,
        "peak_count_norm": 0.07,
        "persistence": 0.10,
        "momentum": 0.05,
    },
}
AUDIT_SEED = (
    ROOT
    / "access-obsidian-complete-audit"
    / "access-obsidian-audit"
    / "weight-inference"
    / "cipher_model_scan_parsed.csv"
)

_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")

# Time features: dte_inv, trading-week Fourier (dow_sin/cos), dist_to_event stub.
_TIME_FEATURE_NAMES = (
    "dte_inv",
    "dow_sin",
    "dow_cos",
    "dist_to_event",
)

FEATURE_NAMES = [
    "support_count",
    "resistance_count",
    "vacuum_count",
    "near_gap_pct",
    "pull_from_support_pct",
    "stretch_from_support_pct",
    "dist_to_floor_pct",
    "dist_to_ceil_pct",
    "peak_sharpness",
    "oi_log",
    "peak_abs_log",
    "bullish",
    "runway_thinness",
    "dte_inv",
    "dow_sin",
    "dow_cos",
    "dist_to_event",
]

# Flash runway head: card geometry + clarity/DTE + time features.
FLASH_FEATURE_NAMES = [
    "support_count",
    "resistance_count",
    "vacuum_count",
    "near_gap_pct",
    "pull_from_support_pct",
    "stretch_from_support_pct",
    "runway_clarity_norm",
    "dte_inv",
    "bullish",
    "runway_thinness",
    "dow_sin",
    "dow_cos",
    "dist_to_event",
]

# Live OPRA fields kept when merging with commercial card geometry.
_LOCAL_MARKET_KEYS = (
    "oi_log",
    "peak_abs_log",
    "peak_sharpness",
    "runway_thinness",
    "dist_to_floor_pct",
    "dist_to_ceil_pct",
)

_CAVEAT_BASE = (
    "Rank/score calibration against commercial card labels ± local public-OI GEX features. "
    "Approximation only — not proprietary Access Obsidian weights. "
    "GEX is a retail-OI heuristic, not verified dealer positioning. Research-only."
)


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _parse_asof_date(row: dict) -> date | None:
    """Prefer explicit date fields; else YYYY-MM-DD from commercial filename."""
    for key in ("as_of", "date", "session_date", "asof"):
        v = row.get(key)
        if v in (None, ""):
            continue
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            continue
    src = str(row.get("source") or "")
    m = _DATE_RE.search(src)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _trading_dow(d: date | None) -> int:
    """Mon=0 … Fri=4; weekend clamps to Friday (last session stub)."""
    if d is None:
        return 2  # mid-week default when unknown
    wd = d.weekday()
    return 4 if wd >= 5 else wd


def _time_features(*, dte=None, as_of: date | None = None, dist_to_event=None) -> dict:
    """Calendar / DTE features. dist_to_event is 0 when unknown (stub)."""
    dte_v = float(dte if dte is not None else 5.0)
    dow = _trading_dow(as_of)
    angle = 2.0 * math.pi * dow / 5.0
    dist = dist_to_event
    if dist in (None, "", "nan"):
        dist = 0.0
    try:
        dist = float(dist)
    except (TypeError, ValueError):
        dist = 0.0
    return {
        "dte_inv": 1.0 / (1.0 + max(dte_v, 0.0)),
        "dow": float(dow),
        "dow_sin": math.sin(angle),
        "dow_cos": math.cos(angle),
        "dist_to_event": dist,
    }


def _fit_warnings(n: int, r2: float, tau: float | None = None) -> list[str]:
    warnings: list[str] = []
    if n < 30:
        warnings.append(f"Small sample (n={n}<30); rank calibration may be unstable.")
    if n < 15:
        warnings.append(f"Very small sample (n={n}<15); treat coefficients as exploratory only.")
    if r2 > 0.95 and n < 40:
        warnings.append(f"Very high R² ({r2:.3f}) with n={n}<40 — likely overfit to labels.")
    if r2 > 0.99 and n < 50:
        warnings.append(f"Near-perfect R² ({r2:.3f}) with n={n}<50 — do not trust out-of-sample.")
    if tau is not None and tau < 0.2 and n >= 10:
        warnings.append(f"Weak rank agreement (Kendall τ={tau:.3f}); ordering may not track commercial ranks.")
    if tau is not None and r2 > 0.9 and tau < 0.4 and n < 40:
        warnings.append("High score R² but modest τ — scores fit magnitudes better than ranks.")
    return warnings


def ensure_dirs():
    COMMERCIAL.mkdir(parents=True, exist_ok=True)
    COMMERCIAL_OTHER.mkdir(parents=True, exist_ok=True)
    FEATURES.mkdir(parents=True, exist_ok=True)
    LAB.mkdir(parents=True, exist_ok=True)


def seed_audit_commercial():
    """Copy the audited top-30 parse into commercial/ if not already present."""
    ensure_dirs()
    dest = COMMERCIAL / "audit_2026-07-18_cipher_model_top30.csv"
    if dest.exists():
        return {"path": str(dest), "seeded": False, "exists": True}
    if not AUDIT_SEED.exists():
        return {"path": None, "seeded": False, "error": f"Missing audit seed: {AUDIT_SEED}"}
    shutil.copy2(AUDIT_SEED, dest)
    return {"path": str(dest), "seeded": True, "exists": True}


def parse_commercial_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            ticker = (raw.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            try:
                score = float(raw.get("score") or 0)
            except ValueError:
                continue
            rank = raw.get("rank")
            try:
                rank = int(float(rank)) if rank not in (None, "") else None
            except ValueError:
                rank = None

            def _f(key, default=None):
                v = raw.get(key)
                if v in (None, "", "nan", "None"):
                    return default
                try:
                    return float(v)
                except ValueError:
                    return default

            clarity = _f("runway_clarity")
            if clarity is not None and clarity > 1.5:
                clarity = clarity / 100.0
            rows.append(
                {
                    "source": path.name,
                    "ticker": ticker,
                    "rank": rank,
                    "score": score,
                    "direction": (raw.get("direction") or "").upper() or None,
                    "setup": (raw.get("setup") or "").strip().lower() or None,
                    "first_support": _f("first_support"),
                    "first_resistance": _f("first_resistance"),
                    "pull_target": _f("pull_target"),
                    "last_vacuum_target": _f("last_vacuum_target"),
                    "support_count": _f("support_count", 0) or 0,
                    "resistance_count": _f("resistance_count", 0) or 0,
                    "vacuum_count": _f("vacuum_count", 0) or 0,
                    "near_gap_pct": _f("near_gap_pct"),
                    "pull_from_support_pct": _f("pull_from_support_pct"),
                    "stretch_from_support_pct": _f("stretch_from_support_pct"),
                    "runway_clarity": clarity,
                    "dte": _f("dte"),
                    "spot": _f("spot"),
                    "read": raw.get("read") or "",
                }
            )
    return rows


def _dedupe_by_ticker(rows: list[dict]) -> list[dict]:
    by = {}
    for row in rows:
        t = row["ticker"]
        prev = by.get(t)
        if not prev:
            by[t] = row
            continue
        pr, nr = prev.get("rank"), row.get("rank")
        if nr is not None and (pr is None or nr < pr):
            by[t] = row
        elif nr == pr and row.get("score", 0) >= prev.get("score", 0):
            by[t] = row
    return sorted(by.values(), key=lambda r: (r.get("rank") is None, r.get("rank") or 999, -r.get("score", 0)))


def load_all_commercial() -> list[dict]:
    ensure_dirs()
    # Do not auto-seed audit if newer commercial exports exist
    if not any(COMMERCIAL.glob("*.csv")):
        seed_audit_commercial()
    out = []
    # Only top-level CSVs (skip archive/, other/)
    for path in sorted(COMMERCIAL.glob("*.csv")):
        out.extend(parse_commercial_csv(path))
    return _dedupe_by_ticker(out)


def load_flash_commercial() -> list[dict]:
    """Flash runway labels from commercial/other/ (excludes flash-index)."""
    ensure_dirs()
    out = []
    for path in sorted(COMMERCIAL_OTHER.glob("*.csv")):
        name = path.name.lower()
        if "flash_index" in name or "flash-index" in name:
            continue
        if "flash" not in name:
            continue
        out.extend(parse_commercial_csv(path))
    return _dedupe_by_ticker(out)


def load_flash_index_ref() -> list[dict]:
    """Tiny Flash Index sample — reference only, not a ridge fit."""
    ensure_dirs()
    out = []
    for path in sorted(COMMERCIAL_OTHER.glob("*.csv")):
        name = path.name.lower()
        if "flash_index" not in name and "flash-index" not in name:
            continue
        if "parsed" in name or name.endswith("_flash_index.csv"):
            out.extend(parse_commercial_csv(path))
    rows = _dedupe_by_ticker(out)
    FLASH_INDEX_REF.write_text(
        json.dumps(
            {
                "as_of": _utcnow(),
                "n": len(rows),
                "caveat": (
                    "Flash Index reference from commercial capture (IWM/SPY/QQQ typical). "
                    "Too few rows for a stable ridge model — reference-only, never ridge-fit. "
                    "Not proprietary Access Obsidian weights; GEX elsewhere is public-OI heuristic."
                ),
                "fit": False,
                "rows": [
                    {
                        "ticker": r["ticker"],
                        "score": r["score"],
                        "rank": r.get("rank"),
                        "direction": r.get("direction"),
                        "runway_clarity": r.get("runway_clarity"),
                        "read": (r.get("read") or "")[:180],
                    }
                    for r in rows
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return rows


def features_from_label_row(row: dict) -> dict:
    """Visible-card features only (audit baseline) + time features."""
    near = row.get("near_gap_pct")
    pull = row.get("pull_from_support_pct")
    stretch = row.get("stretch_from_support_pct")
    if near is None and row.get("first_support") and row.get("first_resistance"):
        # approximate if spot missing: use absolute gaps as fractions of support
        s = row["first_support"] or 1.0
        near = abs((row["first_resistance"] or s) - s) / max(abs(s), 1e-6)
    clarity = row.get("runway_clarity")
    if clarity is None:
        clarity = min(float(row.get("vacuum_count") or 0) / 3.0, 1.0)
    dte = float(row.get("dte") or 5.0)
    tf = _time_features(
        dte=dte,
        as_of=_parse_asof_date(row),
        dist_to_event=row.get("dist_to_event"),
    )
    return {
        "support_count": float(row.get("support_count") or 0),
        "resistance_count": float(row.get("resistance_count") or 0),
        "vacuum_count": float(row.get("vacuum_count") or 0),
        "near_gap_pct": float(near or 0),
        "pull_from_support_pct": float(pull or 0),
        "stretch_from_support_pct": float(stretch or 0),
        "dist_to_floor_pct": 0.0,
        "dist_to_ceil_pct": float(near or 0) * 0.5,
        "peak_sharpness": 2.0,
        "oi_log": 10.0,
        "peak_abs_log": 12.0,
        "bullish": 1.0 if (row.get("direction") or "").startswith("BULL") else 0.0,
        "runway_thinness": min(float(row.get("vacuum_count") or 0) / 4.0, 1.0),
        "runway_clarity_norm": float(clarity),
        **tf,
    }


def merge_label_and_local(label_feat: dict, local_feat: dict | None) -> dict:
    """Commercial card geometry + live OPRA microstructure when both exist."""
    out = dict(label_feat)
    if not local_feat:
        return out
    for key in _LOCAL_MARKET_KEYS:
        if key in local_feat:
            out[key] = local_feat[key]
    return out


def features_from_model(
    model: dict,
    profile: list,
    spot: float,
    *,
    runway_clarity=None,
    dte=None,
    as_of=None,
    dist_to_event=None,
) -> dict | None:
    """Local matrix-derived features aligned to Cipher Model card geometry."""
    if not model or not spot or not profile:
        return None
    fs = model.get("first_support")
    fr = model.get("first_resistance")
    pull = model.get("pull_target")
    peak_abs = max((p["abs"] for p in profile), default=0.0) or 1.0
    avg_abs = sum(p["abs"] for p in profile) / max(len(profile), 1)
    oi = sum(p.get("oi") or 0.0 for p in profile)
    near_gap_pct = abs((fr or spot) - (fs or spot)) / spot if fs and fr else 0.0
    pull_pct = abs((pull or spot) - (fs or spot)) / spot if fs and pull else 0.0
    stretch_pct = abs((model.get("last_vacuum_target") or pull or spot) - (fs or spot)) / spot if fs else 0.0
    lo, hi = sorted([spot, pull or spot])
    path = [p for p in profile if lo < p["strike"] < hi] if pull else []
    thick = sum(1 for p in path if p["abs"] >= peak_abs * 0.25)
    thin = max(0.0, 1.0 - thick / max(len(path), 1)) if path else 0.5
    if runway_clarity is None:
        runway_clarity = thin
    dte_v = float(dte if dte is not None else 5.0)
    as_of_date: date | None
    if isinstance(as_of, date):
        as_of_date = as_of
    elif as_of:
        try:
            as_of_date = date.fromisoformat(str(as_of)[:10])
        except ValueError:
            as_of_date = datetime.now(timezone.utc).date()
    else:
        as_of_date = datetime.now(timezone.utc).date()
    tf = _time_features(dte=dte_v, as_of=as_of_date, dist_to_event=dist_to_event)
    return {
        "support_count": float(model.get("support_count") or 0),
        "resistance_count": float(model.get("resistance_count") or 0),
        "vacuum_count": float(model.get("vacuum_count") or 0),
        "near_gap_pct": float(near_gap_pct),
        "pull_from_support_pct": float(pull_pct),
        "stretch_from_support_pct": float(stretch_pct),
        "dist_to_floor_pct": abs(spot - fs) / spot if fs else 0.0,
        "dist_to_ceil_pct": abs(fr - spot) / spot if fr else 0.0,
        "peak_sharpness": float(peak_abs / (avg_abs + 1e-9)),
        "oi_log": float(math.log1p(oi)),
        "peak_abs_log": float(math.log1p(peak_abs)),
        "bullish": 1.0 if (model.get("direction") or "").startswith("BULL") else 0.0,
        "runway_thinness": float(thin),
        "runway_clarity_norm": float(max(0.0, min(1.0, runway_clarity))),
        **tf,
    }


def vectorize(feat: dict, names: list[str] | None = None) -> np.ndarray:
    names = names or FEATURE_NAMES
    return np.array([float(feat.get(name) or 0.0) for name in names], dtype=float)


def dump_features_for_tickers(matrix_fn, tickers, feed="opra", mode="short") -> dict:
    """Pull live matrices and write feature rows for weight fitting."""
    from scanner import MODE_CONFIG, _local_peaks, _strike_profile, cipher_model_from_profile

    ensure_dirs()
    cfg = MODE_CONFIG.get(mode, MODE_CONFIG["short"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = FEATURES / f"features_{stamp}.jsonl"
    written = 0
    errors = []
    with out_path.open("w", encoding="utf-8") as fh:
        for ticker in tickers:
            ticker = ticker.upper()
            try:
                payload = matrix_fn(
                    ticker,
                    feed,
                    cfg["depth"],
                    cfg["expirations"],
                    force=False,
                    chain_pages=cfg.get("pages", 2),
                )
                spot = (payload.get("quote") or {}).get("price_context")
                summary = payload.get("summary") or {}
                profile = _strike_profile(payload.get("rows"))
                peaks = _local_peaks(profile)
                model = cipher_model_from_profile(ticker, profile, peaks, summary, spot)
                if not model:
                    errors.append({"ticker": ticker, "error": "no model"})
                    continue
                feat = features_from_model(model, profile, spot)
                if not feat:
                    continue
                row = {
                    "as_of": _utcnow(),
                    "ticker": ticker,
                    "spot": spot,
                    "mode": mode,
                    "feed": feed,
                    "features": feat,
                    "model": {
                        "direction": model.get("direction"),
                        "heuristic_score": model.get("score"),
                        "supports": model.get("supports"),
                        "resistances": model.get("resistances"),
                        "pull_target": model.get("pull_target"),
                    },
                }
                fh.write(json.dumps(row) + "\n")
                written += 1
            except Exception as exc:
                errors.append({"ticker": ticker, "error": str(exc)})
    return {
        "path": str(out_path),
        "written": written,
        "errors": errors[:20],
        "as_of": _utcnow(),
    }


def load_feature_index() -> dict[str, dict]:
    """Latest features by ticker across all dumps. Prefer live OPRA over commercial UI grids."""
    ensure_dirs()
    index: dict[str, dict] = {}
    for path in sorted(FEATURES.glob("features_*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                t = row["ticker"]
                prev = index.get(t)
                if not prev:
                    index[t] = row
                    continue
                prev_live = (prev.get("feed") or "") not in {"obsidian_ui", ""}
                new_live = (row.get("feed") or "") not in {"obsidian_ui", ""}
                # Prefer live market dumps; otherwise take newer file order
                if new_live and not prev_live:
                    index[t] = row
                elif new_live == prev_live:
                    index[t] = row
    return index


def _ridge(X, y, l2=1.0):
    """Ordinary ridge with intercept column. Returns coef (incl intercept), yhat, r2."""
    n, d = X.shape
    Xb = np.column_stack([np.ones(n), X])
    reg = l2 * np.eye(d + 1)
    reg[0, 0] = 0.0
    coef = np.linalg.solve(Xb.T @ Xb + reg, Xb.T @ y)
    yhat = Xb @ coef
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1.0
    r2 = 1.0 - ss_res / ss_tot
    return coef, yhat, r2


def _ridge_rank_hybrid(X, y, l2=1.0, rank_weight=1.0, n_iter=400, lr=0.08):
    """Hybrid ridge + soft pairwise rank penalty (documented fit choice).

    Objective:
      L = mean((y - pred)^2) + l2 * ||w[1:]||^2
        + rank_weight * mean softplus(-(pred_i - pred_j)) over pairs with y_i > y_j

    Softplus on score gaps is a smooth Kendall/pairwise surrogate: it pushes
    higher-labeled rows above lower-labeled ones without requiring hard ranks.
    Default fit remains plain ridge; enable via rank_loss=True / --rank-loss.
    """
    n, d = X.shape
    Xb = np.column_stack([np.ones(n), X])
    coef, _, _ = _ridge(X, y, l2=l2)
    w = coef.copy()

    pairs = [(i, j) for i in range(n) for j in range(n) if y[i] > y[j] + 1e-9]
    if not pairs:
        yhat = Xb @ w
        ss_res = float(np.sum((y - yhat) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1.0
        return w, yhat, 1.0 - ss_res / ss_tot

    if len(pairs) > 2500:
        rng = np.random.default_rng(0)
        pick = rng.choice(len(pairs), 2500, replace=False)
        pairs = [pairs[k] for k in pick]
    pairs_i = np.array([p[0] for p in pairs], dtype=int)
    pairs_j = np.array([p[1] for p in pairs], dtype=int)
    n_pairs = float(len(pairs))

    for _ in range(n_iter):
        pred = Xb @ w
        resid = pred - y
        grad = (2.0 / n) * (Xb.T @ resid)
        grad[1:] += 2.0 * l2 * w[1:]
        # softplus(-(s_i - s_j)); d/ds_i = -sigmoid(-(s_i-s_j)) = -1/(1+e^{s_i-s_j})
        diff = pred[pairs_i] - pred[pairs_j]
        sig = 1.0 / (1.0 + np.exp(np.clip(diff, -30.0, 30.0)))
        scale = rank_weight / n_pairs
        g_pred = np.zeros(n)
        np.add.at(g_pred, pairs_i, -sig * scale)
        np.add.at(g_pred, pairs_j, sig * scale)
        grad += Xb.T @ g_pred
        w = w - lr * grad

    yhat = Xb @ w
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1.0
    r2 = 1.0 - ss_res / ss_tot
    return w, yhat, r2


def _kendall_tau(ranks_a, ranks_b):
    """Kendall tau-b for two rank vectors (1 = perfect agreement)."""
    n = len(ranks_a)
    if n < 2:
        return 0.0
    concord = discord = 0
    for i in range(n):
        for j in range(i + 1, n):
            da = ranks_a[i] - ranks_a[j]
            db = ranks_b[i] - ranks_b[j]
            if da == 0 or db == 0:
                continue
            if da * db > 0:
                concord += 1
            else:
                discord += 1
    denom = concord + discord
    return (concord - discord) / denom if denom else 0.0


def fit_weights(*, use_local_features=True, l2=1.0, rank_loss=False) -> dict:
    """Fit standardized ridge (default) or hybrid ridge+rank on commercial labels."""
    ensure_dirs()
    labels = load_all_commercial()
    if len(labels) < 5:
        return {"error": "Need at least 5 commercial labeled rows in data/weight_lab/commercial/"}

    feat_index = load_feature_index() if use_local_features else {}
    rows = []
    for lab in labels:
        ticker = lab["ticker"]
        label_feat = features_from_label_row(lab)
        if use_local_features and ticker in feat_index:
            feat = merge_label_and_local(label_feat, feat_index[ticker]["features"])
            source = "local+label"
        else:
            feat = label_feat
            source = "label_only"
        rows.append({"ticker": ticker, "score": lab["score"], "rank": lab.get("rank"), "feat": feat, "source": source})

    return _fit_and_write(
        rows, FEATURE_NAMES, WEIGHTS_PATH, l2=l2, head="cipher_model", rank_loss=rank_loss
    )


def fit_flash_weights(*, use_local_features=True, l2=1.0, rank_loss=False) -> dict:
    """Fit Flash runway head from commercial/other flash CSVs."""
    ensure_dirs()
    labels = load_flash_commercial()
    if len(labels) < 5:
        return {
            "error": "Need at least 5 Flash runway rows in data/weight_lab/commercial/other/",
            "hint": "Expected files like obsidian_*_flash_runway.csv",
            "fit": False,
            "rows": 0,
        }

    # Persist Flash Index reference alongside (not used in this ridge).
    load_flash_index_ref()

    feat_index = load_feature_index() if use_local_features else {}
    rows = []
    for lab in labels:
        ticker = lab["ticker"]
        label_feat = features_from_label_row(lab)
        if use_local_features and ticker in feat_index:
            feat = merge_label_and_local(label_feat, feat_index[ticker]["features"])
            source = "local+label"
        else:
            feat = label_feat
            source = "label_only"
        rows.append({"ticker": ticker, "score": lab["score"], "rank": lab.get("rank"), "feat": feat, "source": source})

    return _fit_and_write(
        rows, FLASH_FEATURE_NAMES, WEIGHTS_FLASH_PATH, l2=l2, head="flash", rank_loss=rank_loss
    )


def _other_labeled_rows(*name_needles: str) -> list[dict]:
    """Load commercial/other CSVs whose names match any needle (case-insensitive)."""
    ensure_dirs()
    out = []
    for path in sorted(COMMERCIAL_OTHER.glob("*.csv")):
        name = path.name.lower()
        if any(n in name for n in name_needles):
            out.extend(parse_commercial_csv(path))
    return _dedupe_by_ticker(out)


def load_liq_weights() -> dict | None:
    if not WEIGHTS_LIQ_PATH.exists():
        return None
    return json.loads(WEIGHTS_LIQ_PATH.read_text(encoding="utf-8"))


def load_cluster_weights() -> dict | None:
    if not WEIGHTS_CLUSTER_PATH.exists():
        return None
    return json.loads(WEIGHTS_CLUSTER_PATH.read_text(encoding="utf-8"))


def default_cluster_score_weights() -> dict:
    return json.loads(json.dumps(_DEFAULT_CLUSTER_SCORE_WEIGHTS))


def load_cluster_score_weights() -> dict:
    """Editable Cluster-scan ranking weights (hard tier + factors).

    File: data/weight_lab/cluster_score_weights.json
    Missing/corrupt file falls back to built-in defaults.
    """
    ensure_dirs()
    if not CLUSTER_SCORE_WEIGHTS_PATH.exists():
        payload = default_cluster_score_weights()
        CLUSTER_SCORE_WEIGHTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload
    try:
        raw = json.loads(CLUSTER_SCORE_WEIGHTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_cluster_score_weights()
    base = default_cluster_score_weights()
    if not isinstance(raw, dict):
        return base
    # Merge so partial edits still work.
    out = {**base, **raw}
    out["kind_boost"] = {**base.get("kind_boost", {}), **(raw.get("kind_boost") or {})}
    out["factors"] = {**base.get("factors", {}), **(raw.get("factors") or {})}
    if not out.get("hard_rank_order"):
        out["hard_rank_order"] = list(base["hard_rank_order"])
    return out


def _cluster_factor_vector(setup: dict, *, spot, model=None, peak_abs: float = 1.0) -> dict:
    """Normalized 0–1 factors for Cluster ranking (documented in cluster_score_weights.json)."""
    strength = float(setup.get("strength") or 0.0)
    peak_abs = float(peak_abs or 1.0) or 1.0
    strength_norm = max(0.0, min(1.0, strength / peak_abs))

    center = float(setup.get("center") or setup.get("low") or 0.0)
    if spot and spot > 0 and center:
        dist = abs(center - spot) / spot
        # Match QUAD_BAND_PCT (~3%) used for upside stacks.
        proximity = max(0.0, min(1.0, 1.0 - dist / 0.03))
    else:
        proximity = 0.0

    side_above = 1.0 if (setup.get("side") or "") == "above" else 0.0

    oi = float(setup.get("oi") or 0.0)
    oi_log = max(0.0, min(1.0, math.log1p(oi) / math.log1p(500_000.0)))

    vacuum_n = float((model or {}).get("vacuum_count") or 0.0)
    vacuum_thin = max(0.0, min(1.0, vacuum_n / 3.0))

    peak_count = float(setup.get("peak_count") or len(setup.get("strikes") or []) or 1)
    peak_count_norm = max(0.0, min(1.0, peak_count / 6.0))

    # Persistence: fraction of strikes that appear in multiple expirations
    # Higher persistence = more reliable cluster
    persistence = float(setup.get("persistence_ratio") or 0.0)
    persistence = max(0.0, min(1.0, persistence))

    # Momentum: from GEX forecast (if available)
    # Positive momentum = strengthening cluster
    gex_forecast = setup.get("gex_forecast") or {}
    momentum_raw = float(gex_forecast.get("avg_momentum_score") or 0.0)
    # Scale from [-1, 1] to [0, 1]
    momentum = max(0.0, min(1.0, (momentum_raw + 1.0) / 2.0))

    return {
        "strength_norm": strength_norm,
        "proximity": proximity,
        "side_above": side_above,
        "oi_log": oi_log,
        "vacuum_thin": vacuum_thin,
        "peak_count_norm": peak_count_norm,
        "persistence": persistence,
        "momentum": momentum,
    }


def score_cluster_setup(
    setup: dict,
    *,
    spot,
    model=None,
    peak_abs: float = 1.0,
    weights: dict | None = None,
) -> dict:
    """Score one cluster setup: hard tier first, then weighted factors.

    abs_score = (n_tiers − tier_index) * tier_gap + kind_boost + 20 * Σ(w_i * f_i)

    Guarantees any quad outranks any triple, which outranks battle/golden/walls,
    regardless of factor values (tier_gap dominates).

    2026-08-06: investigated matching the real product's displayed "Strength"
    number directly (not just the tier ordering). Real values for same-tier
    (triple) setups span a huge range — 72 to 260 across a 31-ticker paired
    sample — far wider than this formula's factor_contrib term (0-20) can
    produce once tier_gap floors each tier. Checked whether factor_contrib's
    *inputs* (the weighted factor sum before the 20x scale) at least correlate
    with the real number, to see if widening the scale would help: measured
    correlation was 0.19 (essentially noise) against the same 31 tickers. That
    rules out a simple rescale — the current factor vector (strength_norm,
    proximity, side_above, oi_log, vacuum_thin, peak_count_norm, persistence,
    momentum), all derived from public OI/GEX, does not predict the real
    displayed magnitude. The real number likely reflects something not
    reconstructable from public OI (e.g. actual dealer positioning) rather
    than a differently-scaled version of the same public-data signal. Did not
    change factor_contrib's scale — a rescale here would fit noise, not signal.
    """
    weights = weights or load_cluster_score_weights()
    order = list(weights.get("hard_rank_order") or _DEFAULT_CLUSTER_SCORE_WEIGHTS["hard_rank_order"])
    kind = (setup.get("kind") or "").lower()
    try:
        tier_index = order.index(kind)
    except ValueError:
        tier_index = len(order)
    n_tiers = max(len(order), 1)
    tier_gap = float(weights.get("tier_gap") or 100)
    kind_boost = float((weights.get("kind_boost") or {}).get(kind, 0))

    factors = _cluster_factor_vector(setup, spot=spot, model=model, peak_abs=peak_abs)
    factor_w = weights.get("factors") or {}
    weighted = 0.0
    for name, val in factors.items():
        weighted += float(factor_w.get(name, 0.0)) * float(val)
    # Keep factor contrib in a band well below tier_gap so tiers never invert.
    factor_contrib = 20.0 * weighted

    abs_score = (n_tiers - tier_index) * tier_gap + kind_boost + factor_contrib
    display = max(45.0, min(99.0, 55.0 + kind_boost * 0.6 + factor_contrib))
    return {
        "abs_score": round(abs_score, 3),
        "score": round(display, 1),
        "kind": kind,
        "tier_index": tier_index,
        "kind_boost": kind_boost,
        "factor_contrib": round(factor_contrib, 3),
        "factors": {k: round(v, 4) for k, v in factors.items()},
        "score_source": "cluster_weights",
    }


def score_cluster_pick(
    setups: list[dict],
    *,
    spot,
    model=None,
    peak_abs: float = 1.0,
    weights: dict | None = None,
) -> dict | None:
    """Pick best setup by hard tier + factors; return score payload or None."""
    if not setups:
        return None
    weights = weights or load_cluster_score_weights()
    best = None
    best_payload = None
    for setup in setups:
        payload = score_cluster_setup(
            setup, spot=spot, model=model, peak_abs=peak_abs, weights=weights
        )
        if best_payload is None or payload["abs_score"] > best_payload["abs_score"]:
            best = setup
            best_payload = payload
    if not best_payload:
        return None
    return {**best_payload, "setup": best}


def fit_liq_weights(*, use_local_features=True, l2=1.0, rank_loss=False) -> dict:
    """Scaffold: fit Liquidity head when labeled CSVs appear in commercial/other/."""
    ensure_dirs()
    labels = _other_labeled_rows("liq", "liquidity")
    if len(labels) < 5:
        return {
            "error": "No Liquidity labeled rows yet",
            "fit": False,
            "rows": len(labels),
            "hint": (
                "Drop labeled Liquidity CSVs into data/weight_lab/commercial/other/ "
                "(filename containing liq/liquidity), then re-run fit_liq_weights."
            ),
        }
    feat_index = load_feature_index() if use_local_features else {}
    rows = []
    for lab in labels:
        label_feat = features_from_label_row(lab)
        if use_local_features and lab["ticker"] in feat_index:
            feat = merge_label_and_local(label_feat, feat_index[lab["ticker"]]["features"])
            source = "local+label"
        else:
            feat = label_feat
            source = "label_only"
        rows.append(
            {"ticker": lab["ticker"], "score": lab["score"], "rank": lab.get("rank"), "feat": feat, "source": source}
        )
    return _fit_and_write(rows, FEATURE_NAMES, WEIGHTS_LIQ_PATH, l2=l2, head="liq", rank_loss=rank_loss)


def fit_cluster_weights(*, use_local_features=True, l2=1.0, rank_loss=False) -> dict:
    """Scaffold: fit Cluster head when labeled CSVs appear in commercial/other/."""
    ensure_dirs()
    labels = _other_labeled_rows("cluster")
    if len(labels) < 5:
        return {
            "error": "No Cluster labeled rows yet",
            "fit": False,
            "rows": len(labels),
            "hint": (
                "Drop labeled Cluster CSVs into data/weight_lab/commercial/other/ "
                "(filename containing cluster), then re-run fit_cluster_weights."
            ),
        }
    feat_index = load_feature_index() if use_local_features else {}
    rows = []
    for lab in labels:
        label_feat = features_from_label_row(lab)
        if use_local_features and lab["ticker"] in feat_index:
            feat = merge_label_and_local(label_feat, feat_index[lab["ticker"]]["features"])
            source = "local+label"
        else:
            feat = label_feat
            source = "label_only"
        rows.append(
            {"ticker": lab["ticker"], "score": lab["score"], "rank": lab.get("rank"), "feat": feat, "source": source}
        )
    return _fit_and_write(
        rows, FEATURE_NAMES, WEIGHTS_CLUSTER_PATH, l2=l2, head="cluster", rank_loss=rank_loss
    )


def _fit_and_write(
    rows: list[dict],
    feature_names: list[str],
    out_path: Path,
    *,
    l2=1.0,
    head="cipher_model",
    rank_loss=False,
) -> dict:
    X_raw = np.vstack([vectorize(r["feat"], feature_names) for r in rows])
    y = np.array([r["score"] for r in rows], dtype=float)
    means = X_raw.mean(axis=0)
    stds = X_raw.std(axis=0)
    stds = np.where(stds < 1e-9, 1.0, stds)
    X = (X_raw - means) / stds

    fit_mode = "ridge"
    loss_note = "Default ordinary ridge (score MSE + L2)."
    if rank_loss:
        coef, yhat, r2 = _ridge_rank_hybrid(X, y, l2=l2)
        fit_mode = "hybrid_ridge_rank"
        loss_note = (
            "Hybrid: ridge MSE + L2 + softplus pairwise rank penalty "
            "(soft Kendall-style surrogate on score gaps). Reports both R² and τ."
        )
    else:
        coef, yhat, r2 = _ridge(X, y, l2=l2)

    pred_order = np.argsort(-yhat)
    true_ranks = []
    for i, r in enumerate(rows):
        true_ranks.append(r["rank"] if r["rank"] is not None else i + 1)
    pred_ranks = np.empty(len(rows), dtype=float)
    pred_ranks[pred_order] = np.arange(1, len(rows) + 1)
    tau = _kendall_tau(np.array(true_ranks, dtype=float), pred_ranks)
    warnings = _fit_warnings(len(rows), float(r2), float(tau))

    weights = {
        "as_of": _utcnow(),
        "head": head,
        "n": len(rows),
        "r_squared": round(r2, 4),
        "kendall_tau_rank": round(float(tau), 4),
        "l2": l2,
        "fit_mode": fit_mode,
        "loss": loss_note,
        "rank_loss": bool(rank_loss),
        "feature_names": feature_names,
        "means": means.tolist(),
        "stds": stds.tolist(),
        "intercept": float(coef[0]),
        "coefficients": {feature_names[i]: float(coef[i + 1]) for i in range(len(feature_names))},
        "label_sources": sorted({r["source"] for r in rows}),
        "local_feature_matches": sum(1 for r in rows if r["source"] == "local+label"),
        "warnings": warnings,
        "caveat": _CAVEAT_BASE + f" Fitted head={head}, mode={fit_mode}.",
        "sample_predictions": [
            {
                "ticker": rows[i]["ticker"],
                "label_score": rows[i]["score"],
                "pred_score": round(float(yhat[i]), 2),
                "label_rank": true_ranks[i],
                "pred_rank": int(pred_ranks[i]),
                "source": rows[i]["source"],
            }
            for i in pred_order[:10]
        ],
    }
    out_path.write_text(json.dumps(weights, indent=2), encoding="utf-8")
    return weights


def load_weights() -> dict | None:
    if not WEIGHTS_PATH.exists():
        return None
    return json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))


def load_flash_weights() -> dict | None:
    if not WEIGHTS_FLASH_PATH.exists():
        return None
    return json.loads(WEIGHTS_FLASH_PATH.read_text(encoding="utf-8"))


def _active_payload() -> dict:
    if not ACTIVE_PATH.exists():
        return {"active": False, "flash_active": False}
    try:
        return json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"active": False, "flash_active": False}


def is_active() -> bool:
    return bool(_active_payload().get("active"))


def is_flash_active() -> bool:
    return bool(_active_payload().get("flash_active"))


def _clear_scanner_cache():
    try:
        import scanner

        scanner._FITTED_WEIGHTS = None
        scanner._FITTED_ACTIVE = None
        scanner._FITTED_FLASH_WEIGHTS = None
        scanner._FITTED_FLASH_ACTIVE = None
    except Exception:
        pass


def set_active(active: bool) -> dict:
    ensure_dirs()
    payload = _active_payload()
    payload.update(
        {
            "active": bool(active),
            "as_of": _utcnow(),
            "weights": str(WEIGHTS_PATH) if WEIGHTS_PATH.exists() else None,
        }
    )
    payload.setdefault("flash_active", False)
    ACTIVE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _clear_scanner_cache()
    return payload


def set_flash_active(active: bool) -> dict:
    ensure_dirs()
    payload = _active_payload()
    payload.update(
        {
            "flash_active": bool(active),
            "as_of": _utcnow(),
            "weights_flash": str(WEIGHTS_FLASH_PATH) if WEIGHTS_FLASH_PATH.exists() else None,
        }
    )
    payload.setdefault("active", False)
    ACTIVE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _clear_scanner_cache()
    return payload


def score_features(feat: dict, weights: dict | None = None, *, lo=55.0, hi=92.0) -> float | None:
    weights = weights or load_weights()
    if not weights or "coefficients" not in weights:
        return None
    names = weights.get("feature_names") or FEATURE_NAMES
    means = np.array(weights["means"], dtype=float)
    stds = np.array(weights["stds"], dtype=float)
    stds = np.where(stds < 1e-9, 1.0, stds)
    x = np.array([float(feat.get(n) or 0.0) for n in names], dtype=float)
    z = (x - means) / stds
    coefs = np.array([weights["coefficients"].get(n, 0.0) for n in names], dtype=float)
    pred = float(weights["intercept"] + z @ coefs)
    return max(lo, min(hi, pred))


def score_flash_features(feat: dict, weights: dict | None = None) -> float | None:
    return score_features(feat, weights or load_flash_weights(), lo=50.0, hi=95.0)


def status() -> dict:
    ensure_dirs()
    commercial_files = sorted(p.name for p in COMMERCIAL.glob("*.csv"))
    other_files = sorted(p.name for p in COMMERCIAL_OTHER.glob("*.csv")) if COMMERCIAL_OTHER.exists() else []
    feature_files = sorted(p.name for p in FEATURES.glob("features_*.jsonl"))
    labels = load_all_commercial()
    flash_labels = load_flash_commercial()
    liq_labels = _other_labeled_rows("liq", "liquidity")
    cluster_labels = _other_labeled_rows("cluster")
    w = load_weights()
    wf = load_flash_weights()
    wliq = load_liq_weights()
    wcl = load_cluster_weights()
    csw = load_cluster_score_weights()
    fi = []
    fi_meta = {}
    if FLASH_INDEX_REF.exists():
        try:
            fi_meta = json.loads(FLASH_INDEX_REF.read_text(encoding="utf-8"))
            fi = fi_meta.get("rows") or []
        except Exception:
            fi = []
    elif other_files:
        fi = [
            {"ticker": r["ticker"], "score": r["score"], "rank": r.get("rank")}
            for r in load_flash_index_ref()
        ]
        try:
            fi_meta = json.loads(FLASH_INDEX_REF.read_text(encoding="utf-8"))
        except Exception:
            fi_meta = {}

    def _summary(payload):
        if not payload:
            return None
        return {
            "as_of": payload.get("as_of"),
            "n": payload.get("n"),
            "r_squared": payload.get("r_squared"),
            "kendall_tau_rank": payload.get("kendall_tau_rank"),
            "fit_mode": payload.get("fit_mode"),
            "local_feature_matches": payload.get("local_feature_matches"),
            "warnings": payload.get("warnings") or [],
            "caveat": payload.get("caveat"),
        }

    warnings: list[str] = []
    for payload in (w, wf):
        if payload:
            warnings.extend(payload.get("warnings") or [])
    if len(labels) < 30:
        warnings.append(f"Cipher commercial labels n={len(labels)}<30.")
    if len(flash_labels) < 30:
        warnings.append(f"Flash runway labels n={len(flash_labels)}<30.")

    return {
        "lab_dir": str(LAB),
        "commercial_files": commercial_files,
        "commercial_other_files": other_files,
        "commercial_rows": len(labels),
        "flash_rows": len(flash_labels),
        "flash_index_ref": fi,
        "feature_files": feature_files,
        "feature_tickers": len(load_feature_index()),
        "feature_names": FEATURE_NAMES,
        "flash_feature_names": FLASH_FEATURE_NAMES,
        "weights_fit": bool(w),
        "weights_summary": _summary(w),
        "flash_weights_fit": bool(wf),
        "flash_weights_summary": _summary(wf),
        "liq_head": {
            "fit": bool(wliq),
            "rows": len(liq_labels),
            "path": str(WEIGHTS_LIQ_PATH) if wliq else None,
            "hint": (
                "Drop labeled Liquidity CSVs into commercial/other/ (*liq*), then fit_liq_weights."
                if not wliq
                else "Liquidity head weights present."
            ),
            "summary": _summary(wliq),
        },
        "cluster_head": {
            "fit": bool(wcl),
            "rows": len(cluster_labels),
            "path": str(WEIGHTS_CLUSTER_PATH) if wcl else None,
            "hint": (
                "Drop labeled Cluster CSVs into commercial/other/ (*cluster*), then fit_cluster_weights."
                if not wcl
                else "Cluster head weights present."
            ),
            "summary": _summary(wcl),
        },
        "cluster_score_weights": {
            "path": str(CLUSTER_SCORE_WEIGHTS_PATH),
            "active": True,
            "hard_rank_order": csw.get("hard_rank_order"),
            "factors": csw.get("factors"),
            "kind_boost": csw.get("kind_boost"),
            "hint": "Cluster ranking: quad > triple > battle > … (edit cluster_score_weights.json).",
        },
        "flash_index_head": {
            "fit": False,
            "rows": len(fi),
            "path": str(FLASH_INDEX_REF) if FLASH_INDEX_REF.exists() else None,
            "hint": (
                "Reference-only (IWM/SPY/QQQ typical). Do not ridge-fit 3 rows — "
                "see flash_index_ref.json / README."
            ),
            "caveat": fi_meta.get("caveat") if fi_meta else None,
        },
        "active": is_active(),
        "flash_active": is_flash_active(),
        "warnings": warnings,
        "caveat": _CAVEAT_BASE,
    }
