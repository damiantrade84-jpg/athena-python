"""ai_ux_surfaces.py — Phase 6 UX + surfacing backend primitives.

Consolidates four advisory, config-gated, default-off, fail-closed
capabilities that the frontend consumes:

1. `build_evidence_refs(claims, sources)` — citations / grounding tags for
   AI claims. Returns an `evidence_refs` list to attach additively to any AI
   review payload.

2. `confidence_calibration(samples)` — binned confidence vs realized
   accuracy from Phase 3 eval data. Returns a calibration table
   (bin -> {count, avg_confidence, realized_accuracy, gap}).

3. `detect_ai_disagreement(verdicts)` — detect when Marcus vs Vision vs
   Debate (or any set of AI surfaces) diverge on decision or confidence.
   Returns a disagreement summary for the UI to visualize.

4. `whatif_replay(base_context, overrides)` — produce a hypothetical context
   by applying read-only overrides (never mutates the real context, never
   executes). The caller may feed the hypothetical context back into an AI
   surface for a re-run. This module does NOT call the LLM itself; it only
   builds the hypothetical context and a replay descriptor.

Safety:
- Advisory-only. Never approves, blocks, executes, or mutates real state.
- All functions default-off via config; fail-closed.
- Never raises.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from config import CONFIG

log = logging.getLogger("athena.ai_ux_surfaces")


def _cfg_bool(key: str, default: bool = False) -> bool:
    try:
        return bool(CONFIG.get(key, default))
    except Exception:
        return default


def _cfg_float(key: str, default: float) -> float:
    try:
        return float(CONFIG.get(key, default))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 1. Evidence refs / citations
# ---------------------------------------------------------------------------


def build_evidence_refs(
    claims: list[dict[str, Any]] | None,
    sources: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build additive `evidence_refs` for an AI review payload.

    Each claim is {claim_id, text, source_field?}. Each ref links a claim to
    a grounded source field. Ungrounded claims get `ungrounded: true` so the
    UI can flag them. Config-gated by AI_CITATIONS_ENABLED (default false);
    when disabled returns [].
    """
    if not _cfg_bool("AI_CITATIONS_ENABLED", False):
        return []
    if not isinstance(claims, list):
        return []
    src = sources if isinstance(sources, dict) else {}
    refs: list[dict[str, Any]] = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        claim_id = c.get("claim_id") or c.get("id")
        text = str(c.get("text") or c.get("claim") or "")[:500]
        field = c.get("source_field")
        if field and isinstance(field, str) and field in src:
            refs.append(
                {
                    "claim_id": claim_id,
                    "text": text,
                    "source_field": field,
                    "source_value": src[field],
                    "ungrounded": False,
                }
            )
        else:
            refs.append({"claim_id": claim_id, "text": text, "ungrounded": True})
    return refs


# ---------------------------------------------------------------------------
# 2. Confidence calibration
# ---------------------------------------------------------------------------


_CALIBRATION_BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]


def confidence_calibration(samples: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Binned confidence vs realized accuracy.

    Each sample is {confidence: float, outcome: 'win'|'loss'|'neutral'}.
    Returns {schema_version, bins: [{bin, count, avg_confidence,
    realized_accuracy, gap}], do_not_auto_apply}.
    """
    if not _cfg_bool("AI_CONFIDENCE_CALIBRATION_ENABLED", False):
        return {"schema_version": "confidence_calibration.v1", "enabled": False, "bins": [], "do_not_auto_apply": True}
    if not isinstance(samples, list):
        return {"schema_version": "confidence_calibration.v1", "enabled": True, "bins": [], "do_not_auto_apply": True}
    bins: list[dict[str, Any]] = []
    for lo, hi in _CALIBRATION_BINS:
        in_bin = []
        for s in samples:
            if not isinstance(s, dict):
                continue
            try:
                conf = float(s.get("confidence"))
            except Exception:
                continue
            if lo <= conf < hi:
                in_bin.append(s)
        count = len(in_bin)
        if count == 0:
            bins.append({"bin": f"{lo:.1f}-{hi:.1f}", "count": 0, "avg_confidence": None, "realized_accuracy": None, "gap": None})
            continue
        avg_conf = sum(float(s["confidence"]) for s in in_bin if isinstance(s.get("confidence"), (int, float))) / count
        wins = sum(1 for s in in_bin if str(s.get("outcome") or "").lower() == "win")
        acc = wins / count
        bins.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "count": count,
                "avg_confidence": round(avg_conf, 4),
                "realized_accuracy": round(acc, 4),
                "gap": round(avg_conf - acc, 4),
            }
        )
    return {"schema_version": "confidence_calibration.v1", "enabled": True, "bins": bins, "do_not_auto_apply": True}


# ---------------------------------------------------------------------------
# 3. AI disagreement detection
# ---------------------------------------------------------------------------


def _norm_decision(v: Any) -> str | None:
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    return None


def detect_ai_disagreement(verdicts: dict[str, Any] | None) -> dict[str, Any]:
    """Detect divergence across AI surfaces (e.g. marcus vs vision vs debate).

    `verdicts` is {surface_name: {decision, confidence, ...}}.
    Returns {schema_version, surfaces, decisions, decision_agreement:bool,
    confidence_spread, divergent:bool, summary, advisory_only}.
    """
    if not _cfg_bool("AI_DISAGREEMENT_VIZ_ENABLED", False):
        return {"schema_version": "ai_disagreement.v1", "enabled": False, "divergent": False, "advisory_only": True}
    if not isinstance(verdicts, dict) or not verdicts:
        return {"schema_version": "ai_disagreement.v1", "enabled": True, "divergent": False, "advisory_only": True}
    decisions: dict[str, str | None] = {}
    confidences: dict[str, float | None] = {}
    for surface, v in verdicts.items():
        if not isinstance(v, dict):
            continue
        decisions[surface] = _norm_decision(v.get("decision") or v.get("verdict"))
        try:
            confidences[surface] = float(v.get("confidence"))
        except Exception:
            confidences[surface] = None
    present = [d for d in decisions.values() if d is not None]
    # Long vs short is a disagreement; neutral is compatible.
    longs = sum(1 for d in present if d == "long")
    shorts = sum(1 for d in present if d == "short")
    decision_agreement = not (longs > 0 and shorts > 0)
    conf_vals = [c for c in confidences.values() if c is not None]
    spread = round(max(conf_vals) - min(conf_vals), 4) if len(conf_vals) >= 2 else 0.0
    divergent = (not decision_agreement) or (spread > _cfg_float("AI_DISAGREEMENT_CONF_SPREAD", 0.25))
    surfaces = list(verdicts.keys())
    return {
        "schema_version": "ai_disagreement.v1",
        "enabled": True,
        "surfaces": surfaces,
        "decisions": decisions,
        "confidences": confidences,
        "decision_agreement": decision_agreement,
        "confidence_spread": spread,
        "divergent": divergent,
        "summary": (
            "AI surfaces agree." if not divergent else "AI surfaces diverge — review before acting."
        ),
        "advisory_only": True,
    }


# ---------------------------------------------------------------------------
# 4. What-if replay (read-only, no execution)
# ---------------------------------------------------------------------------


def whatif_replay(
    base_context: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a hypothetical context by applying overrides to a copy of base.

    Config-gated by AI_WHATIF_ENABLED (default false). NEVER mutates the real
    base_context (deep-copied). NEVER executes. The returned hypothetical
    context is meant to be fed back into an AI surface for an advisory re-run.

    Returns {schema_version, hypothetical_context, applied_overrides,
    rejected_overrides, advisory_only, do_not_auto_apply}.
    """
    if not _cfg_bool("AI_WHATIF_ENABLED", False):
        return {
            "schema_version": "whatif_replay.v1",
            "enabled": False,
            "hypothetical_context": None,
            "applied_overrides": [],
            "rejected_overrides": [],
            "advisory_only": True,
            "do_not_auto_apply": True,
        }
    base = copy.deepcopy(base_context) if isinstance(base_context, dict) else {}
    ovr = overrides if isinstance(overrides, dict) else {}
    # Reject any override key that looks like it touches locked safety fields.
    LOCKED_PREFIXES = ("risk_", "execution_", "guardian_", "kill_switch", "broker_", "sl_", "tp_", "rr_")
    LOCKED_KEYS = {"config", "config_path", "threshold", "weight"}
    applied: list[str] = []
    rejected: list[str] = []
    for k, v in ovr.items():
        kl = str(k).lower()
        if any(kl.startswith(p) for p in LOCKED_PREFIXES) or kl in LOCKED_KEYS:
            rejected.append(k)
            continue
        base[k] = v
        applied.append(k)
    return {
        "schema_version": "whatif_replay.v1",
        "enabled": True,
        "hypothetical_context": base,
        "applied_overrides": applied,
        "rejected_overrides": rejected,
        "advisory_only": True,
        "do_not_auto_apply": True,
    }
