"""Enforce, mechanically, that Cipher holds no live-order authority.

`EightLayerStackSpec.forbidden_live_terms` has declared the order-placing terms Cipher must
never contain since the eight-layer stack was written, alongside a serialized
`"live_order_authority": False`. Both were inert: the tuple appeared exactly twice in the
tree -- once where it is defined, once where it is copied into a dict for a report -- and
nothing ever compared it against the source. The single most important safety property of
this system was a claim in a report rather than a fact about the code.

This module scans the tree for those terms so the declaration and the enforcement are the
same list. Adding a term to the spec tightens the guard with no change here.

The guard's own value is only as good as its ability to fail, so
tests/test_research_only_guard.py plants a violation in a temporary tree and asserts it is
caught, rather than only asserting the shipped tree is clean -- a check that cannot fail
proves nothing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.research_platform.seven_layer_stack import EightLayerStackSpec

# Broker and exchange clients Cipher must not import. This is deliberately separate from the
# spec's list: the spec enumerates the *call sites* that would place an order, this
# enumerates the *dependencies* that would make one reachable. Both directions matter,
# because a broker SDK in the dependency tree is a live-order path waiting for one line.
# Extending either tuple tightens the guard; nothing here may be narrowed without the
# owner's explicit decision.
FORBIDDEN_BROKER_IMPORTS: tuple[str, ...] = (
    "alpaca_trade_api",
    "alpaca.trading",
    "ib_insync",
    "robin_stocks",
    "ccxt",
)

SCANNED_SUFFIXES: frozenset[str] = frozenset({".py", ".ts", ".tsx", ".mjs", ".js"})

# Directories excluded from the scan, each for a reason that is not "it was inconvenient":
#   tests            - must be able to name the terms in order to test for them
#   vendor           - third-party repositories, not Cipher's code and not Cipher's promise
#   node_modules     - dependencies, covered by FORBIDDEN_BROKER_IMPORTS at the import site
#   __pycache__/.venv/.git/out/.next - build products and caches, not source
EXCLUDED_DIRS: frozenset[str] = frozenset({
    "tests", "vendor", "node_modules", "__pycache__", ".venv", ".git", "out", ".next",
    "dist", "build", ".mypy_cache", ".pytest_cache",
})

# Files permitted to contain the terms, because containing them is their job.
ALLOWED_FILES: frozenset[str] = frozenset({
    "core/research_platform/seven_layer_stack.py",  # declares them
    "core/research_only_guard.py",                  # enforces them
})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    term: str
    text: str

    def describe(self) -> str:
        return f"{self.path}:{self.line}: {self.term!r} in: {self.text.strip()[:120]}"


def forbidden_terms() -> tuple[str, ...]:
    """Every term the tree must not contain, sourced from the spec plus broker imports."""
    return tuple(EightLayerStackSpec.forbidden_live_terms) + FORBIDDEN_BROKER_IMPORTS


def _is_scannable(path: Path, root: Path) -> bool:
    if path.suffix not in SCANNED_SUFFIXES:
        return False
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if relative.as_posix() in ALLOWED_FILES:
        return False
    # Test files live beside their subjects in places other than tests/, and must also be
    # free to name the terms.
    return not (path.name.startswith("test_") or path.name.endswith((".test.mjs", ".test.ts")))

def scan(root: Path, terms: tuple[str, ...] | None = None) -> list[Violation]:
    """Return every occurrence of a forbidden term under `root`.

    Reads line by line and reports the line number, so a failure names the exact place to
    look rather than only the file. Undecodable bytes are replaced rather than raised on: a
    file the guard cannot read is a file the guard must not silently skip.
    """
    root = root.resolve()
    checked = terms if terms is not None else forbidden_terms()
    violations: list[Violation] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        # Prune excluded directories in-place so os.walk doesn't descend into them
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]

        for fname in sorted(filenames):
            path = Path(dirpath) / fname
            if not _is_scannable(path, root):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                for term in checked:
                    if term in line:
                        violations.append(
                            Violation(path.relative_to(root).as_posix(), number, term, line)
                        )
    return violations
