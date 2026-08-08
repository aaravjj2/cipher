#!/usr/bin/env python3
"""Capture the real product's scan and our equivalent at the same moment, then diff.

Why this exists: accuracy work on the cluster scanner kept producing measurements
that could not be trusted, because the real-product reference and the local scan were
taken hours apart. Between an overnight capture and a mid-morning re-test, spot drifted
a mean of 1.8% (max 6.4%, 19 of 31 tickers over 1%) — enough to move the clusters
themselves, which made a genuine improvement and a genuine regression look identical.

Running both sides back to back removes that ambiguity. Anything this reports as a
difference is a real modelling difference, not the market having moved.

Usage:
    python3 scripts/paired_capture.py                  # cluster, default
    python3 scripts/paired_capture.py --modes cluster flash
    python3 scripts/paired_capture.py --local-only     # reuse newest real capture

Read-only: captures visible UI output and calls the local read-only API. Places no
orders and touches no broker endpoints.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = ROOT / "scripts" / "capture_accessobsidian_scans.py"
SCANS_DIR = ROOT / "data" / "accessobsidian_scans"
OUT_DIR = ROOT / "data" / "paired_reports"
LOCAL_API = "http://127.0.0.1:8283"

# Real "setup" strings look like "TRIPLE UPSIDE" / "QUAD DOWNSIDE".
STRATEGY_FOR_MODE = {"cluster": "cluster", "flash": "flash", "flash_index": "flash_index",
                     "flash_agentic": "flash"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def number(value):
    """Float, or None for anything unparseable. Captured cards carry strings."""
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def newest_capture(mode: str) -> Path | None:
    files = sorted(SCANS_DIR.glob(f"*/*/{mode}.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def run_real_capture(modes: list[str], timeout_seconds: int) -> None:
    cmd = [
        sys.executable, str(CAPTURE_SCRIPT),
        "--modes", *modes,
        "--timeout-seconds", str(timeout_seconds),
        "--serial",
    ]
    print(f"[{utcnow()}] real capture: {' '.join(modes)} …", flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT.parent), capture_output=True, text=True,
                          timeout=timeout_seconds + 180)
    if proc.returncode != 0:
        print(f"  capture exited {proc.returncode}: {proc.stderr[-400:]}", file=sys.stderr)


def local_scan(tickers: list[str], strategy: str, workers: int, timeout: int) -> dict:
    params = urllib.parse.urlencode({
        "tickers": ",".join(tickers), "mode": "short", "strategy": strategy,
        "limit": max(len(tickers), 40), "workers": workers,
    })
    url = f"{LOCAL_API}/api/scan?{params}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_real_row(row: dict) -> dict:
    setup = (row.get("setup") or "").upper()
    return {
        "ticker": row.get("ticker"),
        "kind": "quad" if "QUAD" in setup else ("triple" if "TRIPLE" in setup else None),
        "side": "above" if "UPSIDE" in setup else ("below" if "DOWNSIDE" in setup else None),
        "target": row.get("cluster_target"),
        "strength": row.get("strength"),
        "spot": row.get("spot"),
    }


def parse_local_row(item: dict) -> dict:
    st = (item.get("setup_type") or "").upper()
    return {
        "ticker": item.get("ticker"),
        "kind": "quad" if "QUAD" in st else ("triple" if "TRIPLE" in st else None),
        "side": "above" if "ABOVE" in st else ("below" if "BELOW" in st else None),
        "target": item.get("target"),
        "strength": item.get("strength"),
        "spot": item.get("spot"),
    }


PAIRED_DIR = ROOT / "data" / "weight_lab" / "paired"


def diff_flash(real_rows: list[dict], local_top: list[dict], mode: str, stamp: str) -> dict:
    """Emit paired flash training records: the real card's score beside our features.

    This is the only honest way to build a label corpus for the flash head. The
    fitted head shipped on 22 rows from one session because the fitter could only
    read `data/weight_lab/commercial/**` — hand-exported CSVs — while hundreds of
    captured cards sat unused in `data/accessobsidian_scans/**` with no matching
    local features to pair them against.

    Post-hoc joining those captures to today's chain would be lookahead
    contamination: a 2026-07-23 card scored against a 2026-08-08 surface. So rows
    accrue forward from here, captured in the same moment as the local scan, which
    is what this script already guarantees via its spot-drift assertion.

    Written to data/weight_lab/paired/ rather than data/weight_lab/features/,
    because load_feature_index() is latest-per-ticker and would collapse a time
    series to a single row.
    """
    local_by_ticker = {}
    for item in local_top or []:
        tk = (item.get("ticker") or "").upper()
        if tk:
            local_by_ticker[tk] = item

    records, matched, drifts = [], 0, []
    for real in real_rows or []:
        tk = str(real.get("ticker") or "").upper().lstrip("$")
        if not tk or real.get("score") is None:
            continue
        local = local_by_ticker.get(tk)
        if not local:
            continue
        matched += 1
        real_spot, local_spot = number(real.get("spot")), number(local.get("spot"))
        drift = (abs(local_spot - real_spot) / real_spot * 100.0
                 if real_spot and local_spot else None)
        if drift is not None:
            drifts.append(drift)
        flash = local.get("flash") or {}
        records.append({
            "captured_at": utcnow(),
            "session_date": datetime.now(timezone.utc).date().isoformat(),
            "mode": mode,
            "ticker": tk,
            # Labels from the real card.
            "score": real.get("score"),
            "edge": real.get("edge"),
            "rank": real.get("rank"),
            "runway_clarity_pct": real.get("runway_clarity_pct"),
            "setup": real.get("setup"),
            "direction": real.get("bias") or real.get("direction"),
            "real_spot": real_spot,
            "real_pivot": number(real.get("pivot")),
            "real_first_target": number(real.get("first_target")),
            "real_stretch": number(real.get("stretch")),
            "real_invalidation": number(real.get("invalidation")),
            # Our side, from the same moment.
            "local_spot": local_spot,
            "local_score": local.get("score"),
            "local_score_source": flash.get("score_source"),
            "local_dte": flash.get("dte"),
            "local_first_target": flash.get("first_target"),
            "local_components": flash.get("components"),
            "support_count": local.get("support_count"),
            "resistance_count": local.get("resistance_count"),
            "vacuum_count": local.get("vacuum_count"),
            "spot_drift_pct": None if drift is None else round(drift, 4),
        })

    summary = {"real_rows": len(real_rows or []), "matched_local": matched,
               "records_written": 0}
    if drifts:
        summary["spot_drift_pct_max"] = round(max(drifts), 3)
        # Same bar the cluster diff uses: beyond this the two sides were not
        # simultaneous and the pairing is not a pairing.
        summary["paired_ok"] = max(drifts) < 0.5

    if records and summary.get("paired_ok", True):
        PAIRED_DIR.mkdir(parents=True, exist_ok=True)
        out = PAIRED_DIR / f"paired_{mode}_{stamp}.jsonl"
        with out.open("w", encoding="utf-8") as handle:
            for rec in records:
                handle.write(json.dumps(rec, default=str) + "\n")
        summary["records_written"] = len(records)
        summary["path"] = str(out)
    elif records:
        summary["skipped"] = "spot drift too large; not written"
    return {"summary": summary, "rows": []}


def diff_cluster(real_rows: list[dict], local_top: list[dict]) -> dict:
    real = {r["ticker"]: r for r in (parse_real_row(x) for x in real_rows) if r["ticker"]}
    local = {r["ticker"]: r for r in (parse_local_row(x) for x in local_top) if r["ticker"]}
    rows, kind_ok, side_ok, both_ok, tgt_ok, missing = [], 0, 0, 0, 0, 0
    drifts, pairs = [], []
    for tk, r in real.items():
        l = local.get(tk)
        if not l:
            missing += 1
            rows.append({"ticker": tk, "status": "missing_local", "real": r})
            continue
        k = r["kind"] == l["kind"]
        s = r["side"] == l["side"]
        t = (r["target"] is not None and l["target"] is not None
             and abs(l["target"] - r["target"]) < 0.01)
        kind_ok += k; side_ok += s; both_ok += (k and s); tgt_ok += t
        if r["spot"] and l["spot"]:
            drifts.append(abs(l["spot"] - r["spot"]) / r["spot"] * 100)
        if r["strength"] and l["strength"]:
            pairs.append((l["strength"], r["strength"]))
        rows.append({"ticker": tk, "status": "ok" if (k and s) else "mismatch",
                     "real": r, "local": l, "kind_match": k, "side_match": s,
                     "target_match": t})
    n = len(real)
    summary = {
        "tickers": n,
        "kind_match": kind_ok, "side_match": side_ok, "both_match": both_ok,
        "target_exact": tgt_ok, "missing_local": missing,
        "kind_pct": round(100 * kind_ok / max(n, 1), 1),
        "side_pct": round(100 * side_ok / max(n, 1), 1),
        "both_pct": round(100 * both_ok / max(n, 1), 1),
    }
    if drifts:
        summary["spot_drift_pct_mean"] = round(sum(drifts) / len(drifts), 3)
        summary["spot_drift_pct_max"] = round(max(drifts), 3)
        # The whole point of pairing: this should be ~0. If it is not, the two sides
        # were not actually simultaneous and the diff below is not trustworthy.
        summary["paired_ok"] = max(drifts) < 0.5
    if len(pairs) > 2:
        lv = [p[0] for p in pairs]; rv = [p[1] for p in pairs]
        mean_l = sum(lv) / len(lv); mean_r = sum(rv) / len(rv)
        cov = sum((a - mean_l) * (b - mean_r) for a, b in zip(lv, rv))
        var_l = sum((a - mean_l) ** 2 for a in lv) ** 0.5
        var_r = sum((b - mean_r) ** 2 for b in rv) ** 0.5
        if var_l and var_r:
            summary["strength_corr"] = round(cov / (var_l * var_r), 3)
        summary["strength_mean_ratio"] = round(mean_l / mean_r, 3) if mean_r else None
    return {"summary": summary, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", nargs="+", default=["cluster"])
    ap.add_argument("--timeout-seconds", type=int, default=900)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--local-only", action="store_true",
                    help="Skip the browser capture and reuse the newest one on disk.")
    args = ap.parse_args()

    if not args.local_only:
        run_real_capture(args.modes, args.timeout_seconds)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report = {"generated_at": utcnow(), "modes": {}}

    for mode in args.modes:
        path = newest_capture(mode)
        if not path:
            report["modes"][mode] = {"error": f"no capture found for {mode}"}
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        real_rows = payload.get("rows") or []
        tickers = [r["ticker"] for r in real_rows if r.get("ticker")]
        if not tickers:
            report["modes"][mode] = {"error": "capture had no tickers"}
            continue
        age = time.time() - path.stat().st_mtime
        print(f"[{utcnow()}] {mode}: {len(tickers)} tickers, capture age {age:.0f}s", flush=True)
        strategy = STRATEGY_FOR_MODE.get(mode, mode)
        try:
            local = local_scan(tickers, strategy, args.workers, args.timeout_seconds)
        except Exception as exc:
            report["modes"][mode] = {"error": f"local scan failed: {exc}"}
            continue
        if mode == "cluster":
            entry = diff_cluster(real_rows, local.get("top") or [])
        elif mode in {"flash", "flash_agentic", "flash_index"}:
            entry = diff_flash(real_rows, local.get("top") or [], mode, stamp)
        else:
            entry = {"summary": {"tickers": len(tickers),
                                 "local_qualified": local.get("qualified")}, "rows": []}
        entry["capture_file"] = str(path)
        entry["capture_age_seconds"] = round(age, 1)
        entry["local_elapsed_ms"] = local.get("elapsed_ms")
        report["modes"][mode] = entry
        s = entry["summary"]
        print(f"  {mode}: " + "  ".join(f"{k}={v}" for k, v in s.items()), flush=True)

    out = OUT_DIR / f"paired_{stamp}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (OUT_DIR / "latest.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"[{utcnow()}] report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
