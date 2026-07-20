"""Lazy, thin wrappers around the historical OHLCV fetchers already implemented
in the ``athena.py`` monolith (``fetch_binance_paginated``, ``fetch_mt5``,
``fetch_eodhd``). Nothing here reimplements fetch logic.

The monolith is only loaded when a top-up fetch is actually requested, using
the same bootstrap `tools/run_backtest_matrix.py` and `engine_b_batch.py`
already rely on: load ``athena.py`` by file path (not ``import athena``, which
resolves to the ``athena/`` package) with ``ATHENA_DIAGNOSTIC_MODE=1`` set so
no live feeds/threads/MT5 pollers start. Loaded once per process and memoized.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading

_lock = threading.Lock()
_monolith = None


def _load_monolith():
    global _monolith
    if _monolith is not None:
        return _monolith
    with _lock:
        if _monolith is not None:
            return _monolith
        os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        athena_path = os.path.join(repo_root, "athena.py")
        spec = importlib.util.spec_from_file_location("athena_monolith", athena_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load athena.py from {athena_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _monolith = mod
        return _monolith


def fetch_binance_paginated(symbol: str, interval: str, total_bars: int) -> list[dict] | None:
    """Unlimited-depth Binance USD-M futures klines (paginated)."""
    return _load_monolith().fetch_binance_paginated(symbol, interval, total_bars)


def fetch_mt5(pair: dict, tf: str, limit: int) -> list[dict] | None:
    """MT5 terminal history (requires the local terminal to be running/connected)."""
    return _load_monolith().fetch_mt5(pair, tf, limit)


def fetch_eodhd(pair: dict, tf: str, limit: int) -> list[dict] | None:
    """EODHD history, capped by provider (D1 ~730d, intraday H1 ~365d), rate-limited."""
    return _load_monolith().fetch_eodhd(pair, tf, limit)
