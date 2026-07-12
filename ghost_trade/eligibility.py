"""Visibility and execution eligibility remain separate in Ghost Trade."""

from __future__ import annotations

from .config import GhostConfig
from .models import ConfirmedScoreResult, GhostMode


def execution_reasons(score: ConfirmedScoreResult, config: GhostConfig) -> tuple[str, ...]:
    reasons = list(score.reasons)
    if config.mode is GhostMode.SHADOW:
        reasons.append("shadow_mode")
    if score.confirmed_score < config.minimum_confirmed_score:
        reasons.append("confirmed_score_below_threshold")
    if score.entry_quality.score < config.minimum_entry_quality:
        reasons.append("entry_quality_below_threshold")
    if score.geometry.geometry is None:
        reasons.append("invalid_structural_geometry")
    elif score.geometry.geometry.raw_rr < config.minimum_raw_rr:
        reasons.append("raw_rr_below_threshold")
    return tuple(dict.fromkeys(reasons))
