"""The OPUS conviction stack.

Most scoring models are a weighted sum. A weighted sum has a specific and
expensive failure: one indicator at maximum can carry a signal past a threshold
while every other input disagrees with it. That trade looks strong on the
scorecard and is, in fact, the market telling you two contradictory things.

OPUS multiplies the weighted mean by a COHERENCE term - one minus the weighted
dispersion of the aligned readings. Concretely:

    {+0.6, +0.6, +0.6}  -> mean 0.60, coherence high  -> conviction ~0.60
    {+1.0, +1.0, -0.2}  -> mean 0.60, coherence low   -> conviction ~0.35

Both score 0.60 additively. Only the first is a trade.

Indicator confidence multiplies its weight, so a reading backed by three bars
of evidence cannot outvote one backed by ninety. The conditioning gains from
regime and clock are applied last and are strictly multiplicative: they can
veto a setup (a zero routes it out entirely) but they can never manufacture
conviction that the evidence did not produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import opus.config as config
from opus.core.stats import weighted_dispersion
from opus.types import Archetype, Direction, IndicatorReading, Regime


@dataclass
class Contribution:
    name: str
    value: float          # raw signed reading
    aligned: float        # signed toward the candidate direction
    weight: float         # configured weight
    confidence: float
    effective_weight: float

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": round(float(self.value), 4),
            "aligned": round(float(self.aligned), 4),
            "weight": round(float(self.weight), 4),
            "confidence": round(float(self.confidence), 4),
            "effectiveWeight": round(float(self.effective_weight), 4),
        }


@dataclass
class ConvictionResult:
    direction: Direction
    archetype: Archetype
    conviction: float          # final, after gains, clipped to [-1, 1]
    base: float                # weighted mean before coherence
    coherence: float
    regime_gain: float
    temporal_gain: float
    active: int
    contributions: list[Contribution] = field(default_factory=list)
    reason: str = ""

    @property
    def viable(self) -> bool:
        return self.conviction > 0.0 and not self.reason

    def as_dict(self) -> dict:
        return {
            "direction": self.direction.value,
            "archetype": self.archetype.value,
            "conviction": round(float(self.conviction), 4),
            "base": round(float(self.base), 4),
            "coherence": round(float(self.coherence), 4),
            "regimeGain": round(float(self.regime_gain), 4),
            "temporalGain": round(float(self.temporal_gain), 4),
            "active": int(self.active),
            "contributions": [c.as_dict() for c in self.contributions],
            "reason": self.reason,
        }


def score(
    readings: list[IndicatorReading],
    *,
    archetype: Archetype,
    direction: Direction,
    regime: Regime,
    temporal_gain: float = 1.0,
) -> ConvictionResult:
    """Score one (archetype, direction) hypothesis against the evidence."""
    cfg = config.load()["COHERENCE"]
    weights = config.weights_for(archetype.value)
    sign = float(direction.sign)

    contributions: list[Contribution] = []
    for reading in readings:
        weight = float(weights.get(reading.name, 0.0))
        if weight <= 0:
            continue
        confidence = float(np.clip(reading.confidence, 0.0, 1.0))
        effective = weight * confidence
        contributions.append(
            Contribution(
                name=reading.name,
                value=float(reading.value),
                aligned=sign * float(reading.value),
                weight=weight,
                confidence=confidence,
                effective_weight=effective,
            )
        )

    usable = [c for c in contributions if c.effective_weight > 1e-9]
    min_active = int(cfg["min_active_indicators"])
    if len(usable) < min_active:
        return ConvictionResult(
            direction=direction, archetype=archetype, conviction=0.0, base=0.0,
            coherence=0.0, regime_gain=0.0, temporal_gain=float(temporal_gain),
            active=len(usable), contributions=contributions,
            reason=f"only_{len(usable)}_of_{min_active}_indicators_active",
        )

    values = np.array([c.aligned for c in usable], dtype=float)
    w = np.array([c.effective_weight for c in usable], dtype=float)
    total = float(w.sum())
    if total <= 0:
        return ConvictionResult(
            direction=direction, archetype=archetype, conviction=0.0, base=0.0,
            coherence=0.0, regime_gain=0.0, temporal_gain=float(temporal_gain),
            active=0, contributions=contributions, reason="zero_total_weight",
        )

    base = float(np.sum(w * values) / total)

    dispersion = weighted_dispersion(values, w)
    scale = float(cfg["dispersion_scale"])
    coherence = float(np.clip(1.0 - dispersion / max(scale, 1e-9), 0.0, 1.0))

    exponent = float(cfg["exponent"])
    conviction = base * float(np.power(coherence, exponent))

    regime_gain = float(config.regime_multiplier(regime.value, archetype.value))
    gain = float(np.clip(temporal_gain, 0.0, 2.0))
    conviction = float(np.clip(conviction * regime_gain * gain, -1.0, 1.0))

    reason = ""
    if regime_gain <= 0.0:
        reason = f"archetype_disabled_in_{regime.value}"

    return ConvictionResult(
        direction=direction,
        archetype=archetype,
        conviction=conviction,
        base=base,
        coherence=coherence,
        regime_gain=regime_gain,
        temporal_gain=gain,
        active=len(usable),
        contributions=contributions,
        reason=reason,
    )


def best_hypothesis(
    readings: list[IndicatorReading],
    *,
    regime: Regime,
    temporal_gain: float = 1.0,
    archetypes: list[Archetype] | None = None,
) -> list[ConvictionResult]:
    """Score every (archetype, direction) pair and return them ranked.

    Both directions are always evaluated. Deciding direction from the sign of
    a blended score first, then scoring only that side, hides the case where
    the evidence is genuinely two-sided - which is precisely when not to trade.
    """
    candidates = archetypes or list(Archetype)
    results: list[ConvictionResult] = []
    for archetype in candidates:
        for direction in (Direction.LONG, Direction.SHORT):
            results.append(
                score(
                    readings,
                    archetype=archetype,
                    direction=direction,
                    regime=regime,
                    temporal_gain=temporal_gain,
                )
            )
    results.sort(key=lambda r: r.conviction, reverse=True)
    return results


__all__ = ["Contribution", "ConvictionResult", "score", "best_hypothesis"]
