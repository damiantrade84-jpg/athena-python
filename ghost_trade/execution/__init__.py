"""Verified demo-only execution adapters for Ghost Trade."""

from .base import DemoAttestation, DemoExecutionResult
from .bybit_demo import BybitDemoAdapter
from .mt5_demo import MT5DemoAdapter

__all__ = [
    "BybitDemoAdapter",
    "DemoAttestation",
    "DemoExecutionResult",
    "MT5DemoAdapter",
]
