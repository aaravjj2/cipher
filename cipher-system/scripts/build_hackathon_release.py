#!/usr/bin/env python3
"""Build and audit a secret-free standalone Alpaca hackathon source bundle."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


SOURCE = Path(__file__).resolve().parents[1]
REPO = SOURCE.parent
DEFAULT_OUTPUT = REPO / "runtime" / "data" / "releases"
ROOT_FILES = (".env.example", "README.md", "Start-Cipher-App.sh")
WEB_FILES = (
    "web/package.json", "web/package-lock.json", "web/next.config.ts",
    "web/tsconfig.json", "web/postcss.config.mjs", "web/eslint.config.mjs",
    "web/playwright.config.ts",
)
TREES = ("app", "config", "core", "web/src", "web/public", "web/e2e")
TESTS = (
    "tests/test_alpaca_paper_broker.py", "tests/test_alpaca_paper_runtime.py",
    "tests/test_autopilot_status.py", "tests/test_alpaca_paper_status.py",
    "tests/test_research_only_guard.py", "tests/test_paper_executor_runtime.py",
    "tests/test_paper_executor_recovery.py", "tests/conftest.py",
)
EXCLUDED_PARTS = {
    ".env", ".git", ".next", ".releases", "__pycache__", "data",
    "node_modules", "runtime", "test-results", "previous-work",
}
EXCLUDED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc", ".key", ".pem"}
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"PK[A-Z0-9]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

MIT_LICENSE = """MIT License

Copyright (c) 2026 Aarav Jain

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

PUBLIC_README = """# Cipher — Auditable Alpaca Paper-Trading Agent

Cipher is an all-in-one stocks and options research cockpit that turns a broad
premarket scan into an evidence-backed plan, waits for deterministic price and
liquidity confirmation, and can execute limit orders in an Alpaca paper account.
The model advises; policy owns eligibility. Every intent is persisted before it
is submitted and reconciled against the broker after restart.

## Judge path

1. Open the app and choose **Continue as guest**.
2. Start at **Autopilot** to see the decision pipeline and safety boundary.
3. Explore Strike Matrix, Night Vision, Setup Scanner, earnings, research,
   backtests, and the paper portfolio. Demo data is always labelled.

![Cipher guest workflow](docs/screenshots/guest-desktop-complete.png)

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8283`. Guest mode requires no credentials. To connect a
paper account, set `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET`; the
adapter rejects any host except `https://paper-api.alpaca.markets`.

## Architecture

```text
market data -> research agents -> evidence reconciliation -> deterministic policy
    -> persisted TradeIntent -> Alpaca paper limit order -> reconciliation ledger
```

The browser has no order endpoint. Live-capital execution is not implemented.
Options involve risk; this project is educational software, not investment advice.

## Tests

```bash
pytest -q tests
node --test app/test/*.test.mjs
npm --prefix web ci
npm --prefix web run check
```

MIT licensed.
"""

DOCKERFILE = """FROM node:20-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip curl \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt
COPY . .
RUN npm --prefix web ci --no-audit --no-fund \
    && npm --prefix web run build \
    && mkdir -p app/public \
    && cp -a web/out/. app/public/
ENV PORT=8283 CIPHER_CORE_PORT=8282 CIPHER_GUEST_MODE=1
EXPOSE 8283
CMD ["bash", "Start-Cipher-App.sh"]
"""

COMPOSE = """services:
  cipher:
    build: .
    ports:
      - "8283:8283"
    env_file:
      - path: .env
        required: false
    environment:
      CIPHER_GUEST_MODE: "1"
    read_only: true
    tmpfs:
      - /tmp
    volumes:
      - cipher-data:/app/data
volumes:
  cipher-data:
"""

GITIGNORE = """.env
.next/
node_modules/
__pycache__/
*.py[cod]
*.db
*.sqlite*
data/
runtime/
test-results/
web/out/
"""

CI = """name: CI
on:
  push:
  pull_request:
jobs:
  safety-and-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - uses: actions/setup-node@v4
        with: { node-version: "20", cache: npm, cache-dependency-path: web/package-lock.json }
      - run: pip install -r requirements.txt
      - run: pytest -q tests
      - run: node --test app/test/*.test.mjs
      - run: npm --prefix web ci --no-audit --no-fund
      - run: npm --prefix web run check
"""


def allowed(path: Path) -> bool:
    relative = path.relative_to(SOURCE)
    return (
        not path.is_symlink()
        and relative.parts[:2] != ("app", "public")
        and not any(part in EXCLUDED_PARTS for part in relative.parts)
        and path.suffix.lower() not in EXCLUDED_SUFFIXES
    )


def copy_file(relative: str, destination: Path) -> None:
    source = SOURCE / relative
    if source.is_file() and allowed(source):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def copy_tree(relative: str, destination: Path) -> None:
    root = SOURCE / relative
    for source in sorted(root.rglob("*")):
        if source.is_file() and allowed(source):
            target = destination / source.relative_to(SOURCE)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def audit(destination: Path) -> dict[str, object]:
    violations: list[str] = []
    files: list[dict[str, object]] = []
    for path in sorted(destination.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(destination).as_posix()
        if path.name == ".env" or any(part in EXCLUDED_PARTS for part in Path(relative).parts):
            violations.append(f"forbidden path: {relative}")
        raw = path.read_bytes()
        if b"\0" not in raw:
            text = raw.decode("utf-8", errors="replace")
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    violations.append(f"possible secret: {relative}")
                    break
        files.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    required = {"README.md", "LICENSE", "Dockerfile", "docker-compose.yml", "app/server.mjs", "core/app.py", "web/src/lib/guestCatalog.ts"}
    present = {str(row["path"]) for row in files}
    violations.extend(f"missing required file: {name}" for name in sorted(required - present))
    if "api.alpaca.markets" in (destination / "core/paper_executor/alpaca_paper_broker.py").read_text().replace("paper-api.alpaca.markets", ""):
        violations.append("paper adapter contains a live Alpaca hostname")
    return {"ok": not violations, "violations": violations, "files": files}


def build(destination: Path) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=False)
    for relative in ROOT_FILES + WEB_FILES + TESTS:
        copy_file(relative, destination)
    shutil.copy2(REPO / "requirements.txt", destination / "requirements.txt")
    for relative in TREES:
        copy_tree(relative, destination)
    (destination / "README.md").write_text(PUBLIC_README, encoding="utf-8")
    (destination / "LICENSE").write_text(MIT_LICENSE, encoding="utf-8")
    (destination / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (destination / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (destination / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    workflow = destination / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(CI, encoding="utf-8")
    screenshots = SOURCE / "web" / "test-results"
    for pattern in ("**/guest-desktop-complete.png", "**/guest-mobile-complete.png"):
        matches = sorted(screenshots.glob(pattern))
        if matches:
            target = destination / "docs" / "screenshots" / matches[-1].name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(matches[-1], target)
    result = audit(destination)
    if not result["ok"]:
        raise RuntimeError("; ".join(result["violations"]))
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), **result}
    (destination / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory(prefix="cipher-alpaca-release-") as temporary:
            result = build(Path(temporary) / "cipher-alpaca-agent")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        result = build(args.output_root / f"cipher-alpaca-agent-{stamp}")
    print(f"hackathon-release-ok files={len(result['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
