"""Plan candidate option contracts for ranked setups.

Read-only research helper. It uses the existing Alpaca market-data functions to
inspect option chains, then proposes a contract/structure for manual review.
It does not submit, route, or stage orders.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import app as cipher_app


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SETUP_DIR = DATA / "setup_research"
OUT_DIR = DATA / "option_contract_plans"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def latest_setup_file() -> Path:
    files = sorted(SETUP_DIR.glob("setup_research_*.json"))
    if not files:
        raise FileNotFoundError(f"No setup research JSON found under {SETUP_DIR}")
    return files[-1]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def dte(expiry: str) -> int | None:
    try:
        return (date.fromisoformat(expiry[:10]) - datetime.now().date()).days
    except ValueError:
        return None


def spread_pct(contract: dict[str, Any]) -> float | None:
    bid = num(contract.get("bid"))
    ask = num(contract.get("ask"))
    mid = num(contract.get("mid"))
    if bid is None or ask is None or mid is None or mid <= 0 or ask < bid:
        return None
    return (ask - bid) / mid * 100.0


def side_for(direction: str) -> str:
    return "call" if direction == "up" else "put"


def ideal_horizon_days(distance_pct: float | None, grade: str) -> tuple[int, int, str]:
    if distance_pct is not None and distance_pct <= 2.5:
        return 0, 3, "intraday to next-session scalp"
    if distance_pct is not None and distance_pct <= 5.0:
        return 1, 7, "same day if target hits; otherwise 1-2 sessions max"
    if grade in {"A", "B+"}:
        return 2, 10, "starter/debit spread; 1-3 sessions max"
    return 1, 5, "watch only unless price confirms quickly"


def target_delta(side: str, distance_pct: float | None) -> float:
    if distance_pct is not None and distance_pct > 5.0:
        return 0.35
    return 0.45


def contract_liquidity_score(contract: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    notes = []
    mid = num(contract.get("mid"))
    vol = num(contract.get("volume")) or 0.0
    oi = num(contract.get("open_interest")) or 0.0
    sp = spread_pct(contract)
    if mid is None or mid <= 0:
        return -100.0, ["no_mid"]
    if mid < 0.10:
        score -= 15
        notes.append("very_cheap_lotto_like")
    if vol >= 500:
        score += 8
        notes.append("volume_ok")
    elif vol > 0:
        score += 3
        notes.append("some_volume")
    else:
        score -= 8
        notes.append("no_volume")
    if oi >= 500:
        score += 8
        notes.append("oi_ok")
    elif oi > 0:
        score += 3
        notes.append("some_oi")
    else:
        score -= 6
        notes.append("no_oi")
    if sp is None:
        score -= 10
        notes.append("spread_unknown")
    elif sp <= 15:
        score += 10
        notes.append("tight_spread")
    elif sp <= 30:
        score += 2
        notes.append("spread_acceptable")
    else:
        score -= 12
        notes.append("wide_spread")
    return score, notes


def choose_long_contract(setup: dict[str, Any], chain: list[dict[str, Any]]) -> dict[str, Any] | None:
    direction = setup.get("direction")
    side = side_for(direction)
    spot = num(setup.get("spot"))
    target = num(setup.get("target"))
    distance = num(setup.get("target_distance_pct"))
    min_dte, max_dte, _ = ideal_horizon_days(distance, str(setup.get("grade") or ""))
    ideal_delta = target_delta(side, distance)
    candidates = []
    for contract in chain:
        if contract.get("type") != side:
            continue
        strike = num(contract.get("strike"))
        expiry = contract.get("expiry")
        days = dte(str(expiry or ""))
        mid = num(contract.get("mid"))
        if strike is None or days is None or mid is None or mid <= 0:
            continue
        if days < min_dte or days > max_dte:
            continue
        if spot:
            if side == "call" and strike > spot * 1.08:
                continue
            if side == "put" and strike < spot * 0.92:
                continue
        delta = abs(num(contract.get("delta")) or 0.0)
        liq_score, notes = contract_liquidity_score(contract)
        delta_score = max(0.0, 20.0 - abs(delta - ideal_delta) * 60.0) if delta else 8.0
        moneyness_score = 0.0
        if spot and strike:
            pct = abs(strike - spot) / spot * 100.0
            moneyness_score = max(0.0, 12.0 - pct * 2.0)
        target_score = 0.0
        if target is not None:
            if side == "call" and strike <= target:
                target_score += 5
            if side == "put" and strike >= target:
                target_score += 5
        expiry_score = max(0.0, 10.0 - abs(days - max(min_dte, 1)) * 1.5)
        total = liq_score + delta_score + moneyness_score + target_score + expiry_score
        candidates.append((total, {**contract, "planner_score": round(total, 2), "planner_notes": notes, "dte": days}))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1] if candidates else None


def choose_short_leg(setup: dict[str, Any], long_contract: dict[str, Any], chain: list[dict[str, Any]]) -> dict[str, Any] | None:
    side = long_contract.get("type")
    target = num(setup.get("target"))
    expiry = long_contract.get("expiry")
    long_strike = num(long_contract.get("strike"))
    if target is None or long_strike is None:
        return None
    same = [
        contract for contract in chain
        if contract.get("type") == side and contract.get("expiry") == expiry and num(contract.get("strike")) is not None
    ]
    if side == "call":
        shorts = [c for c in same if num(c.get("strike")) and num(c.get("strike")) > long_strike and num(c.get("strike")) <= target * 1.02]
        shorts.sort(key=lambda c: abs((num(c.get("strike")) or 0) - target))
    else:
        shorts = [c for c in same if num(c.get("strike")) and num(c.get("strike")) < long_strike and num(c.get("strike")) >= target * 0.98]
        shorts.sort(key=lambda c: abs((num(c.get("strike")) or 0) - target))
    for contract in shorts:
        if num(contract.get("mid")) and (spread_pct(contract) is None or (spread_pct(contract) or 999) <= 45):
            return {**contract, "dte": dte(str(contract.get("expiry") or ""))}
    return None


def plan_exit_rules(setup: dict[str, Any], long_contract: dict[str, Any]) -> dict[str, Any]:
    distance = num(setup.get("target_distance_pct"))
    _, _, hold = ideal_horizon_days(distance, str(setup.get("grade") or ""))
    days = int(long_contract.get("dte") or 0)
    if days <= 0:
        max_hold = "intraday only; exit before close"
    elif days <= 2:
        max_hold = "same day preferred; overnight only if still above/below trigger and spread remains liquid"
    else:
        max_hold = hold
    return {
        "planned_holding_time": max_hold,
        "profit_management": "scale/exit into first target or 25-50% option gain; do not wait for full target if index wall stalls",
        "risk_management": "manual invalidation if underlying loses setup support/trigger or option loses 30-40%; tighter for 0DTE",
        "confirmation_needed": "underlying must move in setup direction with volume; confirm bid/ask spread before entry",
    }


def fetch_chain(ticker: str, feed: str, max_pages: int, horizon_days: int) -> list[dict[str, Any]]:
    gte = datetime.now(timezone.utc).date().isoformat()
    lte = (datetime.now(timezone.utc).date() + timedelta(days=horizon_days)).isoformat()
    return cipher_app.option_chain(ticker, feed, force=True, max_pages=max_pages, expiration_gte=gte, expiration_lte=lte)


def build_plan(limit: int, min_grade: set[str], feed: str, max_pages: int) -> dict[str, Any]:
    setup_path = latest_setup_file()
    setup_payload = load_json(setup_path)
    ranked = [
        row for row in setup_payload.get("ranked", [])
        if row.get("grade") in min_grade and row.get("direction") in {"up", "down"}
    ][:limit]
    plans = []
    errors = []
    for setup in ranked:
        ticker = setup["ticker"]
        distance = num(setup.get("target_distance_pct"))
        _, max_dte, _ = ideal_horizon_days(distance, str(setup.get("grade") or ""))
        try:
            chain = fetch_chain(ticker, feed, max_pages=max_pages, horizon_days=max(10, max_dte + 2))
            long_contract = choose_long_contract(setup, chain)
            if not long_contract:
                errors.append({"ticker": ticker, "error": "no_suitable_contract_found"})
                continue
            short_contract = choose_short_leg(setup, long_contract, chain)
            long_mid = num(long_contract.get("mid")) or 0.0
            short_mid = num(short_contract.get("mid")) if short_contract else None
            debit = long_mid - short_mid if short_mid is not None else long_mid
            plans.append({
                "ticker": ticker,
                "setup_grade": setup.get("grade"),
                "setup_score": setup.get("score"),
                "direction": setup.get("direction"),
                "underlying_spot": setup.get("spot"),
                "setup_target": setup.get("target"),
                "target_distance_pct": setup.get("target_distance_pct"),
                "suggested_structure": "debit_spread" if short_contract else "long_option",
                "long_contract": summarize_contract(long_contract),
                "short_contract": summarize_contract(short_contract) if short_contract else None,
                "estimated_debit_mid": round(debit, 4) if debit is not None else None,
                "exit_plan": plan_exit_rules(setup, long_contract),
                "setup_reasons": setup.get("reasons") or [],
                "caveat": "Manual review only. Confirm live bid/ask, chart trigger, and risk before any trade.",
            })
        except Exception as exc:  # noqa: BLE001 - data gaps are common.
            errors.append({"ticker": ticker, "error": str(exc)})
    return {
        "generated_at": now_utc(),
        "setup_source": str(setup_path),
        "feed": feed,
        "plans": plans,
        "errors": errors,
    }


def summarize_contract(contract: dict[str, Any] | None) -> dict[str, Any] | None:
    if not contract:
        return None
    bid = num(contract.get("bid"))
    ask = num(contract.get("ask"))
    return {
        "symbol": contract.get("symbol"),
        "type": contract.get("type"),
        "expiry": contract.get("expiry"),
        "dte": contract.get("dte"),
        "strike": contract.get("strike"),
        "bid": bid,
        "ask": ask,
        "mid": contract.get("mid"),
        "spread_pct": round(spread_pct(contract), 2) if spread_pct(contract) is not None else None,
        "delta": contract.get("delta"),
        "iv": contract.get("iv"),
        "volume": contract.get("volume"),
        "open_interest": contract.get("open_interest"),
        "planner_score": contract.get("planner_score"),
        "planner_notes": contract.get("planner_notes") or [],
    }


def write_outputs(payload: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"option_contract_plan_{stamp}.json"
    csv_path = out_dir / f"option_contract_plan_{stamp}.csv"
    md_path = out_dir / f"option_contract_plan_{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "ticker", "setup_grade", "setup_score", "direction", "underlying_spot",
            "setup_target", "suggested_structure", "long_symbol", "long_expiry",
            "long_strike", "long_mid", "long_delta", "short_symbol", "short_strike",
            "estimated_debit_mid", "planned_holding_time",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for plan in payload["plans"]:
            long = plan["long_contract"]
            short = plan.get("short_contract") or {}
            writer.writerow({
                "ticker": plan["ticker"],
                "setup_grade": plan["setup_grade"],
                "setup_score": plan["setup_score"],
                "direction": plan["direction"],
                "underlying_spot": plan["underlying_spot"],
                "setup_target": plan["setup_target"],
                "suggested_structure": plan["suggested_structure"],
                "long_symbol": long.get("symbol"),
                "long_expiry": long.get("expiry"),
                "long_strike": long.get("strike"),
                "long_mid": long.get("mid"),
                "long_delta": long.get("delta"),
                "short_symbol": short.get("symbol"),
                "short_strike": short.get("strike"),
                "estimated_debit_mid": plan["estimated_debit_mid"],
                "planned_holding_time": plan["exit_plan"]["planned_holding_time"],
            })
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Option Contract Plan",
        "",
        f"Generated: {payload['generated_at']}",
        f"Setup source: `{payload['setup_source']}`",
        "",
        "| Ticker | Structure | Long | Short | Debit Mid | Hold | Notes |",
        "|---|---|---|---|---:|---|---|",
    ]
    for plan in payload["plans"]:
        long = plan["long_contract"]
        short = plan.get("short_contract")
        long_label = f"{long['expiry']} {long['strike']} {long['type']} @ {long['mid']}"
        short_label = f"{short['expiry']} {short['strike']} {short['type']} @ {short['mid']}" if short else ""
        notes = "; ".join(plan.get("setup_reasons") or [])[:220]
        lines.append(
            f"| {plan['ticker']} | {plan['suggested_structure']} | {long_label} | {short_label} | "
            f"{plan['estimated_debit_mid']} | {plan['exit_plan']['planned_holding_time']} | {notes} |"
        )
    if payload.get("errors"):
        lines += ["", "## Data Gaps", ""]
        for err in payload["errors"]:
            lines.append(f"- {err['ticker']}: {err['error']}")
    lines += ["", "Manual-review only. Confirm live bid/ask, chart trigger, liquidity, and risk before any trade.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan candidate option contracts for setup research.")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--min-grade", default="A,B+")
    parser.add_argument("--feed", default="opra", choices=("opra", "indicative"))
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    grades = {part.strip() for part in args.min_grade.split(",") if part.strip()}
    payload = build_plan(args.limit, grades, args.feed, args.max_pages)
    paths = write_outputs(payload, args.out_dir)
    print(json.dumps({
        "generated_at": payload["generated_at"],
        "paths": paths,
        "plans": [
            {
                "ticker": p["ticker"],
                "structure": p["suggested_structure"],
                "long": p["long_contract"],
                "short": p.get("short_contract"),
                "debit_mid": p["estimated_debit_mid"],
                "hold": p["exit_plan"]["planned_holding_time"],
            }
            for p in payload["plans"]
        ],
        "errors": payload["errors"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
