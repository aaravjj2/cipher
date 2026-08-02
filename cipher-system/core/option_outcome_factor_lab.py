"""Mine factors that separated estimated option winners from losers.

This is intentionally lightweight because the current sample is tiny. It emits
hypotheses and a small ridge-style fit using only numpy, so no extra dependency
is needed.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTCOME_DIR = ROOT / "data" / "quad_outcomes"
OUT_DIR = ROOT / "data" / "factor_lab"


FEATURES = [
    "rank",
    "underlying_return_pct",
    "target_distance_pct",
    "long_moneyness_pct",
    "long_quote_width_pct",
    "long_volume",
    "long_oi",
    "long_delta",
    "long_gamma",
    "long_theta",
    "long_iv",
]

PRE_ENTRY_FEATURES = [
    "rank",
    "target_distance_pct",
    "long_moneyness_pct",
    "long_quote_width_pct",
    "long_volume",
    "long_oi",
    "long_delta",
    "long_gamma",
    "long_theta",
    "long_iv",
]

LEAKY_FEATURES = {
    "underlying_return_pct": "Known only after entry unless used as a delayed-confirmation rule.",
    "estimated_option_pnl_pct": "Outcome label.",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_outcomes() -> list[Path]:
    paths = []
    for structure in ("long_call", "spread"):
        matches = sorted(OUTCOME_DIR.glob(f"first_quad_outcome_{structure}_*.json"))
        if matches:
            paths.append(matches[-1])
    return paths


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        structure = payload.get("structure")
        for row in payload.get("rows") or []:
            if num(row.get("estimated_option_pnl_pct")) is None:
                continue
            out = dict(row)
            out["structure"] = structure
            out["source_path"] = str(path)
            rows.append(out)
    return rows


def clean_matrix(rows: list[dict[str, Any]], fields: list[str]) -> tuple[np.ndarray, np.ndarray, list[str], list[dict[str, Any]]]:
    usable = []
    for row in rows:
        if all(num(row.get(f)) is not None for f in fields) and num(row.get("estimated_option_pnl_pct")) is not None:
            usable.append(row)
    if not usable:
        return np.empty((0, 0)), np.empty((0,)), FEATURES, []
    x = np.array([[float(row[f]) for f in fields] for row in usable], dtype=float)
    y = np.array([float(row["estimated_option_pnl_pct"]) for row in usable], dtype=float)
    return x, y, fields, usable


def zscore(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std == 0] = 1.0
    return (x - mean) / std, mean, std


def correlations(x: np.ndarray, y: np.ndarray, fields: list[str]) -> list[dict[str, Any]]:
    out = []
    if len(y) < 3:
        return out
    for idx, field in enumerate(fields):
        col = x[:, idx]
        if np.std(col) == 0 or np.std(y) == 0:
            corr = 0.0
        else:
            corr = float(np.corrcoef(col, y)[0, 1])
        out.append({"feature": field, "correlation_to_pnl": round(corr, 4)})
    return sorted(out, key=lambda row: abs(row["correlation_to_pnl"]), reverse=True)


def ridge_fit(x: np.ndarray, y: np.ndarray, fields: list[str], alpha: float = 1.0) -> dict[str, Any]:
    if len(y) < 4:
        return {"available": False, "reason": "too_few_samples"}
    xz, mean, std = zscore(x)
    design = np.column_stack([np.ones(len(xz)), xz])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coef = np.linalg.pinv(design.T @ design + penalty) @ design.T @ y
    pred = design @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 0.0
    return {
        "available": True,
        "alpha": alpha,
        "r2_in_sample": round(r2, 4),
        "intercept": round(float(coef[0]), 4),
        "coefficients": [
            {"feature": field, "coef_on_zscore": round(float(value), 4)}
            for field, value in sorted(zip(fields, coef[1:]), key=lambda item: abs(float(item[1])), reverse=True)
        ],
        "caveat": "In-sample tiny-N fit. Use only as a ranking hypothesis.",
    }


def rule_tests(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = [
        ("target_distance_under_2pct", lambda r: (num(r.get("target_distance_pct")) or 999) < 2),
        ("target_distance_under_3pct", lambda r: (num(r.get("target_distance_pct")) or 999) < 3),
        ("golden_call_within_1pct_otm", lambda r: (num(r.get("long_moneyness_pct")) or 999) <= 1),
        ("golden_call_delta_over_0_35", lambda r: (num(r.get("long_delta")) or 0) > 0.35),
        ("golden_call_delta_0_25_to_0_55", lambda r: 0.25 <= (num(r.get("long_delta")) or -1) <= 0.55),
        ("quote_width_under_25pct", lambda r: (num(r.get("long_quote_width_pct")) or 999) < 25),
        ("iv_under_35pct", lambda r: (num(r.get("long_iv")) or 999) < 0.35),
        ("theta_less_negative_than_0_25", lambda r: (num(r.get("long_theta")) or -999) > -0.25),
        ("oi_under_1500", lambda r: (num(r.get("long_oi")) or 999999) < 1500),
    ]
    return summarize_rules(rows, rules)


def delayed_confirmation_rule_tests(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = [
        ("delayed_underlying_followthrough_positive", lambda r: (num(r.get("underlying_return_pct")) or 0) > 0),
        ("target_distance_under_2pct", lambda r: (num(r.get("target_distance_pct")) or 999) < 2),
        ("golden_call_delta_over_0_35", lambda r: (num(r.get("long_delta")) or 0) > 0.35),
        ("positive_followthrough_and_target_under_3pct", lambda r: (num(r.get("underlying_return_pct")) or 0) > 0 and (num(r.get("target_distance_pct")) or 999) < 3),
    ]
    return summarize_rules(rows, rules)


def summarize_rules(rows: list[dict[str, Any]], rules) -> list[dict[str, Any]]:
    out = []
    for name, fn in rules:
        selected = [r for r in rows if fn(r)]
        rejected = [r for r in rows if not fn(r)]
        def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
            vals = [float(r["estimated_option_pnl_pct"]) for r in group if num(r.get("estimated_option_pnl_pct")) is not None]
            if not vals:
                return {"n": 0}
            return {
                "n": len(vals),
                "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4),
                "avg_pnl_pct": round(sum(vals) / len(vals), 4),
                "median_pnl_pct": round(float(np.median(vals)), 4),
            }
        out.append({"rule": name, "selected": summarize(selected), "rejected": summarize(rejected)})
    return out


def build_report(paths: list[Path]) -> dict[str, Any]:
    rows = load_rows(paths)
    x_all, y_all, fields_all, usable_all = clean_matrix(rows, FEATURES)
    x_pre, y_pre, fields_pre, usable_pre = clean_matrix(rows, PRE_ENTRY_FEATURES)
    return {
        "generated_at": now_utc(),
        "sources": [str(p) for p in paths],
        "sample_count": len(rows),
        "usable_feature_rows": len(usable_all),
        "leaky_features_disregarded_for_pre_entry": LEAKY_FEATURES,
        "all_feature_correlations_for_diagnostics": correlations(x_all, y_all, fields_all) if len(usable_all) else [],
        "pre_entry_correlations": correlations(x_pre, y_pre, fields_pre) if len(usable_pre) else [],
        "pre_entry_ridge_fit": ridge_fit(x_pre, y_pre, fields_pre) if len(usable_pre) else {"available": False, "reason": "no_rows"},
        "pre_entry_rules": rule_tests(rows),
        "delayed_confirmation_rules": delayed_confirmation_rule_tests(rows),
        "rows": rows,
        "caveat": "Estimated option outcomes only. True scan-time option ticks were not captured.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Option Outcome Factor Lab",
        "",
        f"Generated: {report['generated_at']}",
        f"Samples: {report['sample_count']} total / {report['usable_feature_rows']} usable feature rows",
        "",
        "## Pre-Entry Correlations",
        "",
        "| Feature | Corr to P/L |",
        "|---|---:|",
    ]
    for row in report.get("pre_entry_correlations", [])[:10]:
        lines.append(f"| {row['feature']} | {row['correlation_to_pnl']} |")
    lines += [
        "",
        "Disregarded for pre-entry selection:",
        "",
    ]
    for feature, reason in (report.get("leaky_features_disregarded_for_pre_entry") or {}).items():
        lines.append(f"- {feature}: {reason}")
    lines += ["", "## Pre-Entry Rule Tests", "", "| Rule | Selected N | Selected Win | Selected Avg P/L | Rejected Avg P/L |", "|---|---:|---:|---:|---:|"]
    for row in report.get("pre_entry_rules", []):
        s = row["selected"]
        r = row["rejected"]
        lines.append(f"| {row['rule']} | {s.get('n')} | {s.get('win_rate')} | {s.get('avg_pnl_pct')} | {r.get('avg_pnl_pct')} |")
    lines += ["", "## Delayed Confirmation Diagnostics", "", "| Rule | Selected N | Selected Win | Selected Avg P/L | Rejected Avg P/L |", "|---|---:|---:|---:|---:|"]
    for row in report.get("delayed_confirmation_rules", []):
        s = row["selected"]
        r = row["rejected"]
        lines.append(f"| {row['rule']} | {s.get('n')} | {s.get('win_rate')} | {s.get('avg_pnl_pct')} | {r.get('avg_pnl_pct')} |")
    fit = report.get("pre_entry_ridge_fit") or {}
    if fit.get("available"):
        lines += ["", "## Tiny Ridge Fit", "", f"In-sample R2: {fit.get('r2_in_sample')}", "", "| Feature | Coef |", "|---|---:|"]
        for row in fit.get("coefficients", [])[:10]:
            lines.append(f"| {row['feature']} | {row['coef_on_zscore']} |")
    lines += ["", report["caveat"], ""]
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"option_outcome_factor_lab_{stamp}.json"
    md_path = OUT_DIR / f"option_outcome_factor_lab_{stamp}.md"
    csv_path = OUT_DIR / f"option_outcome_factor_lab_{stamp}.csv"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    fields = sorted({k for row in report["rows"] for k in row})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["rows"])
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outcome", action="append", type=Path)
    args = parser.parse_args()
    paths = args.outcome or latest_outcomes()
    report = build_report(paths)
    paths_out = write_outputs(report)
    print(json.dumps({
        "generated_at": report["generated_at"],
        "paths": paths_out,
        "pre_entry_top_correlations": report["pre_entry_correlations"][:5],
        "pre_entry_rules": report["pre_entry_rules"],
        "delayed_confirmation_rules": report["delayed_confirmation_rules"],
        "pre_entry_ridge_fit": report["pre_entry_ridge_fit"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
