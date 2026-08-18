"""RQS - Regime Quadrant State.

Every setup has a regime in which it is a good trade and a regime in which it
is the exact wrong trade. Fading an extension is excellent in a balancing
auction and is how accounts die in a trend. Buying a breakout is correct out of
compression and is a donation inside a range.

RQS classifies the market on two independent axes and routes accordingly:

    volatility  expanding or contracting, from the z-score of realised
                dispersion against its own recent baseline
    structure   balanced or imbalanced, from rotation around session value
                combined with fractal efficiency

    imbalanced + expanding  -> TREND_DRIVE     continuation is favoured
    imbalanced + contracting-> COILED          expansion is pending
    balanced   + expanding  -> VOLATILE_RANGE  extremes are tradable
    balanced   + contracting-> DORMANT         nothing is tradable

The routing table in opus.yaml can disable an archetype outright in a regime by
setting its multiplier to zero, and an archetype missing from a regime row is
treated as disabled rather than neutral.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import opus.config as config
from opus.core.stats import rolling_std, zscore_last
from opus.indicators.base import MarketContext
from opus.indicators.kinetics import kinetics
from opus.indicators.value import session_value
from opus.types import Regime


@dataclass
class RegimeState:
    regime: Regime
    vol_z: float
    expanding: bool
    rotation: float
    efficiency: float
    jump_ratio: float
    imbalanced: bool
    confidence: float

    def as_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "volZ": round(float(self.vol_z), 4),
            "expanding": bool(self.expanding),
            "rotation": round(float(self.rotation), 4),
            "efficiency": round(float(self.efficiency), 4),
            "jumpRatio": round(float(self.jump_ratio), 4),
            "imbalanced": bool(self.imbalanced),
            "confidence": round(float(self.confidence), 4),
        }


def compute(ctx: MarketContext) -> RegimeState:
    cfg = config.indicator("RQS")
    candles = ctx.structure
    close = candles.close
    n = close.shape[0]

    # --- volatility axis ---------------------------------------------------
    vol_z = 0.0
    baseline = int(cfg["vol_baseline_window"])
    if n > 30:
        prices = np.where(close > 0, close, np.nan)
        rets = np.diff(np.log(prices))
        if np.isfinite(rets).sum() > 20:
            sigma_series = rolling_std(rets, max(10, baseline // 6))
            sigma_series = sigma_series[np.isfinite(sigma_series)]
            if sigma_series.shape[0] > 12:
                vol_z = zscore_last(sigma_series, baseline)

    expansion_z = float(cfg["vol_expansion_z"])
    contraction_z = float(cfg["vol_contraction_z"])
    if vol_z >= expansion_z:
        expanding = True
    elif vol_z <= contraction_z:
        expanding = False
    else:
        # Neutral band: no coin flips. Lean on the sign so the classification
        # is total and stable rather than depending on which threshold is
        # nearer in an arbitrary metric.
        expanding = vol_z >= 0.0

    # --- structure axis ----------------------------------------------------
    k = kinetics(ctx)
    efficiency = float(k.get("efficiency", 0.0))
    jr = float(k.get("jumpRatio", 0.0))

    value = session_value(ctx)
    rotation = float(value["rotation"]) if value else 0.5

    rotation_balance = float(cfg["rotation_balance"])
    efficiency_trend = float(cfg["efficiency_trend"])
    # Imbalanced requires BOTH a directional path and an auction that is not
    # rotating around its own value. Either alone is routinely produced by
    # noise on a short session sample.
    imbalanced = (efficiency >= efficiency_trend) and (rotation <= rotation_balance)

    if imbalanced:
        regime = Regime.TREND_DRIVE if expanding else Regime.COILED
    else:
        regime = Regime.VOLATILE_RANGE if expanding else Regime.DORMANT

    # Confidence: how far from every boundary this classification sits. A
    # market one basis point from the rotation threshold is not confidently
    # anything, and downstream sizing should know.
    margins = [
        abs(vol_z - (expansion_z if expanding else contraction_z)),
        abs(efficiency - efficiency_trend) * 3.0,
        abs(rotation - rotation_balance) * 3.0,
    ]
    confidence = float(np.clip(np.tanh(np.mean(margins) * 1.5), 0.05, 1.0))

    return RegimeState(
        regime=regime,
        vol_z=float(vol_z),
        expanding=bool(expanding),
        rotation=rotation,
        efficiency=efficiency,
        jump_ratio=jr,
        imbalanced=bool(imbalanced),
        confidence=confidence,
    )


__all__ = ["RegimeState", "compute"]
