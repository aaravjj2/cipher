"""Compatibility re-export for hosts that import ``cipher_fleet.app``."""

from .agent import app, root_agent

__all__ = ["app", "root_agent"]
