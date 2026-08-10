"""Out-of-sample replication sweep for the filter-mode partition result.

docs/backtest-findings.md records one positive result: partitioning a fixed-cadence
base strategy by detector state, the `bearish` partition beat both the base and its
own matched random control. It was found on ten symbols, 15Min bars, one year,
"EOD Focus". A single positive on a single configuration is exactly the shape a false
positive takes, so this sweep asks whether it is there when you look elsewhere.

Three axes move independently of the finding: a disjoint symbol set, the bar
timeframe, and the detector mode. The original configuration is included as a control
on the harness itself — if it does not reproduce, the sweep is measuring something
other than what was reported.

Multiple comparisons are the whole hazard here. The sweep therefore reports how many
configurations were tried alongside how many were positive, so the hit rate can be read
against chance rather than in isolation. Every partition keeps its own matched random
control and the `beats_control_range` test (clear the best of N random draws, not the
mean), so a partition can show lift over base and still fail honestly.

Research only. Simulated fills over historical bars; places no orders.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

import backtest_engine as be  # noqa: E402
from scripts.run_obsidian_backtest import load_bars  # noqa: E402

# The ten symbols the original finding was measured on.
ORIGINAL = ["NVDA", "AAPL", "SPY", "QQQ", "TSLA", "AMD", "META", "MSFT", "AMZN", "GOOGL"]
# Deliberately disjoint from ORIGINAL and drawn from different sectors, so a result
# that only reflects 2025 mega-cap-tech drift cannot survive the crossing.
DISJOINT = ["AVGO", "NFLX", "COST", "JPM", "XOM", "WMT", "UNH", "LLY", "V", "MA"]

TIMEFRAMES = ["5Min", "15Min", "30Min", "1Hour"]
DETECTORS = ["EOD Focus", "Full Session"]

# A partition this small cannot support a verdict whichever way it falls; recording it
# as a hit would inflate the hit rate with noise.
MIN_TRADES = 25


def run_one(
    symbols,
    label,
    timeframe,
    detector,
    years=1.0,
    *,
    cost_profile=None,
):
    bars = load_bars(symbols, timeframe, years)
    if not bars:
        return {"error": "no bars", "label": label, "timeframe": timeframe,
                "detector": detector}
    payload = be.run_filter(
        bars,
        detector_params={"mode": detector},
        lookback_bars=6,
        entry_every=12,
        control_repeats=20,
        cost_profile=cost_profile,
    )
    if "error" in payload:
        return {"error": payload["error"], "label": label, "timeframe": timeframe,
                "detector": detector}

    base = payload.get("base", {})
    rows = []
    for name, part in (payload.get("partitions") or {}).items():
        if name == "none":
            continue
        stats = part.get("stats", {})
        rows.append({
            "partition": name,
            "n": stats.get("trades", 0),
            "win_rate": stats.get("win_rate"),
            "avg_return_pct": stats.get("avg_return_pct"),
            "profit_factor": stats.get("profit_factor"),
            "lift_vs_base_pp": part.get("lift_vs_base_pp"),
            "beats_control_range": part.get("beats_control_range"),
            "control_avg": (part.get("control") or {}).get("avg_return_pct"),
            "control_best": ((part.get("control") or {}).get("avg_return_pct_range") or [None, None])[1],
        })
    return {
        "label": label, "symbols": sorted(bars), "timeframe": timeframe,
        "detector": detector, "years": years,
        "base": {"n": base.get("trades"), "win_rate": base.get("win_rate"),
                 "avg_return_pct": base.get("avg_return_pct"),
                 "profit_factor": base.get("profit_factor")},
        "partitions": rows,
    }


def main() -> int:
    results = []
    for label, symbols in (("original", ORIGINAL), ("disjoint", DISJOINT)):
        for timeframe in TIMEFRAMES:
            for detector in DETECTORS:
                print(f"… {label:9s} {timeframe:6s} {detector}", flush=True)
                try:
                    results.append(run_one(symbols, label, timeframe, detector))
                except Exception as exc:  # noqa: BLE001 - a failed cell must not lose the sweep
                    results.append({"error": f"{type(exc).__name__}: {exc}",
                                    "label": label, "timeframe": timeframe,
                                    "detector": detector})

    tested = hits = small = 0
    print("\n" + "=" * 96)
    print(f"{'set':10s}{'tf':7s}{'detector':14s}{'part':9s}{'n':>6s}{'avg%':>9s}"
          f"{'lift_pp':>9s}{'ctrl_best':>11s}{'beats':>7s}")
    print("-" * 96)
    for res in results:
        if res.get("error"):
            print(f"{res['label']:10s}{res['timeframe']:7s}{res['detector']:14s}"
                  f"ERROR {res['error'][:44]}")
            continue
        for row in res["partitions"]:
            flag = ""
            if row["n"] < MIN_TRADES:
                small += 1
                flag = " (small)"
            else:
                tested += 1
                if row["beats_control_range"]:
                    hits += 1
            print(f"{res['label']:10s}{res['timeframe']:7s}{res['detector']:14s}"
                  f"{row['partition']:9s}{row['n']:>6d}{row['avg_return_pct']:>9.4f}"
                  f"{(row['lift_vs_base_pp'] or 0):>9.4f}{(row['control_best'] or 0):>11.4f}"
                  f"{str(row['beats_control_range']):>7s}{flag}")

    print("-" * 96)
    rate = (hits / tested * 100) if tested else 0.0
    print(f"partitions with n>={MIN_TRADES}: {tested}   beat their control: {hits} "
          f"({rate:.1f}%)   too small to judge: {small}")
    print("A partition beats its control by chance about 5% of the time per test, so read "
          "the hit\nrate against that, not against zero.")

    out = ROOT / "data" / "backtests" / (
        "filter_replication_sweep_"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_trades_for_verdict": MIN_TRADES,
        "partitions_judged": tested, "partitions_beating_control": hits,
        "partitions_too_small": small,
        "results": results,
    }, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
