"""SOL — Session-Oriented Liquidity intraday trading engine.

The package owns its indicators, scoring, signal lifecycle, persistence, and
execution attestation.  Market-data and broker clients are injected at the
runtime boundary so the analytical core stays deterministic and import-safe.
"""

from .config import SolConfig, load_sol_config
from .scoring import evaluate_snapshot

__all__ = ["SolConfig", "evaluate_snapshot", "load_sol_config"]

