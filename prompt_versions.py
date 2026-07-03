"""prompt_versions.py — registry for AI prompt / calibration versions (Audit ATH-003)."""

from __future__ import annotations

import logging

log = logging.getLogger("sentinel.prompt_versions")

# Bump the relevant entry whenever prompt text changes in code or modules.
PROMPT_VERSIONS: dict[str, str] = {
    "marcus_expert": "EXPERT_PROMPT_v6",
    "engine_b_ai": "ENGINE_B_AI_v2",
    "vision_system": "VISION_SYSTEM_v1",
    "vision_modes": "VISION_MODES_v1",
    "debate_bull": "DEBATE_BULL_v1",
    "debate_bear": "DEBATE_BEAR_v1",
    "debate_judge": "DEBATE_JUDGE_v1",
    "decay_ai": "DECAY_AI_v1",
    "calibration_context": "CALIBRATION_CONTEXT_v1",
    "chart_vision_audit": "VISION_v4",
}


def get_prompt_version(surface: str) -> str:
    ver = PROMPT_VERSIONS.get(surface)
    if not ver:
        log.warning("[PROMPT_VERSION] Unknown surface=%r — Audit ATH-003.", surface)
        return "UNKNOWN"
    return ver


def hash_prompt(prompt_text: str) -> str:
    import hashlib

    return hashlib.sha256((prompt_text or "").encode("utf-8")).hexdigest()
