"""Forward-safe option setup factor scorer.

Uses only factors known at scan/entry time. It can optionally require a delayed
confirmation flag supplied by a separate live mark/quote process, but it never
uses final P/L or future return labels when scoring.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTCOME_DIR = ROOT / "data" / "quad_outcomes"
OUT_DIR = ROOT / "data" / "factor_lab"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_long_call_outcome() -> Path:
    matches = sorted(OUTCOME_DIR.glob("first_quad_outcome_long_call_*.json"))
    if not matches:
        raise FileNotFoundError("Run first_quad_outcome.py --structure long-call first.")
    return matches[-1]


def score_row(row: dict[str, Any], *, require_confirmation: bool = False) -> dict[str, Any]:
    score = 0.0
    reasons = []
    target_distance = num(row.get("target_distance_pct"))
    moneyness = num(row.get("long_moneyness_pct"))
    delta = num(row.get("long_delta"))
    oi = num(row.get("long_oi"))
    iv = num(row.get("long_iv"))
    width = num(row.get("long_quote_width_pct"))
    rank = num(row.get("rank"))
    # Forward-safe factors from the current tiny sample.
    if target_distance is not None and target_distance < 2.0:
        score += 25
        reasons.append("target_under_2pct")
    elif target_distance is not None and target_distance < 3.0:
        score += 12
        reasons.append("target_under_3pct")
    else:
        score -= 10
        reasons.append("target_far")
    if moneyness is not None and moneyness <= 1.0:
        score += 22
        reasons.append("golden_call_within_1pct_otm")
    elif moneyness is not None and moneyness <= 2.5:
        score += 8
        reasons.append("call_reasonably_close")
    else:
        score -= 12
        reasons.append("call_far_otm")
    if delta is not None and delta > 0.35:
        score += 18
        reasons.append("delta_over_0_35")
    elif delta is not None and delta < 0.2:
        score -= 8
        reasons.append("low_delta")
    if oi is not None and oi < 1500:
        score += 10
        reasons.append("oi_not_crowded")
    if iv is not None and iv > 0.75:
        score -= 10
        reasons.append("very_high_iv")
    if width is not None and width > 50:
        score -= 10
        reasons.append("wide_option_quote")
    if rank is not None and rank <= 4:
        score += 5
        reasons.append("top4_quad")
    if require_confirmation:
        # Caller must set this from a live quote check after the scan; do not
        # use final outcome fields as confirmation in production.
        if bool(row.get("entry_confirmation")):
            score += 30
            reasons.append("live_entry_confirmation")
        else:
            score -= 30
            reasons.append("missing_live_entry_confirmation")
    grade = "A" if score >= 55 else "B" if score >= 35 else "C" if score >= 15 else "skip"
    return {**row, "pre_entry_score": round(score, 2), "pre_entry_grade": grade, "pre_entry_reasons": reasons}


def build_report(source: Path, require_confirmation: bool = False) -> dict[str, Any]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = [score_row(row, require_confirmation=require_confirmation) for row in payload.get("rows") or []]
    rows.sort(key=lambda r: (r["pre_entry_score"], -(num(r.get("rank")) or 999)), reverse=True)
    return {
        "generated_at": now_utc(),
        "source": str(source),
        "require_confirmation": bool(require_confirmation),
        "rows": rows,
        "caveat": "Forward-safe score uses only scan/entry-known fields. Outcome columns are retained only for retrospective evaluation.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Pre-Entry Factor Score",
        "",
        f"Generated: {report['generated_at']}",
        f"Source: `{report['source']}`",
        "",
        "| Rank | Ticker | Score | Grade | Outcome P/L | Reasons |",
        "|---:|---|---:|---|---:|---|",
    ]
    for idx, row in enumerate(report["rows"], start=1):
        lines.append(
            f"| {idx} | {row.get('ticker')} | {row.get('pre_entry_score')} | {row.get('pre_entry_grade')} | "
            f"{row.get('estimated_option_pnl_pct')} | {'; '.join(row.get('pre_entry_reasons') or [])} |"
        )
    lines += ["", report["caveat"], ""]
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"pre_entry_factor_score_{stamp}.json"
    csv_path = OUT_DIR / f"pre_entry_factor_score_{stamp}.csv"
    md_path = OUT_DIR / f"pre_entry_factor_score_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    fields = sorted({key for row in report["rows"] for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["rows"])
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--require-confirmation", action="store_true")
    args = parser.parse_args()
    report = build_report(args.source or latest_long_call_outcome(), require_confirmation=args.require_confirmation)
    paths = write_outputs(report)
    print(json.dumps({
        "generated_at": report["generated_at"],
        "paths": paths,
        "top": [
            {
                "ticker": row["ticker"],
                "score": row["pre_entry_score"],
                "grade": row["pre_entry_grade"],
                "retrospective_pnl_pct": row.get("estimated_option_pnl_pct"),
                "reasons": row["pre_entry_reasons"],
            }
            for row in report["rows"][:8]
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
