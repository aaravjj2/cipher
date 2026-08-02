#!/usr/bin/env python3
"""Ingest selected, revision-pinned Hugging Face data into Cipher's local raw lake."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[0]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.huggingface_datasets import (  # noqa: E402
    approved_source,
    ingest_approved_file,
    verify_revision,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=("options_iv_sp500", "ohlcv_1m"))
    parser.add_argument("--file", action="append", default=[], dest="files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    source = approved_source(args.source)
    files = args.files or list(source.files)
    if not files:
        raise SystemExit("--file is required for the monthly OHLCV archive")
    if args.dry_run:
        remote = verify_revision(source)
        selected = {filename: remote["files"].get(filename) for filename in files}
        if any(size is None for size in selected.values()):
            raise SystemExit("one or more requested files do not exist at the pinned revision")
        print(json.dumps({
            "source": source.name,
            "repo_id": source.repo_id,
            "revision": source.revision,
            "gated": remote["gated"],
            "files": selected,
        }, indent=2, sort_keys=True))
        return 0
    results = [
        ingest_approved_file(source, filename, repository_root=REPOSITORY_ROOT)
        for filename in files
    ]
    print(json.dumps({"ingested": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
