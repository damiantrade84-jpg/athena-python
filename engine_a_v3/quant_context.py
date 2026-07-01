"""Engine A quant scorer — relative-volume context assembler.

Maps the app's forex/crypto volume-ratio feeds to the `volume_ratio` input
`engine_a_v3.quant_scorer._volume_component` consumes.

Subsystem signals (carry/sentiment/microstructure/intermarket) were previously
assembled here too, but `quant_scorer.score_pair` never wired them into
direction/confluence (no caller reads anything but `volume_ratio` from this
context dict), so that dead plumbing was removed (2026-06-30). See
`athena_research/engine_a_ablation/shadow_scorer.py` for the research-only
ablation studying whether/how to add subsystem signals with point-in-time-safe
data and walk-forward evidence before any live wiring.
"""

from __future__ import annotations

from typing import Any


def build_quant_context(*, volume_ratio: Any = None) -> dict[str, Any]:
    """Assemble the quant scorer's context. Only populated keys are returned;
    absent keys are treated as neutral by the scorer."""
    context: dict[str, Any] = {}
    if volume_ratio is not None:
        try:
            context["volume_ratio"] = float(volume_ratio)
        except (TypeError, ValueError):
            pass
    return context
