"""Re-derive stored GEX levels from the raw capture payloads and report the drift.

The captures in `data/gex_history.sqlite` were computed by whatever version of
`core/exposure.py` was running at capture time. Two corrections have landed since
the earliest of them: `_years_to_expiry` now floors same-day expiries instead of
discarding them (`_MIN_T_YEARS`), and `greeks._IV_HI` was raised from 5.0 to 10.0
so deep-ITM contracts solve instead of failing. Snapshots taken before those fixes
carry the old answers.

The point-in-time open interest cannot be re-fetched from any vendor, so the
question is whether the corrections require re-capturing (impossible) or only
re-deriving (cheap). They only require re-deriving: the raw payloads retain
`call_oi`/`put_oi` **and** `call_mid`/`put_mid` per strike and expiration, plus
the spot at capture time. That is everything the math consumes — mid solves to an
implied vol, which gives gamma, which with OI gives GEX.

This script therefore recomputes each snapshot's summary levels with the current
math and reports how far the stored values moved. It writes a **new** artifact and
never updates `gex_snapshots`: the difference between the old and new answers is
evidence about the size of the fix, and overwriting the old values would destroy
the only record of it.

Read-only against the capture database and the raw files.

Usage:
  python3 scripts/recompute_gex_from_raw.py --limit 500
  python3 scripts/recompute_gex_from_raw.py --all
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

import exposure  # noqa: E402
import greeks  # noqa: E402

DEFAULT_DB = ROOT / "data" / "gex_history.sqlite"
DEFAULT_OUT = ROOT / "data" / "gex_recompute" / "recompute_report.json"

# Below this the two answers are the same number printed differently; float noise
# in the IV solve is not drift worth reporting.
MATERIAL_PCT = 0.5


def _recompute_rows(payload: dict) -> list[dict] | None:
    """Rebuild matrix rows with current gamma math, from mid prices and OI.

    Returns rows in the shape `exposure.profile_summary` expects, or None when the
    payload lacks what the recomputation needs.
    """
    quote = payload.get("quote") or {}
    spot = quote.get("price_context")
    as_of = payload.get("as_of")
    if not spot or not as_of:
        return None
    today_iso = str(as_of)[:10]

    rebuilt = []
    for row in payload.get("rows") or []:
        strike = row.get("strike")
        if strike is None:
            continue
        cells = []
        for cell in row.get("cells") or []:
            expiry = cell.get("expiration")
            call_oi = cell.get("call_oi") or 0
            put_oi = cell.get("put_oi") or 0
            call_mid = cell.get("call_mid")
            put_mid = cell.get("put_mid")

            call_gex = put_gex = 0.0
            available = False
            for mid, oi, is_call in ((call_mid, call_oi, True), (put_mid, put_oi, False)):
                if not mid or mid <= 0 or not oi or not expiry:
                    continue
                g = greeks.gamma_from_mid(float(mid), float(spot), float(strike),
                                          str(expiry), today_iso, is_call)
                if g is None:
                    continue
                # Same formula as core/exposure.gex: gamma x OI x 100 x spot^2 x 0.01.
                # Puts carry the negative sign; that convention is what makes the
                # gamma flip a zero crossing rather than a minimum.
                value = g * float(oi) * 100.0 * float(spot) ** 2 * 0.01
                available = True
                if is_call:
                    call_gex = value
                else:
                    put_gex = -value
            cells.append({"call_gex": call_gex, "put_gex": put_gex, "available": available})
        if cells:
            rebuilt.append({"strike": strike, "cells": cells})
    return rebuilt or None


def _pct_move(old, new) -> float | None:
    if old is None or new is None:
        return None
    if old == 0:
        return None if new == 0 else 100.0
    return abs(new - old) / abs(old) * 100.0


def _snapshot_levels(payload: dict) -> dict | None:
    rebuilt = _recompute_rows(payload)
    return exposure.profile_summary(rebuilt) if rebuilt else None


def _isolate_fix(db: Path, out: Path, args) -> int:
    """Attribute drift to the parity corrections alone.

    Comparing stored values against a fresh recomputation conflates two things: the
    corrections to `_IV_HI` and the same-day expiry floor, and the fact that the
    capture path prefers a contract's vendor-supplied IV where one exists while this
    recomputation always solves from the mid. Running the SAME recomputation twice,
    changing only the two constants, cancels the IV-source difference and leaves the
    corrections as the only moving part.
    """
    sql = ("select id, ticker, captured_at, raw_json_path from gex_snapshots "
           "order by id desc")
    if not args.all:
        sql += f" limit {int(args.limit)}"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(sql).fetchall()
    conn.close()

    fields = ("call_wall_strike", "put_wall_strike", "gamma_flip_level")
    compared = 0
    moved = {f: 0 for f in fields}
    examples: list[dict] = []
    by_day: dict[str, int] = {}

    # One payload at a time. Holding all of them to run the two passes separately
    # needed roughly a gigabyte on the full history, which is a lot of memory to
    # spend on an ordering detail.
    for sid, ticker, captured_at, raw_path in rows:
        path = Path(raw_path)
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue

        now = _snapshot_levels(payload)
        # Pre-fix constants: the IV bisection ceiling was 5.0, and same-day
        # expiries were not floored to a finite time.
        saved_hi, saved_min_t = greeks._IV_HI, greeks.MIN_T_YEARS
        greeks._IV_HI = 5.0
        greeks.MIN_T_YEARS = 0.0
        try:
            before = _snapshot_levels(payload)
        finally:
            greeks._IV_HI, greeks.MIN_T_YEARS = saved_hi, saved_min_t

        if not now or not before:
            continue
        by_day[str(captured_at)[:10]] = by_day.get(str(captured_at)[:10], 0) + 1
        compared += 1
        for field in fields:
            pct = _pct_move(before.get(field), now.get(field))
            if pct is not None and pct >= MATERIAL_PCT:
                moved[field] += 1
                if len(examples) < 20:
                    examples.append({
                        "snapshot_id": sid, "ticker": ticker, "captured_at": captured_at,
                        "field": field, "pre_fix": before.get(field),
                        "current": now.get(field), "pct_move": round(pct, 2),
                    })

    print("Isolating the parity corrections (same IV source both runs)")
    print(f"  snapshots compared : {compared}")
    if by_day:
        print(f"  capture days       : {len(by_day)} "
              f"({min(by_day)} .. {max(by_day)})")
    for field in fields:
        share = (moved[field] / compared * 100) if compared else 0.0
        print(f"  {field:20s} {moved[field]:>6d}  ({share:.1f}%)")
    if examples:
        print("\n  examples:")
        for e in examples[:8]:
            print(f"    {e['ticker']:6s} {e['field']:20s} {e['pre_fix']} -> "
                  f"{e['current']}  ({e['pct_move']}%)")
    if not any(moved.values()):
        print("\n  No level moved. The corrections changed no stored capture in this "
              "sample, so the drift seen against stored values is attributable to the "
              "IV source, not to the fixes.")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_name("fix_isolation_report.json").write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "isolate_fix",
        "snapshots_compared": compared,
        "by_capture_day": by_day,
        "levels_moved": moved,
        "examples": examples,
        "note": (
            "Both runs recompute from the raw payload's mid prices, so the IV source "
            "is identical and cancels. The only difference is _IV_HI (5.0 -> 10.0) and "
            "the same-day expiry floor, so any movement here is attributable to those "
            "corrections alone."
        ),
    }, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--isolate-fix", action="store_true",
                    help="recompute twice, with current and pre-fix constants, and "
                         "report only the difference between them")
    args = ap.parse_args()

    if args.isolate_fix:
        return _isolate_fix(Path(args.db), Path(args.out), args)

    db = Path(args.db)
    if not db.exists():
        print(f"no capture database at {db}", file=sys.stderr)
        return 1

    sql = ("select id, ticker, captured_at, raw_json_path, call_wall_strike, "
           "put_wall_strike, gamma_flip_level from gex_snapshots order by id desc")
    if not args.all:
        sql += f" limit {int(args.limit)}"

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(sql).fetchall()
    conn.close()

    checked = missing_raw = unrecomputable = 0
    moved = {"call_wall_strike": 0, "put_wall_strike": 0, "gamma_flip_level": 0}
    examples: list[dict] = []
    by_day: dict[str, dict] = {}

    for sid, ticker, captured_at, raw_path, old_call, old_put, old_flip in rows:
        path = Path(raw_path)
        if not path.exists():
            missing_raw += 1
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            missing_raw += 1
            continue

        rebuilt = _recompute_rows(payload)
        if not rebuilt:
            unrecomputable += 1
            continue

        summary = exposure.profile_summary(rebuilt)
        checked += 1
        day = str(captured_at)[:10]
        bucket = by_day.setdefault(day, {"checked": 0, "moved": 0})
        bucket["checked"] += 1

        snapshot_moved = False
        for field, old in (("call_wall_strike", old_call),
                           ("put_wall_strike", old_put),
                           ("gamma_flip_level", old_flip)):
            pct = _pct_move(old, summary.get(field))
            if pct is not None and pct >= MATERIAL_PCT:
                moved[field] += 1
                snapshot_moved = True
                if len(examples) < 25:
                    examples.append({
                        "snapshot_id": sid, "ticker": ticker, "captured_at": captured_at,
                        "field": field, "stored": old, "recomputed": summary.get(field),
                        "pct_move": round(pct, 2),
                    })
        if snapshot_moved:
            bucket["moved"] += 1

    print(f"snapshots examined : {len(rows)}")
    print(f"  recomputed       : {checked}")
    print(f"  raw missing      : {missing_raw}")
    print(f"  not recomputable : {unrecomputable}")
    print(f"\nlevels moved by >= {MATERIAL_PCT}%:")
    for field, count in moved.items():
        share = (count / checked * 100) if checked else 0.0
        print(f"  {field:20s} {count:>6d}  ({share:.1f}% of recomputed)")

    if by_day:
        print("\nby capture day:")
        for day in sorted(by_day):
            b = by_day[day]
            share = (b["moved"] / b["checked"] * 100) if b["checked"] else 0.0
            print(f"  {day}  checked={b['checked']:>5d}  moved={b['moved']:>5d}  ({share:.1f}%)")

    if examples:
        print("\nexamples:")
        for e in examples[:10]:
            print(f"  {e['captured_at'][:16]} {e['ticker']:6s} {e['field']:20s} "
                  f"{e['stored']} -> {e['recomputed']}  ({e['pct_move']}%)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_db": str(db),
        "material_move_pct": MATERIAL_PCT,
        "snapshots_examined": len(rows),
        "recomputed": checked,
        "raw_missing": missing_raw,
        "not_recomputable": unrecomputable,
        "levels_moved": moved,
        "by_capture_day": by_day,
        "examples": examples,
        "note": (
            "Recomputed with current core/exposure.py and core/greeks.py from the raw "
            "capture payloads. gex_snapshots was NOT modified: the gap between stored "
            "and recomputed values is the measurable size of the parity corrections, "
            "and overwriting the stored values would destroy the only record of it."
        ),
    }, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
