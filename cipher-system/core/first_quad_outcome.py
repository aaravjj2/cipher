"""Analyze first same-day quad cluster outcomes.

The option outcome is explicitly reconstructed when true entry-time option
quotes were not captured. It uses scan-time spot, current IV proxy, and latest
Tradier option mids. This is useful for research directionality, not audited
trade P/L.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import tradier_stream_capture as tradier


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[0]
SCAN_DIR = ROOT / "data" / "accessobsidian_scans"
OUT_DIR = ROOT / "data" / "quad_outcomes"
STOCK_DATA = WORKSPACE / "Stock data" / "data"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def first_successful_scan(day: str) -> Path:
    for summary in sorted((SCAN_DIR / day).glob("*/summary.json")):
        payload = load_json(summary)
        cluster = summary.parent / "cluster.json"
        if payload.get("errors") or not cluster.is_file():
            continue
        rows = load_json(cluster).get("rows") or []
        if any("QUAD" in str(row.get("setup") or "").upper() for row in rows):
            return summary.parent
    raise FileNotFoundError(f"No successful quad cluster scan found for {day}")


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def occ_symbol(root: str, expiry: str, option_type: str, strike: float) -> str:
    y, m, d = expiry.split("-")
    cp = "C" if option_type.lower().startswith("c") else "P"
    return f"{root.upper()}{y[2:]}{m}{d}{cp}{int(round(float(strike) * 1000)):08d}"


def tradier_quotes(symbols: list[str], greeks: bool = True) -> dict[str, dict[str, Any]]:
    token, _ = tradier.load_credentials("production")
    url = "https://api.tradier.com/v1/markets/quotes?" + urllib.parse.urlencode({
        "symbols": ",".join(symbols),
        "greeks": "true" if greeks else "false",
    })
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    quotes = (payload.get("quotes") or {}).get("quote") or []
    if isinstance(quotes, dict):
        quotes = [quotes]
    return {str(row.get("symbol") or "").upper(): row for row in quotes}


def mid(quote: dict[str, Any]) -> float | None:
    bid = num(quote.get("bid"))
    ask = num(quote.get("ask"))
    last = num(quote.get("last"))
    if bid is not None and ask is not None and ask >= bid and ask > 0:
        return (bid + ask) / 2.0
    return last


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, iv: float, option_type: str, rate: float = 0.045) -> float:
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    sigma_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / sigma_t
    d2 = d1 - sigma_t
    if option_type == "call":
        return spot * normal_cdf(d1) - strike * math.exp(-rate * t_years) * normal_cdf(d2)
    return strike * math.exp(-rate * t_years) * normal_cdf(-d2) - spot * normal_cdf(-d1)


def iv_from_quote(quote: dict[str, Any]) -> float | None:
    greeks = quote.get("greeks") or {}
    for key in ("mid_iv", "smv_vol", "ask_iv", "bid_iv"):
        value = num(greeks.get(key))
        if value and value > 0:
            return value
    return None


def parse_scan_time(raw: str) -> datetime:
    return datetime.fromisoformat(raw).astimezone(timezone.utc)


def expiry_close_utc(expiry: str) -> datetime:
    # Options generally stop regular trading at 16:00 New York time.
    # July is EDT, so 16:00 ET = 20:00 UTC.
    return datetime.fromisoformat(expiry + "T20:00:00+00:00")


def choose_call_spread(row: dict[str, Any]) -> tuple[float, float]:
    target = float(row["cluster_target"])
    levels = sorted({float(level["strike"]) for level in row.get("levels") or []})
    below = [strike for strike in levels if strike < target]
    if below:
        return max(below), target
    diffs = sorted({round(abs(a - b), 4) for a in levels for b in levels if abs(a - b) > 0})
    increment = diffs[0] if diffs else 1.0
    return target - increment, target


def choose_golden_call(row: dict[str, Any]) -> float:
    """Pick the first strike above spot from the cluster levels.

    This is a transparent "golden spot" proxy: buy the nearest listed cluster
    call that is above scan spot, instead of buying the far target or a spread.
    """
    spot = float(row["spot"])
    levels = sorted({float(level["strike"]) for level in row.get("levels") or []})
    above = [strike for strike in levels if strike >= spot]
    return min(above) if above else float(row["cluster_target"])


def quote_width_pct(quote: dict[str, Any]) -> float | None:
    bid = num(quote.get("bid"))
    ask = num(quote.get("ask"))
    qmid = mid(quote)
    if bid is None or ask is None or qmid is None or qmid <= 0 or ask < bid:
        return None
    return (ask - bid) / qmid * 100.0


def local_rows_after(ticker: str, scan_time_local: datetime) -> list[dict[str, Any]]:
    for timeframe in ("1m", "5m", "15m"):
        path = STOCK_DATA / timeframe / f"{ticker.upper()}.csv"
        if not path.is_file():
            continue
        rows = []
        with path.open(newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                raw = row.get("Datetime") or row.get("timestamps")
                if not raw:
                    continue
                try:
                    ts = datetime.fromisoformat(raw.replace(" ", "T"))
                except ValueError:
                    continue
                # Local stock CSV timestamps are naive exchange-time in this repo.
                if ts.date().isoformat() != scan_time_local.date().isoformat():
                    continue
                if ts.time() < scan_time_local.time():
                    continue
                hi = num(row.get("High") or row.get("high"))
                lo = num(row.get("Low") or row.get("low"))
                cl = num(row.get("Close") or row.get("close"))
                if hi is None or lo is None or cl is None:
                    continue
                rows.append({"timestamp": ts.isoformat(), "high": hi, "low": lo, "close": cl, "timeframe": timeframe})
        if rows:
            return rows
    return []


def analyze(day: str, expiry: str, structure: str = "spread") -> dict[str, Any]:
    run_dir = first_successful_scan(day)
    summary = load_json(run_dir / "summary.json")
    scan_time_local = datetime.fromisoformat(summary["captured_at"])
    scan_time = scan_time_local.astimezone(timezone.utc)
    rows = [
        row for row in (load_json(run_dir / "cluster.json").get("rows") or [])
        if "QUAD" in str(row.get("setup") or "").upper()
    ]
    option_symbols = []
    ticker_symbols = []
    specs = {}
    for row in rows:
        ticker = str(row["ticker"]).upper()
        if structure == "long-call":
            long_strike = choose_golden_call(row)
            long_symbol = occ_symbol(ticker, expiry, "call", long_strike)
            specs[ticker] = (long_strike, None, long_symbol, None)
            option_symbols.append(long_symbol)
        else:
            long_strike, short_strike = choose_call_spread(row)
            long_symbol = occ_symbol(ticker, expiry, "call", long_strike)
            short_symbol = occ_symbol(ticker, expiry, "call", short_strike)
            specs[ticker] = (long_strike, short_strike, long_symbol, short_symbol)
            option_symbols.extend([long_symbol, short_symbol])
        ticker_symbols.append(ticker)
    quotes = tradier_quotes(option_symbols + ticker_symbols, greeks=True)
    expiry_dt = expiry_close_utc(expiry)
    t_entry = max((expiry_dt - scan_time).total_seconds() / (365.0 * 24 * 3600), 0.0)
    out_rows = []
    for row in rows:
        ticker = str(row["ticker"]).upper()
        scan_spot = float(row["spot"])
        target = float(row["cluster_target"])
        long_strike, short_strike, long_symbol, short_symbol = specs[ticker]
        long_quote = quotes.get(long_symbol) or {}
        short_quote = quotes.get(short_symbol) if short_symbol else {}
        long_mid_now = mid(long_quote)
        short_mid_now = mid(short_quote) if short_quote else None
        latest_spot = num((quotes.get(ticker) or {}).get("last"))
        bars = local_rows_after(ticker, scan_time_local)
        high_after = max((bar["high"] for bar in bars), default=None)
        low_after = min((bar["low"] for bar in bars), default=None)
        close_after = bars[-1]["close"] if bars else latest_spot
        hit_target = high_after is not None and high_after >= target
        iv_long = iv_from_quote(long_quote)
        iv_short = iv_from_quote(short_quote)
        entry_est = None
        current_mark = None
        option_pnl_pct = None
        option_outcome = "unknown"
        if structure == "long-call" and iv_long and long_mid_now is not None:
            entry_est = max(bs_price(scan_spot, long_strike, t_entry, iv_long, "call"), 0.01)
            current_mark = max(long_mid_now, 0.0)
            option_pnl_pct = (current_mark - entry_est) / entry_est * 100.0
            option_outcome = "profit" if option_pnl_pct > 0 else "loss" if option_pnl_pct < 0 else "flat"
        elif iv_long and iv_short and long_mid_now is not None and short_mid_now is not None:
            long_entry = bs_price(scan_spot, long_strike, t_entry, iv_long, "call")
            short_entry = bs_price(scan_spot, short_strike, t_entry, iv_short, "call")
            entry_est = max(long_entry - short_entry, 0.01)
            current_mark = max(long_mid_now - short_mid_now, 0.0)
            option_pnl_pct = (current_mark - entry_est) / entry_est * 100.0
            option_outcome = "profit" if option_pnl_pct > 0 else "loss" if option_pnl_pct < 0 else "flat"
        underlying_return = ((close_after - scan_spot) / scan_spot * 100.0) if close_after else None
        out_rows.append({
            "ticker": ticker,
            "rank": row.get("rank"),
            "setup": row.get("setup"),
            "scan_spot": scan_spot,
            "latest_or_close": round(close_after, 4) if close_after is not None else None,
            "underlying_return_pct": round(underlying_return, 3) if underlying_return is not None else None,
            "target": target,
            "high_after_scan": high_after,
            "hit_target": hit_target if high_after is not None else None,
            "spread": (
                f"{expiry} {long_strike:g} call"
                if structure == "long-call"
                else f"{expiry} {long_strike:g}/{short_strike:g} call debit spread"
            ),
            "long_symbol": long_symbol,
            "short_symbol": short_symbol,
            "entry_debit_est": round(entry_est, 4) if entry_est is not None else None,
            "current_mark": round(current_mark, 4) if current_mark is not None else None,
            "estimated_option_pnl_pct": round(option_pnl_pct, 2) if option_pnl_pct is not None else None,
            "estimated_option_outcome": option_outcome,
            "long_volume": long_quote.get("volume"),
            "short_volume": short_quote.get("volume"),
            "long_oi": long_quote.get("open_interest"),
            "short_oi": short_quote.get("open_interest"),
            "target_distance_pct": round((target - scan_spot) / scan_spot * 100.0, 3),
            "long_moneyness_pct": round((long_strike - scan_spot) / scan_spot * 100.0, 3),
            "long_quote_width_pct": round(quote_width_pct(long_quote), 3) if quote_width_pct(long_quote) is not None else None,
            "long_delta": (long_quote.get("greeks") or {}).get("delta"),
            "long_gamma": (long_quote.get("greeks") or {}).get("gamma"),
            "long_theta": (long_quote.get("greeks") or {}).get("theta"),
            "long_iv": iv_long,
            "data_quality": "estimated_option_entry_from_scan_spot_and_current_iv; true_0919_option_tick_not_captured",
        })
    factor_report = build_factor_report(out_rows)
    return {
        "generated_at": now_utc(),
        "day": day,
        "first_successful_scan_run": str(run_dir),
        "scan_captured_at": summary.get("captured_at"),
        "expiry": expiry,
        "structure": structure,
        "rows": out_rows,
        "factor_report": factor_report,
        "caveat": "Option P/L is reconstructed because true scan-time option quotes were not captured for every quad.",
    }


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_factor_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    winners = [r for r in rows if r.get("estimated_option_outcome") == "profit"]
    losers = [r for r in rows if r.get("estimated_option_outcome") == "loss"]
    fields = [
        "rank", "underlying_return_pct", "target_distance_pct", "long_moneyness_pct",
        "long_quote_width_pct", "long_volume", "long_oi", "long_delta",
        "long_gamma", "long_theta", "long_iv", "estimated_option_pnl_pct",
    ]
    def avg(group: list[dict[str, Any]], field: str) -> float | None:
        vals = [float(r[field]) for r in group if num(r.get(field)) is not None]
        value = mean(vals)
        return round(value, 4) if value is not None else None
    return {
        "winner_count": len(winners),
        "loser_count": len(losers),
        "averages": {
            field: {"winners": avg(winners, field), "losers": avg(losers, field)}
            for field in fields
        },
        "observations": infer_observations(winners, losers),
        "caveat": "Tiny same-day sample. Treat as hypotheses to forward test, not a fitted edge.",
    }


def infer_observations(winners: list[dict[str, Any]], losers: list[dict[str, Any]]) -> list[str]:
    obs = []
    w_ret = mean([float(r["underlying_return_pct"]) for r in winners if num(r.get("underlying_return_pct")) is not None])
    l_ret = mean([float(r["underlying_return_pct"]) for r in losers if num(r.get("underlying_return_pct")) is not None])
    if w_ret is not None and l_ret is not None:
        obs.append(f"Winners had better underlying follow-through: {w_ret:.2f}% avg vs {l_ret:.2f}% avg.")
    w_dist = mean([float(r["target_distance_pct"]) for r in winners if num(r.get("target_distance_pct")) is not None])
    l_dist = mean([float(r["target_distance_pct"]) for r in losers if num(r.get("target_distance_pct")) is not None])
    if w_dist is not None and l_dist is not None:
        obs.append(f"Closer targets helped: winners target distance {w_dist:.2f}% vs losers {l_dist:.2f}%.")
    w_oi = mean([float(r["long_oi"]) for r in winners if num(r.get("long_oi")) is not None])
    l_oi = mean([float(r["long_oi"]) for r in losers if num(r.get("long_oi")) is not None])
    if w_oi is not None and l_oi is not None:
        obs.append(f"OI was not sufficient alone: winners long OI {w_oi:.0f} vs losers {l_oi:.0f}.")
    return obs


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# First Quad Cluster Outcome",
        "",
        f"Generated: {report['generated_at']}",
        f"First successful scan: `{report['first_successful_scan_run']}`",
        f"Scan captured at: {report['scan_captured_at']}",
        f"Structure: {report.get('structure')}",
        "",
        "| Rank | Ticker | Spread | Scan Spot | Latest/Close | Underlying % | Target Hit | Est. Entry | Current Mark | Est. Option P/L | Outcome |",
        "|---:|---|---|---:|---:|---:|---|---:|---:|---:|---|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['rank']} | {row['ticker']} | {row['spread']} | {row['scan_spot']} | "
            f"{row['latest_or_close']} | {row['underlying_return_pct']} | {row['hit_target']} | "
            f"{row['entry_debit_est']} | {row['current_mark']} | {row['estimated_option_pnl_pct']} | "
            f"{row['estimated_option_outcome']} |"
        )
    lines += ["", report["caveat"], ""]
    factors = report.get("factor_report") or {}
    if factors:
        lines += ["## Factor Read", ""]
        for item in factors.get("observations") or []:
            lines.append(f"- {item}")
        lines += ["", "Averages:", ""]
        lines.append("| Factor | Winners | Losers |")
        lines.append("|---|---:|---:|")
        for field, values in (factors.get("averages") or {}).items():
            lines.append(f"| {field} | {values.get('winners')} | {values.get('losers')} |")
        lines.append("")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    structure = str(report.get("structure") or "unknown").replace("-", "_")
    json_path = out_dir / f"first_quad_outcome_{structure}_{stamp}.json"
    csv_path = out_dir / f"first_quad_outcome_{structure}_{stamp}.csv"
    md_path = out_dir / f"first_quad_outcome_{structure}_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    fields = list(report["rows"][0].keys()) if report["rows"] else []
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["rows"])
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=datetime.now().date().isoformat())
    parser.add_argument("--expiry", default="2026-07-24")
    parser.add_argument("--structure", choices=("spread", "long-call"), default="spread")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    report = analyze(args.day, args.expiry, args.structure)
    paths = write_outputs(report, args.out_dir)
    print(json.dumps({
        "generated_at": report["generated_at"],
        "scan_captured_at": report["scan_captured_at"],
        "paths": paths,
        "structure": report["structure"],
        "factor_observations": report.get("factor_report", {}).get("observations"),
        "summary": [
            {
                "ticker": row["ticker"],
                "underlying_return_pct": row["underlying_return_pct"],
                "hit_target": row["hit_target"],
                "estimated_option_pnl_pct": row["estimated_option_pnl_pct"],
                "estimated_option_outcome": row["estimated_option_outcome"],
            }
            for row in report["rows"]
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
