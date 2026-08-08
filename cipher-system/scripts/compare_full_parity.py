#!/usr/bin/env python3
"""Diff the real product against the local one, panel by panel and field by field.

Consumes the paired output of full_parity_capture.py. Three levels of comparison,
because they answer different questions:

  1. COVERAGE  — does each panel fetch anything at all on each side? A panel that
     renders from nothing is the failure mode that a numeric diff cannot see,
     because there is no number to compare.
  2. SHAPE     — do the payloads carry the same fields? A missing key is a feature
     gap; a differing key set explains numeric gaps further down.
  3. VALUES    — for the shared numeric fields, how far apart are they?

Scalars are compared directly. Equal-length numeric arrays are compared
element-wise. Nothing is compared across a length mismatch, because pairing the
first N of two differently-sized grids invents an alignment that is not there.

Usage:
  python3 scripts/compare_full_parity.py                 # newest pair
  python3 scripts/compare_full_parity.py --real X --local Y
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_DIR = ROOT / "data" / "full_parity"
OUT_DIR = ROOT / "data" / "parity_reports"

# Fields whose disagreement is expected and uninformative: capture timestamps and
# feed labels differ by construction.
IGNORE_KEYS = {"updated", "as_of", "captured_at", "feed", "schema_version", "caveat"}
# Below this, a relative difference is noise from rounding rather than modelling.
FLOOR = 1e-6


def newest(prefix):
    files = sorted(IN_DIR.glob(f"{prefix}_*.json"))
    return files[-1] if files else None


def endpoint_name(url):
    return (url or "").split("?")[0].rsplit("/", 1)[-1] or "(root)"


# The two products name and slice their endpoints differently, so a raw name match
# finds nothing. Each side is mapped onto a role instead.
#
# The real product serves ONE grid endpoint, `heatmap`, and derives Strike Matrix,
# Night Vision and Trident from it client-side. Ours splits the same data across
# `matrix` (grid), `night-vision` (grid plus levels) and a separate `quote`. That
# is a real architectural difference, not a defect — but it means our app makes
# more round trips per panel than the real one.
ROLE_ALIASES = {
    "heatmap": "grid",
    "matrix": "grid",
    "night-vision": "grid",
    "candles": "bars",
    "bars": "bars",
    "flow": "flow",
    "spyglass": "flow",
    "quote": "quote",
}


def normalise_grid(payload):
    """Common summary from either grid shape.

    Theirs is parallel arrays (`strikes`, `gex`, ...); ours is `rows[].cells[]` on
    /api/matrix and /api/night-vision. Comparing them field-path by field-path is
    meaningless, so both are reduced to the quantities that mean the same thing on
    either side. The full cell-by-cell diff lives in compare_ticker_views.py, which
    calls the matching endpoint directly.
    """
    if not isinstance(payload, dict):
        return {}
    out = {}
    for key in ("spot", "day_change_pct", "contracts"):
        if isinstance(payload.get(key), (int, float)):
            out[key] = payload[key]
    quote = payload.get("quote")
    if isinstance(quote, dict):
        if isinstance(quote.get("price_context"), (int, float)):
            out.setdefault("spot", quote["price_context"])
        if isinstance(quote.get("day_change_pct"), (int, float)):
            out.setdefault("day_change_pct", quote["day_change_pct"])
    exps = payload.get("expirations")
    if isinstance(exps, list):
        out["expiration_count"] = len(exps)
    strikes = payload.get("strikes")
    if isinstance(strikes, list):
        out["strike_count"] = len(strikes)
        nums = [s for s in strikes if isinstance(s, (int, float))]
        if nums:
            out["strike_min"], out["strike_max"] = min(nums), max(nums)
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        out["strike_count"] = len(rows)
        nums = [r.get("strike") for r in rows if isinstance(r.get("strike"), (int, float))]
        if nums:
            out["strike_min"], out["strike_max"] = min(nums), max(nums)
    return out


def index_calls(panel):
    """{role: json} for one panel's recorded calls, keyed by what the call is FOR."""
    out = {}
    for call in (panel or {}).get("calls", []) or []:
        payload = call.get("json")
        if not isinstance(payload, (dict, list)):
            continue
        role = ROLE_ALIASES.get(endpoint_name(call.get("url")))
        if role is None:
            continue
        # Richest payload wins when a side hits several endpoints for one role.
        prior = out.get(role)
        if prior is None or len(json.dumps(payload)) > len(json.dumps(prior)):
            out[role] = payload
    return out


def numeric_leaves(node, prefix="", out=None, depth=0):
    """{path: value} for scalars, and {path: list} for flat numeric arrays."""
    if out is None:
        out = {}
    if depth > 6:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            if key in IGNORE_KEYS:
                continue
            numeric_leaves(value, f"{prefix}.{key}" if prefix else key, out, depth + 1)
    elif isinstance(node, list):
        if node and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in node):
            out[prefix] = list(node)
        elif node and all(isinstance(x, list) for x in node):
            flat = []
            for row in node:
                for x in row:
                    if isinstance(x, (int, float)) and not isinstance(x, bool):
                        flat.append(x)
                    elif x is None:
                        flat.append(None)
            if flat:
                out[prefix] = flat
        else:
            for i, item in enumerate(node[:3]):
                numeric_leaves(item, f"{prefix}[{i}]", out, depth + 1)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out[prefix] = node
    return out


def rel(a, b):
    scale = max(abs(a), abs(b))
    return None if scale < FLOOR else abs(a - b) / scale


def compare_payload(real, local):
    r_leaves, l_leaves = numeric_leaves(real), numeric_leaves(local)
    shared = sorted(set(r_leaves) & set(l_leaves))
    only_real = sorted(set(r_leaves) - set(l_leaves))
    only_local = sorted(set(l_leaves) - set(r_leaves))

    field_rows = []
    for path in shared:
        rv, lv = r_leaves[path], l_leaves[path]
        if isinstance(rv, list) != isinstance(lv, list):
            field_rows.append({"field": path, "note": "type mismatch"})
            continue
        if isinstance(rv, list):
            if len(rv) != len(lv):
                field_rows.append({"field": path, "note": f"length {len(rv)} vs {len(lv)}"})
                continue
            errs = [rel(a, b) for a, b in zip(rv, lv)
                    if isinstance(a, (int, float)) and isinstance(b, (int, float))]
            errs = [e for e in errs if e is not None]
            if errs:
                field_rows.append({
                    "field": path, "n": len(errs),
                    "median_rel_err_pct": round(100 * statistics.median(errs), 4),
                    "max_rel_err_pct": round(100 * max(errs), 4),
                })
        else:
            err = rel(rv, lv)
            field_rows.append({
                "field": path, "real": rv, "local": lv,
                "rel_err_pct": round(100 * err, 4) if err is not None else 0.0,
            })
    return {"fields": field_rows, "only_real": only_real, "only_local": only_local}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", default="")
    ap.add_argument("--local", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    real_path = Path(args.real) if args.real else newest("real")
    local_path = Path(args.local) if args.local else newest("local")
    if not real_path or not local_path:
        print("need both a real and a local capture — run full_parity_capture.py",
              file=sys.stderr)
        return 1
    print(f"real  : {real_path.name}")
    print(f"local : {local_path.name}\n")

    real = json.loads(real_path.read_text())
    local = json.loads(local_path.read_text())
    report = {"real": real_path.name, "local": local_path.name, "symbols": {}}

    for symbol in sorted(set(real.get("symbols", {})) & set(local.get("symbols", {}))):
        print(f"=== {symbol} ===")
        r_sym, l_sym = real["symbols"][symbol], local["symbols"][symbol]
        sym_report = {}
        for panel in sorted(set(r_sym.get("panels", {})) | set(l_sym.get("panels", {}))):
            r_panel = r_sym.get("panels", {}).get(panel, {})
            l_panel = l_sym.get("panels", {}).get(panel, {})
            r_calls, l_calls = index_calls(r_panel), index_calls(l_panel)

            # ── coverage ────────────────────────────────────────────────────
            if not r_calls and not l_calls:
                print(f"  {panel:<14} both sides fetched nothing")
                sym_report[panel] = {"status": "no data either side"}
                continue
            if not l_calls:
                print(f"  {panel:<14} LOCAL FETCHED NOTHING (real: {sorted(r_calls)})")
                sym_report[panel] = {"status": "local missing", "real_endpoints": sorted(r_calls)}
                continue
            if not r_calls:
                print(f"  {panel:<14} real fetched nothing (local: {sorted(l_calls)})")
                sym_report[panel] = {"status": "real missing", "local_endpoints": sorted(l_calls)}
                continue

            shared_eps = sorted(set(r_calls) & set(l_calls))
            panel_report = {
                "status": "compared",
                "shared_endpoints": shared_eps,
                "only_real_endpoints": sorted(set(r_calls) - set(l_calls)),
                "only_local_endpoints": sorted(set(l_calls) - set(r_calls)),
                "endpoints": {},
            }
            if not shared_eps:
                print(f"  {panel:<14} no shared endpoint  real={sorted(r_calls)} local={sorted(l_calls)}")
            for ep in shared_eps:
                r_payload, l_payload = r_calls[ep], l_calls[ep]
                if ep == "grid":
                    r_payload, l_payload = normalise_grid(r_payload), normalise_grid(l_payload)
                cmp = compare_payload(r_payload, l_payload)
                panel_report["endpoints"][ep] = cmp
                errs = [f["rel_err_pct"] for f in cmp["fields"] if "rel_err_pct" in f]
                errs += [f["median_rel_err_pct"] for f in cmp["fields"] if "median_rel_err_pct" in f]
                med = statistics.median(errs) if errs else None
                worst = sorted(
                    (f for f in cmp["fields"] if f.get("rel_err_pct", 0) > 1
                     or f.get("median_rel_err_pct", 0) > 1),
                    key=lambda f: -(f.get("rel_err_pct") or f.get("median_rel_err_pct") or 0),
                )[:3]
                print(f"  {panel:<14} {ep:<16} fields={len(cmp['fields']):<4} "
                      f"median={med if med is None else round(med,4)}%  "
                      f"only_real={len(cmp['only_real'])} only_local={len(cmp['only_local'])}")
                for f in worst:
                    val = f.get("rel_err_pct") or f.get("median_rel_err_pct")
                    print(f"      ! {f['field']}: {val}%")
            sym_report[panel] = panel_report
        report["symbols"][symbol] = sym_report

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT_DIR / f"full_parity_{real_path.stem.split('_',1)[1]}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
