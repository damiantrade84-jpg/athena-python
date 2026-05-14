"""AI Evaluation — Flask API Routes.

Read-only evaluation metrics across all AI surfaces.

Safety:
  - No trade execution.
  - No threshold changes.
  - Missing data returns warnings, not crashes.
  - All recommendations have do_not_auto_apply=True.
"""

from __future__ import annotations

import logging

from flask import jsonify, request

from ai_evaluation import (
    compute_surface_metrics,
    evaluate_debate_gate,
    evaluate_strategist_accuracy,
    evaluate_vision_accuracy,
    generate_ai_evaluation_report,
)
from ai_outcome_linker import link_samples_to_outcomes, load_ai_review_samples
from ai_weekly_report import generate_weekly_ai_report

log = logging.getLogger(__name__)


def register_ai_evaluation_routes(app) -> None:
    """Register all /api/ai/evaluation/* routes."""

    def _lookback() -> int:
        try:
            return max(1, int(request.args.get("lookback_days", 30)))
        except (TypeError, ValueError):
            return 30

    def _build_report(lb: int) -> dict:
        samples = load_ai_review_samples(lookback_days=lb)
        linked = link_samples_to_outcomes(samples)
        metrics = compute_surface_metrics(linked)
        return {
            "lookback_days": lb,
            "samples_loaded": len(samples),
            "samples_linked": len(linked),
            "surface_metrics": metrics,
        }

    # ── GET /api/ai/evaluation/summary ────────────────────────────────────────
    @app.route("/api/ai/evaluation/summary", methods=["GET"])
    def api_ai_eval_summary():
        lb = _lookback()
        report = generate_ai_evaluation_report(lookback_days=lb)
        return jsonify(report)

    # ── GET /api/ai/evaluation/surfaces ───────────────────────────────────────
    @app.route("/api/ai/evaluation/surfaces", methods=["GET"])
    def api_ai_eval_surfaces():
        lb = _lookback()
        data = _build_report(lb)
        return jsonify(data)

    # ── GET /api/ai/evaluation/vision ─────────────────────────────────────────
    @app.route("/api/ai/evaluation/vision", methods=["GET"])
    def api_ai_eval_vision():
        lb = _lookback()
        samples = load_ai_review_samples(lookback_days=lb)
        linked = link_samples_to_outcomes(samples)
        result = evaluate_vision_accuracy(linked)
        return jsonify(result)

    # ── GET /api/ai/evaluation/debate ─────────────────────────────────────────
    @app.route("/api/ai/evaluation/debate", methods=["GET"])
    def api_ai_eval_debate():
        lb = _lookback()
        samples = load_ai_review_samples(lookback_days=lb)
        linked = link_samples_to_outcomes(samples)
        result = evaluate_debate_gate(linked)
        return jsonify(result)

    # ── GET /api/ai/evaluation/strategist ─────────────────────────────────────
    @app.route("/api/ai/evaluation/strategist", methods=["GET"])
    def api_ai_eval_strategist():
        lb = _lookback()
        samples = load_ai_review_samples(lookback_days=lb)
        linked = link_samples_to_outcomes(samples)
        result = evaluate_strategist_accuracy(linked)
        return jsonify(result)

    # ── GET /api/ai/evaluation/weekly ─────────────────────────────────────────
    @app.route("/api/ai/evaluation/weekly", methods=["GET"])
    def api_ai_eval_weekly():
        report = generate_weekly_ai_report()
        return jsonify(report)

    # ── POST /api/ai/evaluation/generate-weekly ───────────────────────────────
    @app.route("/api/ai/evaluation/generate-weekly", methods=["POST"])
    def api_ai_eval_generate_weekly():
        report = generate_weekly_ai_report()
        return jsonify(report)

    log.info("[ai_evaluation_routes] AI Evaluation routes registered")
