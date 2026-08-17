#!/usr/bin/env python3
"""Build a local, allowlisted QuantumHacks review bundle.

This does not publish, commit, push, or change the active application. Runtime
data, credentials, caches, rollback releases, and private research archives are
excluded by construction.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


SOURCE = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = SOURCE.parent / "runtime" / "data" / "releases"

ROOT_FILES = (
    ".env.example",
    "README.md",
    "DESIGN.md",
    "Start-Cipher-App.sh",
)
DIRECTORIES = (
    "app",
    "config",
    "core",
    "docs/quantumhacks",
    "scripts",
    "tests",
    "web/e2e",
    "web/public",
    "web/src",
)
WEB_FILES = (
    "web/eslint.config.mjs",
    "web/next.config.ts",
    "web/package-lock.json",
    "web/package.json",
    "web/playwright.config.ts",
    "web/postcss.config.mjs",
    "web/tsconfig.json",
)
SCREENSHOTS = (
    "morning-brief-v4-mobile.png",
    "setup-scanner-v2-desktop.png",
    "night-vision-v2-desktop.png",
    "options-history-v2-desktop.png",
    "paper-portfolios-v3-desktop.png",
    "research-desk-desktop.png",
    "backtest-v2-desktop.png",
)
EXCLUDED_NAMES = {
    ".env", ".next", ".releases", "__pycache__", "auth.json", "data",
    "node_modules", "runtime", "test-results",
}
EXCLUDED_SUFFIXES = {".db", ".db-shm", ".db-wal", ".pyc", ".sqlite", ".sqlite3"}


def allowed(path: Path) -> bool:
    try:
        relative = path.relative_to(SOURCE)
    except ValueError:
        # The only allowlisted external input is the monorepo requirements.txt.
        return path == SOURCE.parent / "requirements.txt"
    return not any(part in EXCLUDED_NAMES for part in relative.parts) and not any(
        path.name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES
    )


def copy_file(source: Path, destination: Path) -> None:
    if source.is_file() and not source.is_symlink() and allowed(source):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def copy_tree(relative: str, destination: Path) -> None:
    source_root = SOURCE / relative
    if not source_root.is_dir():
        return
    for source in sorted(source_root.rglob("*")):
        if source.is_file() and not source.is_symlink() and allowed(source):
            copy_file(source, destination / source.relative_to(SOURCE))


def manifest(destination: Path) -> dict:
    files = []
    for path in sorted(destination.rglob("*")):
        if not path.is_file() or path.name == "release-manifest.json":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": str(path.relative_to(destination)), "bytes": path.stat().st_size, "sha256": digest})
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "private-worktree",
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "publication_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output_root.resolve() / f"quantumhacks-public-{stamp}"
    if destination.exists():
        parser.error(f"refusing to overwrite existing export: {destination}")
    destination.mkdir(parents=True)
    for relative in ROOT_FILES + WEB_FILES:
        copy_file(SOURCE / relative, destination / relative)
    # The private monorepo keeps the active Python requirements one directory
    # above cipher-system. Flatten it into the standalone submission root.
    copy_file(SOURCE.parent / "requirements.txt", destination / "requirements.txt")
    for relative in DIRECTORIES:
        copy_tree(relative, destination)
    screenshot_root = SOURCE / "web" / "test-results"
    for name in SCREENSHOTS:
        source = screenshot_root / name
        if source.is_file():
            target = destination / "docs" / "quantumhacks" / "screenshots" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    result = manifest(destination)
    (destination / "release-manifest.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "destination": str(destination),
        "file_count": result["file_count"],
        "total_bytes": result["total_bytes"],
        "publication_performed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
