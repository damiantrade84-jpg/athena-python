"""Standalone Ghost Trade engine.

Ghost Trade owns its scoring and lifecycle state.  Importing this package never
starts scanners, schedulers, broker clients, or order execution.
"""

from .config import GhostConfig, GhostConfigError, load_ghost_config

__all__ = ["GhostConfig", "GhostConfigError", "load_ghost_config"]
