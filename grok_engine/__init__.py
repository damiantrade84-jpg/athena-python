"""GROK — Killzone Displacement Delivery intraday engine.

The package owns its session clock, ICT-native indicators, scoring, signal
lifecycle, persistence, and execution attestation. Market-data and broker
clients are injected at the runtime boundary so the analytical core stays
deterministic and import-safe.
"""

from .config import GrokConfig, load_grok_config
from .scoring import evaluate_snapshot

__all__ = ["GrokConfig", "evaluate_snapshot", "load_grok_config"]
