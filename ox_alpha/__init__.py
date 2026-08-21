"""OX Alpha — standalone intraday kinetic-liquidity engine.

Independent scoring stack (own indicators, own group-aware profiles, own gates).
It consumes the platform's shared data feeds, timeframe policy, and demo-gated
execution primitives, but shares no scoring logic with any other engine.
"""

__version__ = "1.0.0"
ENGINE_ID = "ox_alpha"
ENGINE_LABEL = "OX Alpha"
