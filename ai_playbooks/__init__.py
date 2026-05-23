"""Versioned deterministic trade playbooks for Athena AI reviews."""

from __future__ import annotations

import json
from typing import Any

from ai_playbooks.engine_a_playbook import get_engine_a_playbook
from ai_playbooks.engine_b_playbook import get_engine_b_playbook
from ai_playbooks.engine_d_scalp_playbook import get_engine_d_scalp_playbook

_PLAYBOOK_GETTERS = {
    "A": get_engine_a_playbook,
    "B": get_engine_b_playbook,
    "D": get_engine_d_scalp_playbook,
}


def get_playbook(engine: str) -> dict[str, Any]:
    key = str(engine or "").upper().strip()
    getter = _PLAYBOOK_GETTERS.get(key)
    if getter is None:
        raise KeyError(f"unknown playbook engine: {engine}")
    return getter()


def render_playbook_prompt_block(
    playbooks: list[dict[str, Any]],
    *,
    compact: bool = True,
) -> str:
    """Render playbooks as compact JSON for prompt injection."""
    if not playbooks:
        return ""

    if compact:
        slim: list[dict[str, Any]] = []
        for pb in playbooks:
            entry: dict[str, Any] = {
                "schemaVersion": pb.get("schemaVersion"),
                "engine": pb.get("engine"),
                "name": pb.get("name"),
                "reviewOrder": pb.get("reviewOrder"),
                "principles": pb.get("principles", [])[:8],
                "entryModels": pb.get("entryModels", []),
                "invalidations": pb.get("invalidations", [])[:5],
                "mustRejectIf": pb.get("mustRejectIf", [])[:8],
                "requiredOutputFields": pb.get("requiredOutputFields", []),
            }
            for extra in (
                "marketStates",
                "locationChecklist",
                "aggressionChecklist",
                "decisions",
                "sessionModelSwitch",
                "effortVsResultDecisionTable",
                "trappedTraderRules",
                "pocTargetMagnetRules",
                "casinoTimeDegradationRules",
                "structuralStopGeometry",
                "tradeManagementRules",
                "extendedOutputFields",
            ):
                if extra in pb:
                    entry[extra] = pb[extra]
            slim.append(entry)
        payload = slim
    else:
        payload = playbooks

    return (
        "== ATHENA TRADE PLAYBOOKS (follow strictly; advisory only) ==\n"
        + json.dumps(payload, indent=2, default=str)
        + "\n"
    )


__all__ = [
    "get_playbook",
    "get_engine_a_playbook",
    "get_engine_b_playbook",
    "get_engine_d_scalp_playbook",
    "render_playbook_prompt_block",
]