"""FABLE — Narrative Liquidity Engine.

FABLE reads every market as a five-act story told by liquidity:

    I.   DRAW    — where the higher-timeframe range wants price to go
    II.  RAID    — the liquidity pool that was just swept
    III. SHIFT   — the displacement that changed structure after the raid
    IV.  RETURN  — price coming back into the imbalance the shift left behind
    V.   CHORUS  — the quantitative and external voices that agree or dissent

The acts are scored independently and fused with a weighted geometric mean, so
one weak act drags the whole narrative down instead of being averaged away.
The package owns its structure detection, quant overlays, scoring, levels,
persistence and execution attestation. Market data, broker clients and
context feeds are injected at the runtime boundary so the analytical core
stays deterministic and import-safe.
"""

from .config import FableConfig, FableConfigError, load_fable_config
from .narrative import evaluate_snapshot

__all__ = ["FableConfig", "FableConfigError", "evaluate_snapshot", "load_fable_config"]
