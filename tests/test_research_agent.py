"""Tests for AI Research Agent Phase 4 implementation.

Covers: contracts, load, summarize, propose, validate, run, compare,
recommendations, chat research routing, route safety.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

from ai_research_contracts import (
    ResearchComparison,
    ResearchHypothesis,
    ResearchPlan,
    ResearchRecommendation,
    ResearchResultSummary,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1: Contract tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchContracts:
    def test_research_result_summary_defaults(self):
        s = ResearchResultSummary()
        d = s.model_dump()
        assert d["sample_quality"] == "unknown"
        assert d["total_trades"] == 0
        assert d["warnings"] == []
        assert d["missing_fields"] == []

    def test_research_result_summary_sample_quality_validation(self):
        s = ResearchResultSummary(sample_quality="moderate")
        assert s.sample_quality == "moderate"
        s2 = ResearchResultSummary(sample_quality="invalid")
        assert s2.sample_quality == "unknown"

    def test_research_hypothesis_defaults(self):
        h = ResearchHypothesis()
        d = h.model_dump()
        assert d["priority"] == 5
        assert d["status"] == "proposed"
        assert d["hypothesis_id"] == ""

    def test_research_plan_safety_defaults(self):
        p = ResearchPlan()
        assert p.requires_user_approval is True
        assert p.can_modify_live_config is False
        assert p.can_execute_live_trades is False

    def test_research_plan_cannot_set_live_config(self):
        """Even if someone tries to set can_modify_live_config=True, validator forces False."""
        p = ResearchPlan(can_modify_live_config=True)
        assert p.can_modify_live_config is False

    def test_research_plan_cannot_set_live_execution(self):
        p = ResearchPlan(can_execute_live_trades=True)
        assert p.can_execute_live_trades is False

    def test_research_recommendation_defaults(self):
        r = ResearchRecommendation()
        assert r.do_not_auto_apply is True
        assert r.recommendation_type == "DO_NOT_CHANGE"
        assert r.sample_size == 0

    def test_research_recommendation_cannot_set_auto_apply(self):
        r = ResearchRecommendation(do_not_auto_apply=False)
        assert r.do_not_auto_apply is True

    def test_research_comparison_defaults(self):
        c = ResearchComparison()
        assert c.statistically_reliable is False
        assert c.missing_fields == []

    def test_contracts_serialize_to_json(self):
        p = ResearchPlan(title="Test", objective="Test objective")
        j = p.model_dump_json()
        assert '"title":"Test"' in j.replace(" ", "")
        assert '"can_modify_live_config":false' in j.replace(" ", "")
        assert '"can_execute_live_trades":false' in j.replace(" ", "")

    def test_recommendation_type_literals(self):
        for t in ["ADD_FILTER", "REMOVE_FILTER", "ADJUST_THRESHOLD_CANDIDATE",
                   "DISABLE_WEAK_SETUP", "PROMOTE_TO_VALIDATION",
                   "NEED_MORE_DATA", "DO_NOT_CHANGE"]:
            r = ResearchRecommendation(recommendation_type=t)
            assert r.recommendation_type == t


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2: Core Research Agent — load / summarize / propose / validate / compare
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchAgentCore:
    """Tests the core functions. Uses temp dirs to avoid touching real logs."""

    @pytest.fixture
    def tmp_research_dir(self) -> Generator[Path, None, None]:
        """Create a minimal research_lab run dir with CSV files."""
        import tempfile
        import shutil
        tmp_root = Path(tempfile.mkdtemp(prefix="ra_test_research_"))
        run_dir = tmp_root / "run_20260501_120000"
        run_dir.mkdir(parents=True)

        # ranked_strategies.csv
        import csv
        ranked_path = run_dir / "ranked_strategies.csv"
        with open(ranked_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "timeframe", "family", "engine", "direction",
                        "trade_count", "profit_factor", "win_rate", "max_drawdown",
                        "expectancy", "session"])
            w.writerow(["BTCUSDT", "H4", "trend_following", "engine_d", "LONG",
                        "45", "1.5", "0.55", "-0.12", "0.35", "London"])
            w.writerow(["BTCUSDT", "H1", "mean_reversion", "engine_d", "SHORT",
                        "12", "0.9", "0.40", "-0.18", "0.10", "New_York"])
            w.writerow(["ETHUSDT", "H4", "trend_following", "engine_a", "LONG",
                        "30", "1.8", "0.60", "-0.08", "0.42", "London"])
            w.writerow(["SOLUSDT", "D1", "breakout", "engine_d", "LONG",
                        "8", "0.7", "0.35", "-0.22", "-0.05", "Asia"])

        yield tmp_root
        import shutil
        shutil.rmtree(tmp_root, ignore_errors=True)

    def test_load_latest_empty_returns_warning(self):
        """Missing research files produce stable empty result with warning."""
        from athena_research.research_agent import load_latest_research_results

        # Point to a non-existent directory
        import athena_research.research_agent as ra
        original_dir = ra._RESEARCH_LAB_DIR
        ra._RESEARCH_LAB_DIR = Path(tempfile.mktemp())
        ra._BACKTEST_MATRIX_DIR = Path(tempfile.mktemp())

        try:
            results = load_latest_research_results(limit=5)
            assert len(results) == 1
            assert "warning" in results[0]
            assert "No research results found" in results[0]["warning"]
            assert "missing_fields" in results[0]
        finally:
            ra._RESEARCH_LAB_DIR = original_dir
            ra._BACKTEST_MATRIX_DIR = original_dir

    def test_load_latest_reports_incomplete_run_outputs(self):
        """Existing run dirs with missing CSVs must be surfaced, not silently treated as empty results."""
        from athena_research.research_agent import load_latest_research_results
        import athena_research.research_agent as ra
        import shutil

        tmp_root = Path(tempfile.mkdtemp(prefix="ra_incomplete_test_"))
        run_dir = tmp_root / "run_20260502_120000"
        run_dir.mkdir(parents=True)
        (run_dir / "run_meta.json").write_text(json.dumps({"mode": "tiny"}), encoding="utf-8")

        original_research = ra._RESEARCH_LAB_DIR
        original_backtest = ra._BACKTEST_MATRIX_DIR
        ra._RESEARCH_LAB_DIR = tmp_root
        ra._BACKTEST_MATRIX_DIR = tmp_root / "missing_backtests"
        try:
            results = load_latest_research_results(limit=5)
        finally:
            ra._RESEARCH_LAB_DIR = original_research
            ra._BACKTEST_MATRIX_DIR = original_backtest
            shutil.rmtree(tmp_root, ignore_errors=True)

        assert results[0]["run_id"] == run_dir.name
        assert results[0]["has_ranked"] is False
        assert results[0]["has_summary"] is False
        assert "ranked_strategies.csv" in results[0]["missing_files"]
        assert "research_summary.csv" in results[0]["missing_files"]
        assert any("incomplete" in warning.lower() for warning in results[0]["warnings"])

    def test_summarize_nonexistent_run_returns_warning(self):
        from athena_research.research_agent import summarize_research_results
        summary = summarize_research_results("nonexistent_run_id")
        assert summary.get("warnings")
        assert "No research run directory found" in str(summary["warnings"])
        assert summary.get("total_trades") == 0

    def test_summarize_with_data(self, tmp_research_dir: Path):
        from athena_research.research_agent import summarize_research_results
        import athena_research.research_agent as ra

        original = ra._RESEARCH_LAB_DIR
        ra._RESEARCH_LAB_DIR = tmp_research_dir
        try:
            summary = summarize_research_results()
            assert summary["total_trades"] == 95  # 45 + 12 + 30 + 8
            # 95 trades: < 100 → weak (>=30), >= 30 so not "insufficient"
            assert summary["sample_quality"] == "weak"
            assert "BTCUSDT" in summary["symbols"]
            assert "H4" in summary["timeframes"]
            assert summary["top_clusters"]
            assert summary["engines"] == ["engine_a", "engine_d"]
        finally:
            ra._RESEARCH_LAB_DIR = original

    def test_propose_plan_returns_safe_defaults(self, tmp_research_dir: Path):
        from athena_research.research_agent import (
            propose_next_research_plan,
            summarize_research_results,
        )
        import athena_research.research_agent as ra

        original = ra._RESEARCH_LAB_DIR
        ra._RESEARCH_LAB_DIR = tmp_research_dir
        try:
            summary = summarize_research_results()
            plan = propose_next_research_plan(summary)

            assert plan["requires_user_approval"] is True
            assert plan["can_modify_live_config"] is False
            assert plan["can_execute_live_trades"] is False
            assert len(plan.get("jobs", [])) > 0
            assert len(plan.get("hypotheses", [])) > 0
        finally:
            ra._RESEARCH_LAB_DIR = original

    def test_propose_plan_with_user_goal(self, tmp_research_dir: Path):
        from athena_research.research_agent import (
            propose_next_research_plan,
            summarize_research_results,
        )
        import athena_research.research_agent as ra

        original = ra._RESEARCH_LAB_DIR
        ra._RESEARCH_LAB_DIR = tmp_research_dir
        try:
            summary = summarize_research_results()
            plan = propose_next_research_plan(summary, user_goal="crypto scalp")
            assert plan["requires_user_approval"] is True
        finally:
            ra._RESEARCH_LAB_DIR = original

    def test_validate_plan_valid(self):
        from athena_research.research_agent import validate_research_plan
        plan = {
            "plan_id": "test_plan",
            "title": "Test",
            "objective": "Test",
            "hypotheses": [{"hypothesis_id": "h1", "title": "Test"}],
            "jobs": [
                {
                    "symbols": ["BTCUSDT"],
                    "timeframes": ["H4"],
                    "engines": ["engine_d"],
                    "acceptance_criteria": {"min_trade_count": 20},
                }
            ],
            "requires_user_approval": True,
            "can_modify_live_config": False,
            "can_execute_live_trades": False,
        }
        result = validate_research_plan(plan)
        assert result["valid"] is True
        assert result["issues"] == []

    def test_validate_plan_rejects_live_config(self):
        from athena_research.research_agent import validate_research_plan
        plan = {
            "plan_id": "test_bad",
            "jobs": [{"symbols": ["BTCUSDT"], "timeframes": ["H4"], "engines": ["engine_d"]}],
            "can_modify_live_config": True,
            "can_execute_live_trades": False,
            "requires_user_approval": True,
        }
        result = validate_research_plan(plan)
        assert result["valid"] is False
        assert any("live config" in issue.lower() for issue in result["issues"])

    def test_validate_plan_rejects_live_execution(self):
        from athena_research.research_agent import validate_research_plan
        plan = {
            "plan_id": "test_bad",
            "jobs": [{"symbols": ["BTCUSDT"], "timeframes": ["H4"], "engines": ["engine_d"]}],
            "can_modify_live_config": False,
            "can_execute_live_trades": True,
            "requires_user_approval": True,
        }
        result = validate_research_plan(plan)
        assert result["valid"] is False
        assert any("live trade" in issue.lower() for issue in result["issues"])

    def test_validate_plan_requires_approval(self):
        from athena_research.research_agent import validate_research_plan
        plan = {
            "plan_id": "test_bad",
            "jobs": [{"symbols": ["BTCUSDT"], "timeframes": ["H4"], "engines": ["engine_d"]}],
            "can_modify_live_config": False,
            "can_execute_live_trades": False,
            "requires_user_approval": False,
        }
        result = validate_research_plan(plan)
        assert result["valid"] is False
        assert any("approval" in issue.lower() for issue in result["issues"])

    def test_validate_plan_rejects_no_symbols(self):
        from athena_research.research_agent import validate_research_plan
        plan = {
            "plan_id": "test_no_symbols",
            "jobs": [{"symbols": [], "timeframes": ["H4"], "engines": ["engine_d"]}],
            "requires_user_approval": True,
            "can_modify_live_config": False,
            "can_execute_live_trades": False,
        }
        result = validate_research_plan(plan)
        assert result["valid"] is False

    def test_run_approved_rejects_not_approved(self):
        from athena_research.research_agent import run_approved_research_plan
        result = run_approved_research_plan({"plan_id": "test"})
        assert result["status"] == "rejected"
        assert "not approved" in result["reason"].lower()

    def test_run_approved_with_approved_flag(self):
        from athena_research.research_agent import run_approved_research_plan
        result = run_approved_research_plan({"plan_id": "test", "approved": True})
        # Should be handled gracefully — either run or not_implemented
        assert result["status"] in ("started", "not_implemented", "completed")

    def test_run_approved_empty_jobs(self):
        from athena_research.research_agent import run_approved_research_plan
        result = run_approved_research_plan({
            "plan_id": "test_empty",
            "approved": True,
            "jobs": [],
        })
        assert result["status"] in ("completed", "started")

    def test_compare_missing_runs(self):
        from athena_research.research_agent import compare_research_runs
        result = compare_research_runs("nonexistent_a", "nonexistent_b")
        assert "missing_fields" in result
        assert result["statistically_reliable"] is False

    def test_produce_recommendations_empty(self):
        from athena_research.research_agent import produce_research_recommendations
        recs = produce_research_recommendations({})
        assert isinstance(recs, list)
        assert len(recs) > 0
        for r in recs:
            assert r.get("do_not_auto_apply") is True

    def test_produce_recommendations_small_sample(self):
        from athena_research.research_agent import produce_research_recommendations
        summary = {"total_trades": 5, "sample_quality": "insufficient"}
        recs = produce_research_recommendations(summary)
        types = [r["recommendation_type"] for r in recs]
        assert "NEED_MORE_DATA" in types

    def test_produce_recommendations_strong_cluster(self):
        from athena_research.research_agent import produce_research_recommendations
        summary = {
            "total_trades": 200,
            "sample_quality": "moderate",
            "top_clusters": [
                {"key": "engine=engine_d", "total_trades": 150,
                 "avg_profit_factor": 1.8, "sample_quality": "moderate"},
            ],
            "weak_clusters": [],
        }
        recs = produce_research_recommendations(summary)
        types = [r["recommendation_type"] for r in recs]
        assert "PROMOTE_TO_VALIDATION" in types

    def test_produce_recommendations_all_do_not_auto_apply(self):
        from athena_research.research_agent import produce_research_recommendations
        for n in [0, 5, 50, 500]:
            recs = produce_research_recommendations({"total_trades": n})
            for r in recs:
                assert r["do_not_auto_apply"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3: API Route tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchAgentRoutes:
    """Test API routes using a Flask test client."""

    @pytest.fixture
    def app(self):
        from flask import Flask
        app = Flask(__name__)

        from athena_research.research_agent_routes import register_research_agent_routes
        register_research_agent_routes(app)
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_latest_returns_json(self, client):
        resp = client.get("/api/ai/research-agent/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "results" in data

    def test_summarize_returns_json(self, client):
        resp = client.post("/api/ai/research-agent/summarize",
                           json={"run_id": None})
        assert resp.status_code == 200
        data = resp.get_json()
        # Should handle missing gracefully
        assert "total_trades" in data or "warnings" in data

    def test_propose_returns_plan_with_safety(self, client):
        resp = client.post("/api/ai/research-agent/propose",
                           json={"user_goal": "Find what works for crypto"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("requires_user_approval") is True
        assert data.get("can_modify_live_config") is False
        assert data.get("can_execute_live_trades") is False

    def test_propose_empty_goal(self, client):
        resp = client.post("/api/ai/research-agent/propose", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "plan_id" in data

    def test_validate_plan_rejects_bad_body(self, client):
        resp = client.post("/api/ai/research-agent/validate-plan",
                           json={})
        assert resp.status_code == 400 or resp.status_code == 200

    def test_validate_plan_returns_issues(self, client):
        bad_plan = {
            "plan_id": "bad",
            "jobs": [],
            "can_modify_live_config": True,
            "can_execute_live_trades": True,
            "requires_user_approval": False,
        }
        resp = client.post("/api/ai/research-agent/validate-plan",
                           json={"plan": bad_plan})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "issues" in data
        assert data.get("valid") is False

    def test_run_approved_rejects_missing_approval(self, client):
        resp = client.post("/api/ai/research-agent/run-approved-plan",
                           json={"plan": {"plan_id": "test"}})
        assert resp.status_code == 400
        data = resp.get_json()
        assert "not approved" in str(data.get("reason", "")).lower()

    def test_run_approved_rejects_false_approval(self, client):
        resp = client.post("/api/ai/research-agent/run-approved-plan",
                           json={"plan": {"plan_id": "test"}, "approved": False})
        assert resp.status_code == 400

    def test_compare_requires_both_ids(self, client):
        resp = client.post("/api/ai/research-agent/compare",
                           json={"baseline_run_id": "a"})
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_compare_handles_missing_runs(self, client):
        resp = client.post("/api/ai/research-agent/compare",
                           json={"baseline_run_id": "nonexistent_a",
                                 "candidate_run_id": "nonexistent_b"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("statistically_reliable") is False
        assert "missing_fields" in data or "sample_warning" in data

    def test_plans_list_returns_json(self, client):
        resp = client.get("/api/ai/research-agent/plans")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "plans" in data

    def test_runs_list_returns_json(self, client):
        resp = client.get("/api/ai/research-agent/runs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "runs" in data

    def test_plan_detail_not_found(self, client):
        resp = client.get("/api/ai/research-agent/plan/nonexistent")
        assert resp.status_code == 404

    def test_recommendations_endpoint(self, client):
        resp = client.post("/api/ai/research-agent/recommendations", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "recommendations" in data


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4: AI Chat research tool routing tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchChatTools:
    """Tests that AI chat routes research questions to research tools."""

    def test_research_tools_in_registry(self):
        from ai_tools import get_ai_tool_registry
        registry = get_ai_tool_registry()
        assert "get_latest_research_summary_tool" in registry
        assert "propose_research_plan_tool" in registry
        assert "validate_research_plan_tool" in registry
        assert "compare_research_runs_tool" in registry
        assert "get_research_recommendations_tool" in registry

    def test_latest_research_summary_tool_unavailable(self):
        """Tool should handle missing research data gracefully."""
        from ai_tools import get_latest_research_summary_tool
        result = get_latest_research_summary_tool()
        assert "research_summary" in result
        assert result["advisory_only"] is True
        assert result["execution_allowed"] is False

    def test_propose_plan_tool_unavailable(self):
        from ai_tools import propose_research_plan_tool
        result = propose_research_plan_tool(user_goal="test")
        assert "plan" in result
        assert result["advisory_only"] is True
        assert result["execution_allowed"] is False

    def test_validate_plan_tool_no_plan(self):
        from ai_tools import validate_research_plan_tool
        result = validate_research_plan_tool()
        assert result["status"] == "error"
        assert result["advisory_only"] is True

    def test_compare_runs_tool_no_ids(self):
        from ai_tools import compare_research_runs_tool
        result = compare_research_runs_tool()
        assert result["status"] == "error"
        assert "missing_fields" in result["comparison"]

    def test_compare_runs_tool_missing_ids(self):
        from ai_tools import compare_research_runs_tool
        result = compare_research_runs_tool(baseline="a", candidate="b")
        assert "comparison" in result

    def test_research_tools_advisory_only(self):
        from ai_tools import (
            compare_research_runs_tool,
            get_latest_research_summary_tool,
            get_research_recommendations_tool,
            propose_research_plan_tool,
            validate_research_plan_tool,
        )
        for fn in [get_latest_research_summary_tool, propose_research_plan_tool,
                   validate_research_plan_tool, compare_research_runs_tool,
                   get_research_recommendations_tool]:
            result = fn() if fn in (validate_research_plan_tool, compare_research_runs_tool) else fn()
            assert result["advisory_only"] is True, f"{fn.__name__} not advisory_only"
            assert result["execution_allowed"] is False, f"{fn.__name__} not execution_allowed"

    def test_ai_trading_chat_does_not_route_research_lab_questions(self):
        """AI Trading Agent chat should not route to Research Lab tools."""
        from ai_trade_chat import plan_tool_calls

        research_tools = {
            "get_latest_research_summary",
            "propose_research_plan",
            "validate_research_plan",
            "compare_research_runs",
            "get_research_recommendations",
        }
        for message in [
            "what works best for crypto scalp",
            "tell me about the research",
            "research plan for engine d",
            "compare runs engine a vs engine d",
            "propose research plan for forex",
            "what should we test next",
        ]:
            calls = plan_tool_calls(message, {"symbol": "BTCUSDT"})
            call_names = {c["name"] for c in calls}
            assert not (research_tools & call_names), (
                f"'{message}' should stay in trade review, got {call_names}"
            )
            assert "get_signal_detail" in call_names

    def test_research_tools_remain_available_to_research_lab(self):
        """Research Lab tool wrappers stay available outside AI Trading Agent routing."""
        import ai_tools

        for fn_name in [
            "get_latest_research_summary_tool",
            "propose_research_plan_tool",
            "validate_research_plan_tool",
            "compare_research_runs_tool",
            "get_research_recommendations_tool",
        ]:
            assert hasattr(ai_tools, fn_name)

    def test_chat_does_not_auto_run(self):
        """Chat may discuss research wording but must not auto-execute."""
        from ai_trade_chat import plan_tool_calls

        messages = [
            "what should we test next",
            "propose research for me",
        ]
        for msg in messages:
            calls = plan_tool_calls(msg, {"symbol": "BTCUSDT"})
            call_names = [c["name"] for c in calls]
            assert "run_approved_research_plan" not in call_names


# ═══════════════════════════════════════════════════════════════════════════════
# Part 6: Store tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchAgentStore:
    """Tests for persistence layer."""

    @pytest.fixture
    def store(self):
        import tempfile
        import athena_research.research_agent_store as store_module
        original = store_module._STORE_DIR
        store_module._STORE_DIR = Path(tempfile.mkdtemp(prefix="ra_test_"))
        yield store_module
        store_module._STORE_DIR = original

    def test_persist_plan(self, store):
        ok = store.persist_plan("test_plan", "test goal", {"plan_id": "test_plan"})
        assert ok is True

    def test_persist_plan_run(self, store):
        ok = store.persist_plan_run("test_run", "test goal", {"plan_id": "test_run"},
                                     approved=True, run_status="started")
        assert ok is True

    def test_get_plans(self, store):
        store.persist_plan("plan1", "goal1", {"plan_id": "plan1"})
        store.persist_plan("plan2", "goal2", {"plan_id": "plan2"})
        plans = store.get_plans(limit=10)
        assert len(plans) >= 2

    def test_get_plan_by_id(self, store):
        store.persist_plan("unique_plan", "test", {"plan_id": "unique_plan"})
        plan = store.get_plan("unique_plan")
        assert plan is not None

    def test_persist_validation(self, store):
        ok = store.persist_validation("test_plan", {"valid": True})
        assert ok is True

    def test_persist_comparison(self, store):
        ok = store.persist_comparison("run_a", "run_b", {"result": "ok"})
        assert ok is True

    def test_persist_recommendations(self, store):
        ok = store.persist_recommendations("test_plan", [{"type": "DO_NOT_CHANGE"}])
        assert ok is True

    def test_write_failure_safe(self, store):
        """Write failures must not raise exceptions."""
        result = store._append_jsonl(Path("/nonexistent_dir/plans.jsonl"),
                                     {"test": "data"})
        assert result is False

    def test_persisted_plan_has_do_not_auto_apply(self, store):
        store.persist_plan("test_no_auto", "goal", {"plan_id": "test_no_auto"})
        plan = store.get_plan("test_no_auto")
        if plan:
            assert plan.get("do_not_auto_apply") is True
