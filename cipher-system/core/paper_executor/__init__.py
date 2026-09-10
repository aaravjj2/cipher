"""Fail-closed Cipher-owned paper-portfolio execution.

The browser and research API remain read-only. The deployed backend records
modeled fills in Cipher's SQLite ledger and has no external order capability.
The legacy Alpaca-paper adapter is retained only as an inactive, separately
guarded compatibility path. Live-capital execution is intentionally absent.
"""

from .config import ExecutorConfig, load_config

__all__ = ["ExecutorConfig", "load_config"]
