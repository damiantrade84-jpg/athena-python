"""Research Agent — Flask API Routes.

Safety: All routes are research-only. No live execution. No threshold/config writes.
Stable JSON errors. Missing files return warnings, not crashes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from flask import jsonify, request

from athena_research.research_agent import (
    compare_research_runs,
    load_latest_research_results,
    produce_research_recommendations,
    propose_next_research_plan,
    run_approved_research_plan,
    summarize_research_results,
    validate_research_plan,
)
from athena_research.research_agent_store import (
    get_plan,
    get_plans,
    get_run,
    get_runs,
    persist_comparison,
    persist_plan,
    persist_recommendations,
    persist_validation,
)

log = logging.getLogger(__name__)


def register_research_agent_routes(app) -> None:
    """Register all /api/ai/research-agent/* routes on the Flask app."""

    _validate_runner_available()

    # ── GET /api/ai/research-agent/latest ─────────────────────────────────────
    @app.route("/api/ai/research-agent/latest", methods=["GET"])
    def api_research_agent_latest():
        """Return latest research summaries."""
        try:
            limit = int(request.args.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        results = load_latest_research_results(limit=limit)
        return jsonify({"results": results})

    # ── POST /api/ai/research-agent/summarize ─────────────────────────────────
    @app.route("/api/ai/research-agent/summarize", methods=["POST"])
    def api_research_agent_summarize():
        """Summarize research results for a specific run or latest."""
        body = request.get_json(silent=True) or {}
        run_id = body.get("run_id") or None
        summary = summarize_research_results(run_id)
        return jsonify(summary)

    # ── POST /api/ai/research-agent/propose ───────────────────────────────────
    @app.route("/api/ai/research-agent/propose", methods=["POST"])
    def api_research_agent_propose():
        """Propose a research plan based on latest summary."""
        body = request.get_json(silent=True) or {}
        user_goal = str(body.get("user_goal", "")).strip() or None
        scope = body.get("scope") if isinstance(body.get("scope"), dict) else {}

        run_id = scope.get("run_id") or body.get("run_id")
        summary = summarize_research_results(run_id)

        plan = propose_next_research_plan(summary, user_goal=user_goal)
        return jsonify(plan)

    # ── POST /api/ai/research-agent/validate-plan ─────────────────────────────
    @app.route("/api/ai/research-agent/validate-plan", methods=["POST"])
    def api_research_agent_validate_plan():
        """Validate a research plan for safety and consistency."""
        body = request.get_json(silent=True) or {}
        plan = body.get("plan", body)
        if not isinstance(plan, dict):
            return jsonify({"valid": False, "issues": ["Plan must be a JSON object."]}), 400

        validation = validate_research_plan(plan)

        # Persist
        try:
            persist_plan(
                plan_id=plan.get("plan_id", "unknown"),
                user_goal=plan.get("objective", ""),
                plan_json=plan,
                validation_json=validation,
            )
        except Exception as exc:
            log.warning("[ResearchAgent] Failed to persist validation: %s", exc)

        return jsonify(validation)

    # ── POST /api/ai/research-agent/run-approved-plan ─────────────────────────
    @app.route("/api/ai/research-agent/run-approved-plan", methods=["POST"])
    def api_research_agent_run_approved():
        """Execute an approved research plan."""
        body = request.get_json(silent=True) or {}
        plan = body.get("plan", body)
        approved = body.get("approved", plan.get("approved", False))

        if not approved:
            return jsonify({
                "status": "rejected",
                "reason": "Plan not approved. Set 'approved': true to execute.",
            }), 400

        if not isinstance(plan, dict):
            return jsonify({"status": "error", "reason": "Plan must be a JSON object."}), 400

        plan["approved"] = True
        result = run_approved_research_plan(plan)
        return jsonify(result)

    # ── GET /api/ai/research-agent/runs/<run_id> ──────────────────────────────
    @app.route("/api/ai/research-agent/runs/<run_id>", methods=["GET"])
    def api_research_agent_run_detail(run_id: str):
        """Return a result summary for a specific run."""
        summary = summarize_research_results(run_id)
        if summary.get("warnings"):
            # Check if it's a "not found" warning
            if "No research run directory found" in str(summary.get("warnings", [])):
                return jsonify(summary)
        return jsonify(summary)

    # ── POST /api/ai/research-agent/compare ────────────────────────────────────
    @app.route("/api/ai/research-agent/compare", methods=["POST"])
    def api_research_agent_compare():
        """Compare two research runs."""
        body = request.get_json(silent=True) or {}
        baseline = str(body.get("baseline_run_id", "")).strip()
        candidate = str(body.get("candidate_run_id", "")).strip()

        if not baseline or not candidate:
            return jsonify({
                "error": "Both baseline_run_id and candidate_run_id are required.",
                "missing_fields": [k for k, v in [("baseline_run_id", baseline), ("candidate_run_id", candidate)] if not v],
            }), 400

        comparison = compare_research_runs(baseline, candidate)

        # Persist
        try:
            persist_comparison(baseline, candidate, comparison)
        except Exception as exc:
            log.warning("[ResearchAgent] Failed to persist comparison: %s", exc)

        return jsonify(comparison)

    # ── POST /api/ai/research-agent/recommendations ────────────────────────────
    @app.route("/api/ai/research-agent/recommendations", methods=["POST"])
    def api_research_agent_recommendations():
        """Generate recommendations from a summary or comparison."""
        body = request.get_json(silent=True) or {}

        # If run_id provided, summarize first
        run_id = body.get("run_id")
        if run_id:
            summary = summarize_research_results(run_id)
        else:
            comparison = body.get("comparison")
            if isinstance(comparison, dict):
                summary = comparison
            else:
                summary = body.get("summary") or summarize_research_results()

        recommendations = produce_research_recommendations(summary)

        # Persist
        try:
            persist_recommendations(
                plan_id=body.get("plan_id", summary.get("run_id", "latest")),
                recommendation_json=recommendations,
            )
        except Exception as exc:
            log.warning("[ResearchAgent] Failed to persist recommendations: %s", exc)

        return jsonify({"recommendations": recommendations})

    # ── GET /api/ai/research-agent/plans ──────────────────────────────────────
    @app.route("/api/ai/research-agent/plans", methods=["GET"])
    def api_research_agent_plans():
        """Return stored plans."""
        try:
            limit = int(request.args.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        plans = get_plans(limit=limit)
        return jsonify({"plans": plans})

    # ── GET /api/ai/research-agent/plan/<plan_id> ─────────────────────────────
    @app.route("/api/ai/research-agent/plan/<plan_id>", methods=["GET"])
    def api_research_agent_plan(plan_id: str):
        """Return a specific plan."""
        plan = get_plan(plan_id)
        if plan is None:
            return jsonify({"error": "Plan not found", "plan_id": plan_id}), 404
        return jsonify(plan)

    # ── GET /api/ai/research-agent/runs ───────────────────────────────────────
    @app.route("/api/ai/research-agent/runs", methods=["GET"])
    def api_research_agent_run_list():
        """Return stored runs."""
        try:
            limit = int(request.args.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        runs = get_runs(limit=limit)
        return jsonify({"runs": runs})

    log.info("[research_agent_routes] Research Agent routes registered")


RUNNER_AVAILABLE: bool | None = None


def _validate_runner_available() -> None:
    """Check if the safe research runner is available at import time."""
    global RUNNER_AVAILABLE
    if RUNNER_AVAILABLE is not None:
        return
    try:
        from athena_research.run_manager import run_research  # noqa: F401
        RUNNER_AVAILABLE = True
    except ImportError:
        RUNNER_AVAILABLE = False
