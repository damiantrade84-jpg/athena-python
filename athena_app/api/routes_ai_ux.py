"""athena_app/api/routes_ai_ux.py — Phase 6 UX surface routes.

Four dedicated, config-gated, default-off, advisory-only, fail-closed routes
that expose the Phase 6 backend primitives to the frontend:

- POST /api/ai/evidence-refs        -> ai_ux_surfaces.build_evidence_refs
- GET  /api/ai/calibration          -> ai_ux_surfaces.confidence_calibration
- POST /api/ai/disagreement         -> ai_ux_surfaces.detect_ai_disagreement
- POST /api/ai/whatif               -> ai_ux_surfaces.whatif_replay

Safety:
- Advisory-only. No execution, no order approval, no gate override.
- Every response carries `do_not_auto_apply: true` where applicable.
- Fail-closed: on any error, returns 200 with an empty/disabled payload
  (never 500) so the frontend can always render a graceful no-op.
- The what-if route rejects locked safety-field overrides server-side
  (delegated to ai_ux_surfaces.whatif_replay).
- No mutation of config.yaml, scoring, gates, SL/TP, or RR.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import jsonify, request

from ai_ux_surfaces import (
    build_evidence_refs,
    confidence_calibration,
    detect_ai_disagreement,
    whatif_replay,
)
from config import CONFIG

log = logging.getLogger("athena.routes_ai_ux")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _gate(key: str) -> bool:
    try:
        return bool(CONFIG.get(key, False))
    except Exception:
        return False


def _build_calibration_samples(surface: str | None, lookback_days: int) -> list[dict[str, Any]]:
    """Best-effort sample assembly for confidence calibration.

    Pulls raw AI review records via ai_outcome_linker and normalizes each
    into {confidence, outcome}. Fail-closed: any error returns [].
    """
    try:
        from ai_outcome_linker import load_ai_review_samples, normalize_ai_decision

        raw = load_ai_review_samples(lookback_days=lookback_days)
        samples: list[dict[str, Any]] = []
        for rec in raw:
            if not isinstance(rec, dict):
                continue
            if surface:
                rec_surface = str(rec.get("surface") or rec.get("engine") or "").upper()
                if rec_surface and rec_surface != surface.upper():
                    continue
            norm = normalize_ai_decision(rec)
            conf = norm.get("confidence")
            if conf is None:
                conf = rec.get("confidence")
            try:
                conf_f = float(conf)
            except Exception:
                continue
            outcome = str(rec.get("outcome") or rec.get("result") or "").lower()
            if outcome not in {"win", "loss", "neutral"}:
                # Outcome unknown -> skip for calibration accuracy
                continue
            samples.append({"confidence": conf_f, "outcome": outcome})
        return samples
    except Exception as exc:
        log.warning("[AI_UX_ROUTE] calibration sample build failed: %s", exc)
        return []


def register_ai_ux_routes(app, runtime) -> None:
    """Register Phase 6 UX surface routes on the Flask app."""

    @app.post("/api/ai/evidence-refs")
    def api_ai_evidence_refs():
        """Build citations/grounding tags for a set of claims.

        Body: {claims: [{claim_id, text, source_field?}, ...], sources: {...}}
        Gate: AI_CITATIONS_ENABLED. When off -> {enabled: false, evidence_refs: []}.
        """
        try:
            payload = request.get_json(silent=True) or {}
            claims = _as_list(payload.get("claims"))
            sources = _as_dict(payload.get("sources"))
            enabled = _gate("AI_CITATIONS_ENABLED")
            refs = build_evidence_refs(claims, sources) if enabled else []
            return jsonify(
                {
                    "enabled": enabled,
                    "evidence_refs": refs,
                    "do_not_auto_apply": True,
                }
            )
        except Exception as exc:
            log.warning("[AI_UX_ROUTE] evidence-refs failed: %s", exc)
            return jsonify({"enabled": False, "evidence_refs": [], "do_not_auto_apply": True, "error": "fail_closed"}), 200

    @app.get("/api/ai/calibration")
    def api_ai_calibration():
        """Binned confidence vs realized accuracy.

        Query: ?surface=marcus&lookback_days=30
        Gate: AI_CONFIDENCE_CALIBRATION_ENABLED.
        """
        try:
            surface = (request.args.get("surface") or "").strip() or None
            try:
                lookback_days = max(1, min(int(request.args.get("lookback_days", 30)), 365))
            except Exception:
                lookback_days = 30
            samples = _build_calibration_samples(surface, lookback_days) if _gate("AI_CONFIDENCE_CALIBRATION_ENABLED") else []
            out = confidence_calibration(samples)
            out["surface"] = surface
            out["lookback_days"] = lookback_days
            return jsonify(out)
        except Exception as exc:
            log.warning("[AI_UX_ROUTE] calibration failed: %s", exc)
            return jsonify(
                {"schema_version": "confidence_calibration.v1", "enabled": False, "bins": [], "do_not_auto_apply": True, "error": "fail_closed"}
            ), 200

    @app.post("/api/ai/disagreement")
    def api_ai_disagreement():
        """Detect divergence across AI surfaces.

        Body: {verdicts: {marcus: {decision, confidence}, vision: {...}, ...}}
        Gate: AI_DISAGREEMENT_VIZ_ENABLED.
        """
        try:
            payload = request.get_json(silent=True) or {}
            verdicts = _as_dict(payload.get("verdicts"))
            out = detect_ai_disagreement(verdicts)
            return jsonify(out)
        except Exception as exc:
            log.warning("[AI_UX_ROUTE] disagreement failed: %s", exc)
            return jsonify(
                {"schema_version": "ai_disagreement.v1", "enabled": False, "divergent": False, "advisory_only": True, "error": "fail_closed"}
            ), 200

    @app.post("/api/ai/whatif")
    def api_ai_whatif():
        """Build a hypothetical context by applying read-only overrides.

        Body: {base_context: {...}, overrides: {...}}
        Gate: AI_WHATIF_ENABLED. Locked safety-field prefixes are rejected
        server-side inside whatif_replay (never mutates real state, never
        executes).
        """
        try:
            payload = request.get_json(silent=True) or {}
            base_context = _as_dict(payload.get("base_context"))
            overrides = _as_dict(payload.get("overrides"))
            out = whatif_replay(base_context, overrides)
            return jsonify(out)
        except Exception as exc:
            log.warning("[AI_UX_ROUTE] whatif failed: %s", exc)
            return jsonify(
                {
                    "schema_version": "whatif_replay.v1",
                    "enabled": False,
                    "hypothetical_context": None,
                    "applied_overrides": [],
                    "rejected_overrides": [],
                    "advisory_only": True,
                    "do_not_auto_apply": True,
                    "error": "fail_closed",
                }
            ), 200
