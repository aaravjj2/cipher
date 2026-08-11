#!/usr/bin/env python3
"""Export the per-run download configs that make `data/historical_options` rebuildable.

`data/historical_options` is 9.8 GB and deliberately excluded from the GCS backup on the
grounds that it is reproducible from Alpaca. That is only true if the *recipe* survives, and
the recipe is not in the manifest: `download_manifest.json` carries `latest_run_config`,
which is the last run only. The leveraged_etf_wheel dataset was built by 205 runs, and its
stored config covers a single day for a single underlying.

The full recipe lives in each dataset's own `historical_options.sqlite`, in
`download_runs.config_json` — inside the directory that is not backed up. So losing the
directory loses both the data and the instructions for rebuilding it, which makes the
exclusion unsafe rather than economical.

This writes those configs to a small JSON per dataset (kilobytes, against 9.8 GB of bars) so
the backup can carry the recipe while continuing to skip the bulk. Read-only against every
source database.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "historical_options"
DEFAULT_OUTPUT = ROOT / "data" / "options_rebuild_recipes"


def dataset_recipe(db_path: Path) -> dict:
    """Every recorded download run for one dataset, newest last."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "download_runs" not in tables:
            return {"error": "no download_runs table", "runs": []}
        rows = [
            dict(row)
            for row in connection.execute(
                """SELECT id, started_at, completed_at, status, underlying,
                          start_date, end_date, config_json, error
                   FROM download_runs ORDER BY id"""
            )
        ]
    finally:
        connection.close()

    runs = []
    for row in rows:
        raw = row.pop("config_json", None)
        try:
            row["config"] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            # Keep the unparsed text rather than dropping it: a malformed config is still
            # the only record of what that run asked for.
            row["config"] = {"unparsed": str(raw)}
        runs.append(row)
    completed = [r for r in runs if r.get("status") == "completed"]
    return {
        "runs": runs,
        "run_count": len(runs),
        "completed_run_count": len(completed),
        "underlyings": sorted({r["underlying"] for r in runs if r.get("underlying")}),
    }


def export(source: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []
    missing: list[str] = []
    # Recursive rather than one level deep: eod_indices_targeted nests a dataset per index
    # (iwm/qqq/spy, two runs each), and a top-level-only walk silently missed all three --
    # exactly the kind of gap that makes a recovery path look complete when it is not.
    databases = sorted(source.rglob("historical_options.sqlite"))
    found_dirs = {db.parent for db in databases}
    for candidate in sorted(p for p in source.iterdir() if p.is_dir()):
        if not any(d == candidate or candidate in d.parents for d in found_dirs):
            missing.append(candidate.name)
    for db in databases:
        dataset_dir = db.parent
        # Name by path relative to the source so nested datasets stay distinguishable
        # instead of three siblings all writing to "iwm.json".
        name = dataset_dir.relative_to(source).as_posix().replace("/", "__")
        recipe = dataset_recipe(db)
        recipe["dataset"] = name
        recipe["source_database"] = str(db)
        recipe["exported_at"] = datetime.now(timezone.utc).isoformat()
        manifest = dataset_dir / "download_manifest.json"
        if manifest.is_file():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                recipe["provider"] = payload.get("provider")
                recipe["dataset_id"] = payload.get("dataset_id")
                recipe["status"] = payload.get("status")
            except (OSError, ValueError):
                pass
        target = output / f"{name}.json"
        target.write_text(json.dumps(recipe, indent=2, sort_keys=True, default=str), encoding="utf-8")
        written.append({
            "dataset": name,
            "runs": recipe["run_count"],
            "bytes": target.stat().st_size,
        })
    index = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "datasets": written,
        "datasets_without_database": missing,
        "total_runs": sum(item["runs"] for item in written),
        "total_bytes": sum(item["bytes"] for item in written),
        "why": (
            "download_manifest.json records only latest_run_config; the full recipe is "
            "download_runs.config_json inside each dataset database, which the GCS backup "
            "excludes along with the bars. This export is what makes that exclusion safe."
        ),
    }
    (output / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True), encoding="utf-8"
    )
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.source.is_dir():
        raise SystemExit(f"no historical options directory at {args.source}")
    index = export(args.source, args.output)
    print(json.dumps(
        {k: v for k, v in index.items() if k != "datasets"} | {"datasets": len(index["datasets"])},
        indent=2, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
