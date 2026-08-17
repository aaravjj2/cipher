#!/usr/bin/env python3
"""Fail-closed public-release audit for the QuantumHacks submission.

The script reports file names and rule identifiers only. It never prints a
matched secret value. It does not publish, commit, delete, or change the source
tree.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md", "docs/quantumhacks/submission.md", "docs/quantumhacks/demo-script.md",
    "docs/quantumhacks/architecture.mmd", "docs/quantumhacks/release-checklist.md",
    "docs/quantumhacks/three-day-win-plan.md",
)
FORBIDDEN_PARTS = {"data", "runtime", "previous-work", "node_modules", ".next", ".releases"}
SECRET_RULES = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{30,}\b"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "tailscale_key": re.compile(r"\btskey-[A-Za-z0-9_-]{20,}\b"),
    "discord_webhook": re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def repository_files(root: Path) -> list[Path]:
    """Return auditable files without following symlinks.

    The active checkout uses git's view so ignored private artifacts are not
    mistaken for release inputs. A staged/export directory is scanned directly
    because it intentionally has no .git directory.
    """
    git_root = root.parent
    if not (git_root / ".git").exists() or root.name != "cipher-system":
        return sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())
    result = subprocess.run(
        ["git", "-C", str(git_root), "ls-files", "--cached", "--others", "--exclude-standard", "cipher-system"],
        check=True, capture_output=True, text=True,
    )
    prefix = "cipher-system/"
    return sorted(root / line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix))


def audit(root: Path) -> dict:
    findings: list[dict[str, str]] = []
    files = repository_files(root)
    for required in REQUIRED:
        if not (root / required).is_file():
            findings.append({"severity": "blocker", "rule": "required_asset_missing", "path": required})
    for path in files:
        relative = path.relative_to(root)
        parts = set(relative.parts)
        if relative.name == "auth.json" or (relative.name.startswith(".env") and relative.name != ".env.example"):
            findings.append({"severity": "blocker", "rule": "private_config_in_release", "path": str(relative)})
            continue
        forbidden = sorted(parts.intersection(FORBIDDEN_PARTS))
        if forbidden:
            findings.append({"severity": "blocker", "rule": f"forbidden_tree:{forbidden[0]}", "path": str(relative)})
            continue
        try:
            if path.stat().st_size > 2_000_000:
                findings.append({"severity": "review", "rule": "large_file", "path": str(relative)})
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if relative.name == ".env.example":
            continue
        for rule, pattern in SECRET_RULES.items():
            if pattern.search(text):
                findings.append({"severity": "blocker", "rule": rule, "path": str(relative)})
    blockers = [row for row in findings if row["severity"] == "blocker"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": root.name, "files_checked": len(files),
        "public_release_ready": not blockers, "blockers": len(blockers),
        "reviews": sum(row["severity"] == "review" for row in findings),
        "findings": findings,
        "publication_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"release root does not exist: {root}")
    result = audit(root)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["public_release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
