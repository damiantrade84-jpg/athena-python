"""Tests for AI Evaluation (Phase 5).

Covers: contracts, outcome linker, metrics engine, weekly report, API routes.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

from ai_evaluation_contracts import (
    AIEvaluationReport,
    AIEvaluationSample,
    AIRecommendationEvaluation,
    AISurfaceMetrics,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1: Contract tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvaluationContracts:
    def test_ai_evaluation_sample_defaults(self):
        s = AIEvaluationSample()
        d = s.model_dump()
        assert d["sample_valid"] is False
        assert d["ai_surface"] == "MARCUS"
        assert d["ai_blocked"] is False

    def test_ai_surface_metrics_defaults(self):
        m = AISurfaceMetrics()
        d = m.model_dump()
        assert d["sample_count"] == 0
        assert d["valid_outcome_count"] == 0

    def test_ai_evaluation_report_do_not_auto_apply(self):
        r = AIEvaluationReport()
        assert r.do_not_auto_apply is True

    def test_ai_evaluation_report_cannot_set_auto_apply(self):
        r = AIEvaluationReport(do_not_auto_apply=False)
        assert r.do_not_auto_apply is True

    def test_ai_recommendation_evaluation_defaults(self):
        rec = AIRecommendationEvaluation()
        assert rec.accepted_by_user is False
        assert rec.improved is None

    def test_contracts_serialize_to_json(self):
        r = AIEvaluationReport(report_id="test-1", overall_summary="Test report")
        j = r.model_dump_json()
        assert '"do_not_auto_apply":true' in j.replace(" ", "")


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2: Outcome linker tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutcomeLinker:
    def test_normalize_ai_decision_confirm(self):
        from ai_outcome_linker import normalize_ai_decision
        result = normalize_ai_decision({"ai_review_state": "CONFIRM", "review_type": "marcus_reid"})
        assert result["ai_decision"] == "POSITIVE"
        assert result["ai_surface"] == "MARCUS"
        assert result["ai_blocked"] is False

    def test_normalize_ai_decision_block(self):
        from ai_outcome_linker import normalize_ai_decision
        result = normalize_ai_decision({"ai_review_state": "REJECT", "review_type": "signal_debate"})
        assert result["ai_decision"] == "NEGATIVE"
        assert result["ai_surface"] == "DEBATE"
        assert result["ai_blocked"] is True

    def test_normalize_vision_surface(self):
        from ai_outcome_linker import normalize_ai_decision
        result = normalize_ai_decision({"review_type": "chart_vision"})
        assert result["ai_surface"] == "VISION"

    def test_normalize_strategist_surface(self):
        from ai_outcome_linker import normalize_ai_decision
        result = normalize_ai_decision({"review_type": "strategist"})
        assert result["ai_surface"] == "STRATEGIST"

    def test_classify_block_quality_useful_block(self):
        from ai_outcome_linker import classify_block_quality
        sample = {"ai_blocked": True, "actual_outcome": "LOSS", "sample_valid": True}
        result = classify_block_quality(sample)
        assert result["useful_block"] is True
        assert result["false_block"] is False

    def test_classify_block_quality_false_block(self):
        from ai_outcome_linker import classify_block_quality
        sample = {"ai_blocked": True, "actual_outcome": "WIN", "sample_valid": True}
        result = classify_block_quality(sample)
        assert result["false_block"] is True
        assert result["useful_block"] is False

    def test_classify_block_quality_harmful_allow(self):
        from ai_outcome_linker import classify_block_quality
        sample = {"ai_blocked": False, "ai_decision": "POSITIVE", "actual_outcome": "LOSS", "sample_valid": True}
        result = classify_block_quality(sample)
        assert result["harmful_allow"] is True

    def test_classify_block_quality_missing_outcome(self):
        from ai_outcome_linker import classify_block_quality
        sample = {"ai_blocked": True, "actual_outcome": None, "sample_valid": False}
        result = classify_block_quality(sample)
        assert result["block_type"] == "neutral"

    def test_classify_block_quality_neutral_decision(self):
        from ai_outcome_linker import classify_block_quality
        sample = {"ai_blocked": False, "ai_decision": "NEUTRAL", "actual_outcome": "WIN", "sample_valid": True}
        result = classify_block_quality(sample)
        assert result["block_type"] == "neutral"

    def test_link_samples_to_outcomes_no_data(self):
        from ai_outcome_linker import link_samples_to_outcomes
        samples = [{"trace_id": "test-1", "symbol": "BTCUSDT", "timestamp": "2026-01-01T00:00:00Z"}]
        linked = link_samples_to_outcomes(samples, audit_db=":memory:", learning_db=":memory:")
        assert len(linked) == 1
        assert linked[0]["sample_valid"] is False
        assert linked[0]["missing_outcome_reason"] is not None

    def test_link_samples_to_outcomes_empty(self):
        from ai_outcome_linker import link_samples_to_outcomes
        linked = link_samples_to_outcomes([], audit_db=":memory:", learning_db=":memory:")
        assert linked == []

    def test_load_ai_review_samples_empty(self):
        from ai_outcome_linker import load_ai_review_samples
        samples = load_ai_review_samples(lookback_days=30)
        # Should not crash - may return empty list
        assert isinstance(samples, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3: Metrics engine tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricsEngine:
    def test_compute_surface_metrics_empty(self):
        from ai_evaluation import compute_surface_metrics
        assert compute_surface_metrics([]) == {}

    def test_compute_surface_metrics_with_samples(self):
        from ai_evaluation import compute_surface_metrics
        samples = [
            {"ai_surface": "MARCUS", "sample_valid": True, "actual_outcome": "WIN",
             "ai_decision": "POSITIVE", "realized_r": 2.0, "parse_success": True,
             "ai_blocked": False, "data_freshness": "FRESH"},
            {"ai_surface": "MARCUS", "sample_valid": True, "actual_outcome": "LOSS",
             "ai_decision": "POSITIVE", "realized_r": -1.0, "parse_success": True,
             "ai_blocked": False, "data_freshness": "FRESH"},
            {"ai_surface": "VISION", "sample_valid": True, "actual_outcome": "WIN",
             "ai_decision": "POSITIVE", "realized_r": 1.5, "parse_success": False,
             "ai_blocked": False, "data_freshness": "FRESH"},
        ]
        metrics = compute_surface_metrics(samples)
        assert "MARCUS" in metrics
        assert "VISION" in metrics
        m = metrics["MARCUS"]
        assert m["sample_count"] == 2
        assert m["win_rate_when_positive"] == 0.5

    def test_surface_metrics_with_blocks(self):
        from ai_evaluation import compute_surface_metrics
        samples = [
            {"ai_surface": "DEBATE", "sample_valid": True, "actual_outcome": "LOSS",
             "ai_decision": "NEGATIVE", "ai_blocked": True, "parse_success": True,
             "data_freshness": "FRESH"},
            {"ai_surface": "DEBATE", "sample_valid": True, "actual_outcome": "WIN",
             "ai_decision": "NEGATIVE", "ai_blocked": True, "parse_success": True,
             "data_freshness": "FRESH"},
        ]
        metrics = compute_surface_metrics(samples)
        d = metrics.get("DEBATE", {})
        assert d["block_count"] == 2
        assert d["useful_block_count"] == 1  # one was LOSS
        assert d["false_block_count"] == 1   # one was WIN

    def test_surface_metrics_insufficient_sample(self):
        from ai_evaluation import compute_surface_metrics
        samples = [{"ai_surface": "MARCUS", "sample_valid": False,
                     "ai_decision": "POSITIVE", "parse_success": True,
                     "data_freshness": "STALE"}]
        metrics = compute_surface_metrics(samples)
        m = metrics.get("MARCUS", {})
        assert m["sample_count"] == 1
        assert m["valid_outcome_count"] == 0

    def test_evaluate_vision_accuracy_empty(self):
        from ai_evaluation import evaluate_vision_accuracy
        result = evaluate_vision_accuracy([])
        assert "warning" in result
        assert result["sample_count"] == 0

    def test_evaluate_vision_accuracy_with_samples(self):
        from ai_evaluation import evaluate_vision_accuracy
        samples = [
            {"ai_surface": "VISION", "sample_valid": True, "actual_outcome": "WIN",
             "ai_decision": "POSITIVE", "parse_success": True,
             "vision_freshness": "FRESH", "data_freshness": "FRESH"},
            {"ai_surface": "VISION", "sample_valid": True, "actual_outcome": "LOSS",
             "ai_decision": "NEGATIVE", "parse_success": True,
             "vision_freshness": "STALE", "data_freshness": "STALE"},
        ]
        result = evaluate_vision_accuracy(samples)
        assert result["sample_count"] == 2
        assert result["confirms_win_rate"] == 1.0
        assert result["contradicts_loss_rate"] == 1.0
        assert result["stale_vision_rate"] > 0

    def test_evaluate_strategist_accuracy_empty(self):
        from ai_evaluation import evaluate_strategist_accuracy
        result = evaluate_strategist_accuracy([])
        assert "warning" in result

    def test_evaluate_strategist_accuracy_with_samples(self):
        from ai_evaluation import evaluate_strategist_accuracy
        samples = [
            {"ai_surface": "STRATEGIST", "sample_valid": True, "actual_outcome": "WIN",
             "ai_decision": "POSITIVE"},
            {"ai_surface": "STRATEGIST", "sample_valid": True, "actual_outcome": "LOSS",
             "ai_decision": "NEGATIVE"},
        ]
        result = evaluate_strategist_accuracy(samples)
        assert result["sample_count"] == 2
        assert result["concurs"] == 1
        assert result["objects"] == 1

    def test_evaluate_debate_gate_empty(self):
        from ai_evaluation import evaluate_debate_gate
        result = evaluate_debate_gate([])
        assert "warning" in result

    def test_evaluate_debate_gate_with_samples(self):
        from ai_evaluation import evaluate_debate_gate
        samples = [
            {"ai_surface": "DEBATE", "sample_valid": True, "actual_outcome": "LOSS",
             "ai_decision": "NEGATIVE", "ai_blocked": True, "parse_success": True},
            {"ai_surface": "DEBATE", "sample_valid": True, "actual_outcome": "WIN",
             "ai_decision": "POSITIVE", "ai_blocked": False, "parse_success": True},
        ]
        result = evaluate_debate_gate(samples)
        assert result["sample_count"] == 2

    def test_generate_report_has_do_not_auto_apply(self):
        from ai_evaluation import generate_ai_evaluation_report
        report = generate_ai_evaluation_report(lookback_days=90)
        assert report.get("do_not_auto_apply") is True
        assert "generated_at" in report

    def test_compute_engine_asset_breakdowns(self):
        from ai_evaluation import compute_engine_asset_breakdowns
        samples = [
            {"engine": "engine_d", "asset_type": "crypto", "sample_valid": True,
             "actual_outcome": "WIN", "ai_decision": "POSITIVE", "realized_r": 1.5,
             "timeframe": "H1", "setup_type": "scalp"},
            {"engine": "engine_d", "asset_type": "crypto", "sample_valid": True,
             "actual_outcome": "LOSS", "ai_decision": "POSITIVE", "realized_r": -1.0,
             "timeframe": "H4", "setup_type": "intraday"},
        ]
        breakdowns = compute_engine_asset_breakdowns(samples)
        assert "engine" in breakdowns
        assert "asset_type" in breakdowns


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4: Weekly report tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWeeklyReport:
    def test_weekly_report_returns_stable_shape(self):
        from ai_weekly_report import generate_weekly_ai_report
        report = generate_weekly_ai_report()
        assert "schema_version" in report
        assert report["do_not_auto_apply"] is True
        assert "headline" in report
        assert "surface_scores" in report
        assert "recommendations" in report

    def test_weekly_report_empty_data(self):
        from ai_weekly_report import generate_weekly_ai_report
        report = generate_weekly_ai_report()
        # Should not crash with no data
        assert isinstance(report["surface_scores"], dict)
        assert isinstance(report["recommendations"], list)

    def test_telegram_disabled_by_default(self):
        from ai_weekly_report import send_weekly_ai_report_to_telegram
        result = send_weekly_ai_report_to_telegram({"headline": "test"})
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# Part 5: API route tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvaluationRoutes:
    @pytest.fixture
    def app(self):
        from flask import Flask
        app = Flask(__name__)
        from athena_research.ai_evaluation_routes import register_ai_evaluation_routes
        register_ai_evaluation_routes(app)
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_summary_returns_json(self, client):
        resp = client.get("/api/ai/evaluation/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "do_not_auto_apply" in data
        assert "overall_summary" in data

    def test_summary_with_lookback(self, client):
        resp = client.get("/api/ai/evaluation/summary?lookback_days=7")
        assert resp.status_code == 200

    def test_surfaces_returns_json(self, client):
        resp = client.get("/api/ai/evaluation/surfaces")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "surface_metrics" in data

    def test_vision_returns_json(self, client):
        resp = client.get("/api/ai/evaluation/vision")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sample_count" in data

    def test_debate_returns_json(self, client):
        resp = client.get("/api/ai/evaluation/debate")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sample_count" in data

    def test_strategist_returns_json(self, client):
        resp = client.get("/api/ai/evaluation/strategist")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sample_count" in data

    def test_weekly_returns_json(self, client):
        resp = client.get("/api/ai/evaluation/weekly")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["do_not_auto_apply"] is True

    def test_generate_weekly_returns_json(self, client):
        resp = client.post("/api/ai/evaluation/generate-weekly")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "headline" in data

    def test_invalid_lookback_defaults(self, client):
        resp = client.get("/api/ai/evaluation/summary?lookback_days=invalid")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Part 7: Logger enhancement tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoggerEnhancements:
    def test_logger_with_evaluation_fields(self):
        from ai_agent_logger import log_ai_chat_turn
        result = log_ai_chat_turn(
            thread_id="test-thread",
            trace_id="test-trace",
            symbol="BTCUSDT",
            user_message="test message",
            assistant_answer="test answer",
            ai_surface="MARCUS",
            engine="engine_d",
            timeframe="H1",
            setup_type="scalp",
            data_freshness="FRESH",
            deterministic_decision_before_ai=False,
            deterministic_decision_after_ai=True,
        )
        assert result is True

    def test_logger_with_missing_fields(self):
        from ai_agent_logger import log_ai_chat_turn
        result = log_ai_chat_turn(
            thread_id=None,
            trace_id=None,
            symbol=None,
            user_message="minimal",
            assistant_answer="minimal",
        )
        assert result is True

    def test_logger_failure_non_fatal(self):
        from ai_agent_logger import _safe_json
        # Should not raise on any input
        assert _safe_json({"test": "data"})
        assert _safe_json(None)
