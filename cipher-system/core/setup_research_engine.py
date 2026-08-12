"""Rank captured scanner setups for read-only option-buy research.

This module combines the local AccessObsidian scanner captures, quad ledger,
and SPY/QQQ/IWM daytrade context into a compact "is it worth researching?"
score. It intentionally does not place orders or call broker trading APIs.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCAN_DIR = DATA / "accessobsidian_scans"
CONTEXT_DIR = DATA / "index_daytrade_context"
OUT_DIR = DATA / "setup_research"


QQQ_HEAVY = {
    "AAPL", "ADBE", "AMD", "AMAT", "AMGN", "AMZN", "ASML", "AVGO", "COST",
    "GOOG", "GOOGL", "INTC", "META", "MSFT", "NFLX", "NVDA", "ORCL", "QCOM",
    "TSLA", "TXN",
}
IWM_STYLE = {
    "AI", "CLF", "DKNG", "EOSE", "GME", "HTZ", "MARA", "PLUG", "QS", "RGTI",
    "RKT", "SOUN", "TLRY",
}
SPY_CORE = {
    "ABT", "BABA", "BMY", "CAT", "COF", "COP", "CSCO", "CVX", "DECK", "FDX",
    "GE", "GS", "JNJ", "JPM", "KO", "LMT", "MA", "MCD", "MO", "MRK", "ORCL",
    "PEP", "SBUX", "UNH", "V", "XOM",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_file(root: Path, pattern: str) -> Path | None:
    files = sorted(root.glob(pattern))
    return files[-1] if files else None


def latest_scan_run(scan_dir: Path) -> Path:
    summaries = sorted(scan_dir.glob("20*/20*/summary.json"), reverse=True)
    for summary in summaries:
        cluster_path = summary.parent / "cluster.json"
        if not cluster_path.is_file():
            continue
        try:
            rows = load_json(cluster_path).get("rows") or []
        except (OSError, json.JSONDecodeError):
            continue
        if rows:
            return summary.parent
    raise FileNotFoundError(f"No usable scanner summary with cluster rows found under {scan_dir}")


def latest_context(context_dir: Path) -> dict[str, Any] | None:
    path = latest_file(context_dir, "index_daytrade_context_*.json")
    return load_json(path) if path else None


def rows_for(run_dir: Path, name: str) -> list[dict[str, Any]]:
    path = run_dir / f"{name}.json"
    if not path.is_file():
        return []
    return list(load_json(path).get("rows") or [])


def direction_from_text(text: Any) -> str:
    raw = str(text or "").upper()
    if "DOWNSIDE" in raw or raw in {"BEARISH", "SHORT", "PUT"}:
        return "down"
    if "UPSIDE" in raw or raw in {"BULLISH", "LONG", "CALL"}:
        return "up"
    return "neutral"


def to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def target_distance_pct(row: dict[str, Any]) -> float | None:
    spot = to_float(row.get("spot"))
    target = to_float(row.get("cluster_target") or row.get("target") or row.get("pull_target"))
    if not spot or target is None:
        return None
    return abs(target - spot) / spot * 100.0


def index_bucket(ticker: str) -> str:
    ticker = ticker.upper()
    if ticker in QQQ_HEAVY:
        return "QQQ"
    if ticker in IWM_STYLE:
        return "IWM"
    if ticker in SPY_CORE:
        return "SPY"
    return "SPY"


def index_score_adjustment(direction: str, bucket: str, context: dict[str, Any] | None) -> tuple[float, str]:
    if not context:
        return 0.0, "no_index_context"
    index_by_ticker = {row.get("ticker"): row for row in context.get("index_context") or []}
    row = index_by_ticker.get(bucket)
    if not row:
        return 0.0, "index_context_missing"
    regime = str(row.get("regime") or "").lower()
    spot = to_float(row.get("spot"))
    call_wall = to_float(row.get("call_wall"))
    put_wall = to_float(row.get("put_wall"))
    flip = to_float(row.get("gamma_flip"))
    above_flip = bool(flip is not None and spot is not None and spot >= flip)
    if direction == "up":
        if bucket == "IWM" and not above_flip:
            return -10.0, f"{bucket}_below_flip_discount"
        if above_flip and "pin/range" in regime:
            if spot and call_wall and ((call_wall - spot) / spot * 100.0) < 1.0:
                return 2.0, f"{bucket}_bullish_but_near_call_wall"
            return 8.0, f"{bucket}_above_flip_support"
        if above_flip:
            return 5.0, f"{bucket}_above_flip"
        return -5.0, f"{bucket}_not_above_flip"
    if direction == "down":
        if bucket == "IWM" and not above_flip:
            return 8.0, f"{bucket}_below_flip_supports_downside"
        if above_flip and spot and put_wall and ((spot - put_wall) / spot * 100.0) < 1.0:
            return -2.0, f"{bucket}_downside_into_put_wall"
        if above_flip:
            return -8.0, f"{bucket}_above_flip_discount_for_puts"
        return 5.0, f"{bucket}_below_flip"
    return 0.0, "neutral_direction"


def option_structure(direction: str, score: float, distance: float | None, index_note: str) -> str:
    side = "call" if direction == "up" else "put" if direction == "down" else "option"
    if "near_call_wall" in index_note or "put_wall" in index_note:
        return f"{side} scalp or debit spread; avoid chasing into wall"
    if distance is not None and distance > 6:
        return f"{side} debit spread / starter only; target is far"
    if score >= 80:
        return f"{side} candidate on confirmation"
    if score >= 65:
        return f"{side} watchlist; require volume/price confirmation"
    return "watch only"


CLUSTER_STRENGTH_MAX = 400.0
CLUSTER_STRENGTH_POINTS = 25.0


def cluster_strength_points(strength: float) -> float:
    """Score cluster strength across the range it actually occupies.

    `scanner._cluster_strength` returns `strength_norm`: the sum of per-strike normalized
    |GEX| weights, where each strike contributes at most 100. So a triple tops out near
    300 and a quad near 400, and observed values run roughly 72-340.

    This used to be `min(strength / 6.0, 25.0)`, which saturates at 150. Every row of a
    real 40-row scan cleared that — strength spanned 192-340 and all 40 scored exactly
    25.00 — so the scanner's central measure contributed no ordering at all. Seven tickers
    tied at 69.0 with strengths from 192 to 255, and CAT (the strongest cluster in the
    scan at 340) ranked below BABA at 265 purely on the rank and quad bonuses.

    Dividing by the real ceiling instead of 6 restores the discrimination the scanner was
    rebuilt on 2026-08-06 to provide. Cluster *kind* is deliberately not folded in here;
    it is scored separately as the quad/triple bonus, which keeps the real product's
    ordering of quads first and then descending strength.
    """
    if strength <= 0:
        return 0.0
    capped = min(float(strength), CLUSTER_STRENGTH_MAX)
    return round(CLUSTER_STRENGTH_POINTS * capped / CLUSTER_STRENGTH_MAX, 2)


def grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 75:
        return "B+"
    if score >= 65:
        return "B"
    if score >= 55:
        return "C"
    return "skip"


def build_scores(scan_dir: Path, context_dir: Path) -> dict[str, Any]:
    run_dir = latest_scan_run(scan_dir)
    summary = load_json(run_dir / "summary.json")
    cluster_rows = rows_for(run_dir, "cluster")
    liq_rows = rows_for(run_dir, "liq")
    model_rows = rows_for(run_dir, "cipher_model")
    ledger_path = scan_dir / "quad_ledger.json"
    ledger = load_json(ledger_path) if ledger_path.is_file() else {"entries": {}}
    context = latest_context(context_dir)

    liq_by_ticker = {str(row.get("ticker") or "").upper(): row for row in liq_rows}
    model_by_ticker = {str(row.get("ticker") or "").upper(): row for row in model_rows}
    ledger_entries = ledger.get("entries") or {}
    ranked = []

    for row in cluster_rows:
        ticker = str(row.get("ticker") or "").upper()
        setup = str(row.get("setup") or "")
        direction = direction_from_text(setup)
        strength = to_float(row.get("strength")) or 0.0
        rank = int(to_float(row.get("rank")) or 99)
        distance = target_distance_pct(row)
        bucket = index_bucket(ticker)

        score = 25.0
        reasons = []
        score += cluster_strength_points(strength)
        reasons.append(f"cluster_strength={strength:g}")
        if rank <= 5:
            score += 10.0
            reasons.append("top5_cluster")
        elif rank <= 10:
            score += 5.0
            reasons.append("top10_cluster")
        if "QUAD" in setup.upper():
            score += 18.0
            reasons.append("quad_cluster")
        elif "TRIPLE" in setup.upper():
            score += 8.0
            reasons.append("triple_cluster")

        ledger_key = f"{ticker}|{'UPSIDE' if direction == 'up' else 'DOWNSIDE' if direction == 'down' else ''}"
        seen_count = int((ledger_entries.get(ledger_key) or {}).get("seen_count") or 0)
        # The persistence bonus applies to any cluster kind, so the reason names the kind
        # it actually saw. It previously read "repeated_quad_seen_3" for a triple.
        kind_label = "quad" if "QUAD" in setup.upper() else "triple" if "TRIPLE" in setup.upper() else "cluster"
        if seen_count >= 3:
            score += 12.0
            reasons.append(f"repeated_{kind_label}_seen_{seen_count}")
        elif seen_count == 2:
            score += 7.0
            reasons.append(f"repeated_{kind_label}_seen_2")
        elif seen_count == 1 and "QUAD" in setup.upper():
            score += 2.0
            reasons.append("new_or_single_quad")

        # `liq_agrees` / `model_agrees` record agreement, which is what the score used.
        # The response previously published `liq_overlap: bool(liq)`, true whenever a row
        # existed for the ticker — so a row that had just been penalised 8 points for
        # pointing the opposite way was still presented as confirming evidence.
        liq = liq_by_ticker.get(ticker)
        liq_agrees = None
        if liq:
            liq_dir = direction_from_text(liq.get("setup"))
            liq_agrees = liq_dir == direction
            if liq_agrees:
                clarity = to_float(liq.get("runway_clarity_pct")) or 0.0
                score += 10.0 + min(clarity / 20.0, 5.0)
                reasons.append(f"liq_overlap_{clarity:g}%")
            else:
                score -= 8.0
                reasons.append("liq_direction_conflict")

        model = model_by_ticker.get(ticker)
        model_agrees = None
        if model:
            model_dir = direction_from_text(model.get("bias"))
            model_agrees = model_dir == direction
            if model_agrees:
                model_score = to_float(model.get("score")) or 0.0
                score += 8.0 + min(model_score / 25.0, 4.0)
                reasons.append(f"cipher_model_overlap_{model_score:g}")
            else:
                score -= 6.0
                reasons.append("cipher_model_conflict")

        if distance is not None:
            if 0.6 <= distance <= 4.0:
                score += 6.0
                reasons.append(f"tradable_distance_{distance:.2f}%")
            elif distance < 0.6:
                score -= 5.0
                reasons.append(f"target_too_close_{distance:.2f}%")
            elif distance > 8.0:
                score -= 8.0
                reasons.append(f"target_far_{distance:.2f}%")
            else:
                reasons.append(f"moderate_distance_{distance:.2f}%")

        index_adj, index_note = index_score_adjustment(direction, bucket, context)
        score += index_adj
        reasons.append(index_note)
        score = max(0.0, min(score, 100.0))

        ranked.append({
            "ticker": ticker,
            "grade": grade(score),
            "score": round(score, 2),
            "direction": direction,
            "setup": setup,
            "rank": rank,
            "spot": row.get("spot"),
            "target": row.get("cluster_target"),
            "target_distance_pct": round(distance, 3) if distance is not None else None,
            "strength": strength,
            "seen_count": seen_count,
            "index_bucket": bucket,
            "liq_present": bool(liq),
            "liq_overlap": liq_agrees is True,
            "liq_conflict": liq_agrees is False,
            "cipher_model_present": bool(model),
            "cipher_model_overlap": model_agrees is True,
            "cipher_model_conflict": model_agrees is False,
            "option_structure": option_structure(direction, score, distance, index_note),
            "reasons": reasons,
            "cluster_levels": row.get("levels") or [],
            "liq": liq,
            "cipher_model": model,
            "caveat": "Research score only. Not financial advice and not an order signal.",
        })

    ranked.sort(key=lambda item: (item["score"], -item["rank"]), reverse=True)
    return {
        "generated_at": now_utc(),
        "latest_scan_run": str(run_dir),
        "latest_scan_captured_at": summary.get("captured_at"),
        "latest_context_generated_at": (context or {}).get("generated_at"),
        "repo_decision": {
            "chosen_path": "native_setup_research_engine",
            "why": "The current need is a lightweight read-only scorer over data already captured locally. Heavy external engines add useful future capabilities but unnecessary runtime and execution surface for v1.",
            "openbb_future_use": "Optional enrichment for fundamentals, news, macro, technical indicators, and alternate data aggregation.",
            "lean_future_use": "Use later for serious historical options backtesting once we have clean option-bar/chain history.",
            "nautilus_future_use": "Defer unless we add event-sourced execution simulation or multi-venue live trading.",
            "openstock_future_use": "Defer unless replacing/expanding the UI shell.",
        },
        "ranked": ranked,
    }


def write_outputs(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"setup_research_{stamp}.json"
    csv_path = out_dir / f"setup_research_{stamp}.csv"
    md_path = out_dir / f"setup_research_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "ticker", "grade", "score", "direction", "setup", "rank", "spot", "target",
            "target_distance_pct", "strength", "seen_count", "index_bucket",
            "liq_overlap", "cipher_model_overlap", "option_structure", "reasons",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["ranked"]:
            writer.writerow({field: json.dumps(row.get(field)) if isinstance(row.get(field), list) else row.get(field) for field in fields})
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Setup Research Scores",
        "",
        f"Generated: {report['generated_at']}",
        f"Scanner run: `{report['latest_scan_run']}`",
        "",
        "| Rank | Ticker | Grade | Score | Direction | Setup | Target | Why | Structure |",
        "|---:|---|---|---:|---|---|---:|---|---|",
    ]
    for idx, row in enumerate(report["ranked"][:30], start=1):
        why = "; ".join(row["reasons"][:5])
        lines.append(
            f"| {idx} | {row['ticker']} | {row['grade']} | {row['score']:.2f} | "
            f"{row['direction']} | {row['setup']} | {row.get('target')} | {why} | {row['option_structure']} |"
        )
    lines += [
        "",
        "Research-only caveat: these are prioritization scores for manual review, not financial advice or order signals.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank captured setups for read-only option-buy research.")
    parser.add_argument("--scan-dir", type=Path, default=SCAN_DIR)
    parser.add_argument("--context-dir", type=Path, default=CONTEXT_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    report = build_scores(args.scan_dir, args.context_dir)
    paths = write_outputs(report, args.out_dir)
    print(json.dumps({
        "generated_at": report["generated_at"],
        "latest_scan_run": report["latest_scan_run"],
        "paths": paths,
        "top": [
            {
                "ticker": row["ticker"],
                "grade": row["grade"],
                "score": row["score"],
                "direction": row["direction"],
                "target": row["target"],
                "structure": row["option_structure"],
            }
            for row in report["ranked"][:12]
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
