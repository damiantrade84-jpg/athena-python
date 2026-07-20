"""Rebuilt backtesting package for Engine A and Engine B.

Parity-first: signal generation always calls the same live scoring functions
(`engine_a_v3.evaluator.evaluate_engine_a_v3`, the Engine B scanner probe).
This package only supplies data, costs, exits, orchestration, and reporting.
"""

import os

# Must be set before `config` (imported transitively by engine_a_v3.backtest,
# scanner, etc.) does its module-level safety validation, or a bare backtest
# process (no live broker/order confirmation) fails CONFIG_SAFETY_FATAL. Set
# unconditionally at package import time rather than relying on callers to
# bootstrap it first (data/fetchers.py's monolith loader also sets it, but
# only when it runs before this package's scoring imports do).
os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")

BT_PACKAGE_VERSION = "bt.v1"
