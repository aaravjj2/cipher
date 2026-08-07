#!/usr/bin/env python3
"""Cell-level parity diff of the real product's /api/heatmap against ours.

This replaces scraping the rendered UI. The real app's own network calls carry the
exact payload it renders from — spot, expirations, strikes, and the gex/vex/oi
surfaces — so parity can be measured on numbers instead of formatted text.

Two things this deliberately gets right, because getting them wrong produces
confident nonsense:

  * **Matched parameters.** Their payload arrives with whatever depth and
    expiration count the app asked for. Our endpoint defaults to depth=0.06 and
    36 expirations, which on NVDA gives 24 strikes against their 110 — a
    difference that looks like a huge modelling gap and is purely a default.
    This script reads their grid and asks ours for the same one.
  * **Drift.** Their capture and our call are seconds apart, and spot moves. Spot
    drift is reported alongside every diff so a market move is never read as a
    modelling error.

Usage:
    python3 scripts/capture_ticker_views.py --symbols NVDA,AAPL,SPY
    python3 scripts/compare_ticker_views.py            # newest capture
    python3 scripts/compare_ticker_views.py --views data/ticker_views/views_X.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS_DIR = ROOT / "data" / "ticker_views"
OUT_DIR = ROOT / "data" / "parity_reports"
LOCAL_API = "http://127.0.0.1:8283"

# Surfaces compared cell by cell. `oi` is included because it is the input GEX is
# built from — if OI matches and GEX does not, the difference is in the model, not
# the data.
SURFACES = ("gex", "vex", "oi", "call_oi", "put_oi")


def local_heatmap(ticker: str, depth: str, expirations: int, timeout: int = 180) -> dict:
    qs = urllib.parse.urlencode({"ticker": ticker, "depth": depth,
                                 "expirations": expirations})
    with urllib.request.urlopen(f"{LOCAL_API}/api/heatmap?{qs}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def their_heatmap(entry: dict) -> dict | None:
    for c in entry.get("calls", []):
        if "heatmap" in (c.get("url") or "") and isinstance(c.get("json"), dict):
            return c["json"]
    return None


def rel_err(a: float, b: float) -> float | None:
    """Relative difference against the larger magnitude, so tiny cells do not
    dominate the summary with meaningless percentages."""
    scale = max(abs(a), abs(b))
    if scale == 0:
        return None
    return abs(a - b) / scale


def compare_one(sym: str, theirs: dict, floor: float) -> dict:
    their_strikes = theirs.get("strikes") or []
    their_exps = theirs.get("expirations") or []
    ours = local_heatmap(sym, "all", len(their_exps))

    our_strikes = ours.get("strikes") or []
    our_exps = ours.get("expirations") or []

    si = {s: i for i, s in enumerate(our_strikes)}
    ei = {e: i for i, e in enumerate(our_exps)}
    shared_strikes = [s for s in their_strikes if s in si]
    shared_exps = [e for e in their_exps if e in ei]

    out = {
        "ticker": sym,
        "their_spot": theirs.get("spot"),
        "our_spot": ours.get("spot"),
        "spot_drift_pct": None,
        "their_grid": [len(their_strikes), len(their_exps)],
        "our_grid": [len(our_strikes), len(our_exps)],
        "strikes_matched": len(shared_strikes),
        "strikes_missing_from_ours": [s for s in their_strikes if s not in si][:20],
        "expirations_matched": len(shared_exps),
        "expirations_missing_from_ours": [e for e in their_exps if e not in ei],
        "surfaces": {},
    }
    ts, os_ = theirs.get("spot"), ours.get("spot")
    if ts and os_:
        out["spot_drift_pct"] = round(abs(ts - os_) / ts * 100, 4)

    for surface in SURFACES:
        tgrid, ogrid = theirs.get(surface), ours.get(surface)
        if not isinstance(tgrid, list) or not isinstance(ogrid, list):
            continue
        errs, n_cmp, n_sig, sign_flips = [], 0, 0, 0
        for s in shared_strikes:
            trow = tgrid[their_strikes.index(s)]
            orow = ogrid[si[s]]
            for e in shared_exps:
                tv = trow[their_exps.index(e)]
                ov = orow[ei[e]]
                if tv is None or ov is None:
                    continue
                tv, ov = float(tv), float(ov)
                n_cmp += 1
                if tv * ov < 0:
                    sign_flips += 1
                # Only cells big enough to matter — near-zero cells produce huge
                # relative errors that say nothing about whether the model agrees.
                if max(abs(tv), abs(ov)) < floor:
                    continue
                e_rel = rel_err(tv, ov)
                if e_rel is not None:
                    errs.append(e_rel)
                    n_sig += 1
        out["surfaces"][surface] = {
            "cells_compared": n_cmp,
            "cells_above_floor": n_sig,
            "sign_flips": sign_flips,
            "median_rel_err_pct": round(statistics.median(errs) * 100, 3) if errs else None,
            "p90_rel_err_pct": (round(sorted(errs)[int(len(errs) * 0.9)] * 100, 3)
                                if len(errs) >= 10 else None),
            "within_1pct": (round(100 * sum(1 for e in errs if e <= 0.01) / len(errs), 1)
                            if errs else None),
            "within_5pct": (round(100 * sum(1 for e in errs if e <= 0.05) / len(errs), 1)
                            if errs else None),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--views", default="")
    ap.add_argument("--floor", type=float, default=1e4,
                    help="ignore cells smaller than this in both feeds")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    path = Path(args.views) if args.views else None
    if path is None:
        files = sorted(VIEWS_DIR.glob("views_*.json"))
        if not files:
            print("no capture found — run capture_ticker_views.py first", file=sys.stderr)
            return 1
        path = files[-1]
    print(f"comparing against {path.name}\n")

    entries = json.loads(path.read_text())
    reports = []
    for entry in entries:
        sym = entry.get("requested")
        theirs = their_heatmap(entry)
        if not theirs:
            print(f"{sym:<6} no heatmap payload captured")
            continue
        if not entry.get("switched"):
            print(f"{sym:<6} SKIPPED — app did not switch to this ticker")
            continue
        try:
            rep = compare_one(sym, theirs, args.floor)
        except Exception as exc:  # noqa: BLE001
            print(f"{sym:<6} local API failed: {exc}")
            continue
        reports.append(rep)
        g = rep["surfaces"].get("gex") or {}
        o = rep["surfaces"].get("oi") or {}
        print(f"{sym:<6} grid {rep['their_grid']}vs{rep['our_grid']} "
              f"strikes {rep['strikes_matched']}/{rep['their_grid'][0]} "
              f"drift {rep['spot_drift_pct']}%  "
              f"OI med {o.get('median_rel_err_pct')}% <1%:{o.get('within_1pct')}  "
              f"GEX med {g.get('median_rel_err_pct')}% <5%:{g.get('within_5pct')} "
              f"flips {g.get('sign_flips')}")

    if reports:
        print("\n=== aggregate ===")
        for surface in SURFACES:
            meds = [r["surfaces"][surface]["median_rel_err_pct"] for r in reports
                    if r["surfaces"].get(surface, {}).get("median_rel_err_pct") is not None]
            flips = sum(r["surfaces"].get(surface, {}).get("sign_flips") or 0 for r in reports)
            cells = sum(r["surfaces"].get(surface, {}).get("cells_compared") or 0 for r in reports)
            if meds:
                print(f"{surface:<8} median rel err {statistics.median(meds):.3f}%  "
                      f"cells {cells}  sign flips {flips}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT_DIR / f"parity_{path.stem}.json"
    out.write_text(json.dumps(reports, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
