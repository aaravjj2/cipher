#!/usr/bin/env python3
"""Linux atomic directory exchange used by the local static-site publisher."""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import sys

AT_FDCWD = -100
RENAME_EXCHANGE = 2


def exchange(left: Path, right: Path) -> None:
    """Atomically exchange two existing paths on the same filesystem."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable; refusing non-atomic publish")
    result = renameat2(
        AT_FDCWD, os.fsencode(left), AT_FDCWD, os.fsencode(right), RENAME_EXCHANGE,
    )
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), f"{left} <-> {right}")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: atomic_publish.py STAGED_DIR LIVE_DIR", file=sys.stderr)
        return 2
    staged, live = map(Path, argv[1:])
    if not staged.is_dir() or not live.is_dir():
        raise ValueError("staged and live must both be existing directories")
    if staged.stat().st_dev != live.stat().st_dev:
        raise ValueError("staged and live must be on the same filesystem")
    exchange(staged, live)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
