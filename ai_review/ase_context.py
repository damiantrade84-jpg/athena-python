"""ASE signal text context for chart AI review (advisory only)."""

from __future__ import annotations

import json
from typing import Any


def build_ase_prompt_context(ase_signal: dict[str, Any] | None) -> dict[str, Any]:
    if not ase_signal or not isinstance(ase_signal, dict):
        return {"available": False, "reason": "no_ase_signal"}

    return {
        "available": True,
        "engineVersion": ase_signal.get("engineVersion"),
        "modelFamily": ase_signal.get("modelFamily"),
        "modelVersion": ase_signal.get("modelVersion"),
        "horizon": ase_signal.get("horizon"),
        "decisionStatus": ase_signal.get("decisionStatus"),
        "direction": ase_signal.get("direction"),
        "expectedNetR": ase_signal.get("expectedNetR"),
        "expectedNetBps": ase_signal.get("expectedNetBps"),
        "probabilityPositive": ase_signal.get("probabilityPositive"),
        "decisionMargin": ase_signal.get("decisionMargin"),
        "signalStrength": ase_signal.get("signalStrength"),
        "returnQ": ase_signal.get("returnQ"),
        "maeQ": ase_signal.get("maeQ"),
        "mfeQ": ase_signal.get("mfeQ"),
        "holdQ": ase_signal.get("holdQ"),
        "entryReference": ase_signal.get("entryReference"),
        "entryZone": ase_signal.get("entryZone"),
        "sl": ase_signal.get("sl"),
        "tp1": ase_signal.get("tp1"),
        "tp2": ase_signal.get("tp2"),
        "maxHoldBars": ase_signal.get("maxHoldBars"),
        "primarySignals": ase_signal.get("primarySignals"),
        "predictionDiagnostics": ase_signal.get("predictionDiagnostics"),
        "dataQuality": ase_signal.get("dataQuality"),
        "modelHealth": ase_signal.get("modelHealth"),
    }


def render_ase_prompt_block(ase_signal: dict[str, Any] | None) -> str:
    ctx = build_ase_prompt_context(ase_signal)
    if not ctx.get("available"):
        return "== ASE SIGNAL (server-trusted) ==\nunavailable\n"
    return (
        "== ASE SIGNAL (server-trusted economics — advisory only, no execution authority) ==\n"
        + json.dumps({"aseContext": ctx}, default=str, indent=2)
        + "\n"
    )
