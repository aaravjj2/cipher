"""Fail-closed simulation and explicitly authorized Alpaca-paper execution.

The browser and research API remain read-only. Broker activity is confined to
the hardcoded Alpaca paper host, and Cipher's SQLite intent ledger remains the
authoritative audit trail. Live-capital execution is intentionally absent.
"""

from .config import ExecutorConfig, load_config

__all__ = ["ExecutorConfig", "load_config"]
