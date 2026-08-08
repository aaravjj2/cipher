"""Merge GEX capture rows from one history database into another.

Two machines capture independently, so neither database is a superset of the other
by construction. Pulling the VM's history down replaces the local index wholesale,
and any snapshot this machine captured at a timestamp the VM did not is dropped
from the index even though its raw payload survives on disk. Point-in-time open
interest cannot be re-fetched, so an unindexed raw payload is data you still own
and can no longer find.

This merges the missing rows back. Snapshots are identified by `(ticker,
captured_at)`, not by row id: the two databases assign ids independently and the
same id means different things in each.

`run_id` is remapped rather than copied. It is a foreign key into
`gex_capture_runs`, whose ids are also machine-local, so copying it verbatim would
point a merged snapshot at an unrelated capture run — or at no run at all.

Nothing is overwritten. Rows already present in the target are skipped, so running
this twice is a no-op rather than a duplication.

Usage:
  python3 scripts/merge_gex_snapshots.py --from data/gex_history.sqlite.pre-pull-X
  python3 scripts/merge_gex_snapshots.py --from OLD --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "data" / "gex_history.sqlite"

SNAPSHOT_COLUMNS = [
    "ticker", "captured_at", "feed", "spot", "day_change_pct", "raw_json_path",
    "contracts", "calculated_cells", "listed_cells", "global_max_strike",
    "call_wall_strike", "put_wall_strike", "gamma_flip_level", "caveat",
]


def _run_columns(conn: sqlite3.Connection) -> list[str]:
    cols = [r[1] for r in conn.execute("pragma table_info(gex_capture_runs)")]
    return [c for c in cols if c != "id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="source", required=True)
    ap.add_argument("--to", dest="target", default=str(DEFAULT_TARGET))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    source_path, target_path = Path(args.source), Path(args.target)
    for path in (source_path, target_path):
        if not path.exists():
            print(f"missing database: {path}", file=sys.stderr)
            return 1

    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    dst = sqlite3.connect(target_path)

    have = {r[0] for r in dst.execute(
        "select ticker || '|' || captured_at from gex_snapshots")}

    missing = [row for row in src.execute(
        "select id, run_id, ticker, captured_at from gex_snapshots")
        if f"{row[2]}|{row[3]}" not in have]

    print(f"source rows      : {src.execute('select count(*) from gex_snapshots').fetchone()[0]}")
    print(f"target rows      : {len(have)}")
    print(f"missing in target: {len(missing)}")
    if not missing:
        print("nothing to merge")
        return 0

    days: dict[str, int] = {}
    for _sid, _rid, _t, captured_at in missing:
        day = str(captured_at)[:10]
        days[day] = days.get(day, 0) + 1
    print("  by capture day :", dict(sorted(days.items())))

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    run_cols = _run_columns(src)
    placeholders = ", ".join("?" for _ in run_cols)
    run_map: dict[int, int] = {}
    inserted = 0

    with dst:
        for sid, run_id, _ticker, _captured in missing:
            if run_id not in run_map:
                run_row = src.execute(
                    f"select {', '.join(run_cols)} from gex_capture_runs where id = ?",
                    (run_id,)).fetchone()
                if run_row is None:
                    # A snapshot whose run row is gone still carries its own
                    # captured_at and raw payload, so it is worth keeping; it just
                    # cannot be attributed to a run.
                    run_map[run_id] = None
                else:
                    cur = dst.execute(
                        f"insert into gex_capture_runs ({', '.join(run_cols)}) "
                        f"values ({placeholders})", run_row)
                    run_map[run_id] = int(cur.lastrowid)

            snap = src.execute(
                f"select {', '.join(SNAPSHOT_COLUMNS)} from gex_snapshots where id = ?",
                (sid,)).fetchone()
            if snap is None:
                continue
            dst.execute(
                f"insert into gex_snapshots (run_id, {', '.join(SNAPSHOT_COLUMNS)}) "
                f"values (?, {', '.join('?' for _ in SNAPSHOT_COLUMNS)})",
                (run_map[run_id], *snap))
            inserted += 1

    total, day_count = dst.execute(
        "select count(*), count(distinct substr(captured_at,1,10)) from gex_snapshots"
    ).fetchone()
    print(f"\nmerged {inserted} rows")
    print(f"target now: {total} snapshots over {day_count} capture days")
    src.close()
    dst.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
