"""Paper-only local options executor for Cipher.

This package intentionally contains no live-trading mode and no broker order
submission code. The Windows SQLite ledger is the authoritative paper account.
"""

from .config import ExecutorConfig, load_config

__all__ = ["ExecutorConfig", "load_config"]
