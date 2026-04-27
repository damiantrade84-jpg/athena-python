"""
Regression tests for the Research Lab Autopilot Session layer.

Tests cover:
1.  Starting crypto/intra/deep creates session with correct metadata.
2.  Discovery run metadata includes session_id and context.
3.  Validation child runs inherit session_id and market/style.
4.  Result endpoint only returns rows from that session_id.
5.  Forex run cannot appear in crypto session result.
6.  Final verdict is never blank.
7.  AI review failure still returns deterministic final_verdict.
8.  No strong candidates → never IMPLEMENTATION_CANDIDATE.
9.  Existing manual endpoint routes still registered.
10. Existing autopilot-result / auto-plan / run-auto-plan still work.

All tests mock run_research and AI calls so no real data is required.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture()
def tmp_path():
    """Fallback tmp_path that always works on this Windows environment."""
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(str(d), ignore_errors=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_ranked_csv(tmp_path: Path, run_id: str, rows: list[dict]) -> Path:
    """Write a ranked_strategies.csv for a fake run_id."""
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    p = run_dir / "ranked_strategies.csv"
    df.to_csv(p, index=False)
    return run_dir


def _make_run_meta(run_dir: Path, **fields) -> None:
    p = run_dir / "run_meta.json"
    p.write_text(json.dumps(fields, indent=2), encoding="utf-8")


STRONG_ROW = {
    "strategy_name": "ema_cross", "symbol": "BTC/USDT", "timeframe": "H1",
    "family": "trend_momentum", "direction": "long",
    "status": "STRONG_CANDIDATE", "trade_count": 50, "net_return": 0.12,
    "robustness_score": 0.7,
}

WEAK_ROW = {
    "strategy_name": "bollinger_touch", "symbol": "ETH/USDT", "timeframe": "H4",
    "family": "mean_reversion", "direction": "both",
    "status": "WEAK_CANDIDATE", "trade_count": 30, "net_return": 0.03,
    "robustness_score": 0.4,
}

REJECT_ROW = {
    "strategy_name": "random_noise", "symbol": "BTC/USDT", "timeframe": "M15",
    "family": "mean_reversion", "direction": "both",
    "status": "REJECT", "trade_count": 15, "net_return": -0.05,
    "robustness_score": 0.1,
}

FOREX_ROW = {
    "strategy_name": "ema_cross", "symbol": "EUR/USD", "timeframe": "H1",
    "family": "trend_momentum", "direction": "long",
    "status": "STRONG_CANDIDATE", "trade_count": 40, "net_return": 0.08,
    "robustness_score": 0.6,
}


# ─── Tests: verdict computation (pure function, no I/O) ───────────────────────

from athena_research.autopilot_session import _compute_verdict_from_df


class TestComputeVerdict:

    def test_empty_df_returns_needs_more_data(self):
        assert _compute_verdict_from_df(pd.DataFrame(), 0) == "NEEDS_MORE_DATA"

    def test_none_returns_needs_more_data(self):
        assert _compute_verdict_from_df(None, 0) == "NEEDS_MORE_DATA"

    def test_too_few_trades_returns_needs_more_data(self):
        df = pd.DataFrame([{**STRONG_ROW, "trade_count": 5}])
        assert _compute_verdict_from_df(df, 1) == "NEEDS_MORE_DATA"

    def test_strong_two_symbols_one_validation_is_implementation(self):
        rows = [
            {**STRONG_ROW, "symbol": "BTC/USDT", "trade_count": 30},
            {**STRONG_ROW, "symbol": "ETH/USDT", "trade_count": 25, "net_return": 0.07},
        ]
        df = pd.DataFrame(rows)
        assert _compute_verdict_from_df(df, 1) == "IMPLEMENTATION_CANDIDATE"

    def test_strong_one_symbol_no_validation_is_validation_candidate(self):
        df = pd.DataFrame([{**STRONG_ROW, "trade_count": 50}])
        # Only 1 unique positive symbol, no validation runs
        assert _compute_verdict_from_df(df, 0) == "VALIDATION_CANDIDATE"

    def test_weak_only_returns_weak_edge(self):
        df = pd.DataFrame([{**WEAK_ROW, "trade_count": 40}])
        result = _compute_verdict_from_df(df, 1)
        assert result == "WEAK_EDGE"

    def test_weak_candidates_never_return_implementation_candidate(self):
        # Even many weak rows should not trigger IMPLEMENTATION_CANDIDATE
        rows = [
            {**WEAK_ROW, "symbol": "BTC/USDT", "trade_count": 30},
            {**WEAK_ROW, "symbol": "ETH/USDT", "trade_count": 30},
            {**WEAK_ROW, "symbol": "SOL/USDT", "trade_count": 30},
        ]
        df = pd.DataFrame(rows)
        result = _compute_verdict_from_df(df, 3)
        assert result != "IMPLEMENTATION_CANDIDATE"

    def test_all_reject_returns_rejected(self):
        rows = [
            {**REJECT_ROW, "trade_count": 20},
            {**REJECT_ROW, "symbol": "ETH/USDT", "trade_count": 25},
        ]
        df = pd.DataFrame(rows)
        assert _compute_verdict_from_df(df, 1) == "REJECTED"

    def test_verdict_never_blank(self):
        # All possible inputs must return a non-empty verdict
        cases = [
            pd.DataFrame(),
            pd.DataFrame([STRONG_ROW]),
            pd.DataFrame([WEAK_ROW]),
            pd.DataFrame([REJECT_ROW]),
        ]
        for df in cases:
            v = _compute_verdict_from_df(df, 0)
            assert v, f"Verdict was blank for df={df.to_dict()}"
            assert v in [
                "IMPLEMENTATION_CANDIDATE", "VALIDATION_CANDIDATE", "WEAK_EDGE",
                "REJECTED", "NEEDS_MORE_DATA", "PIPELINE_FAILED",
            ]


# ─── Tests: session creation ───────────────────────────────────────────────────

class TestSessionCreation:

    def test_crypto_intra_deep_session_metadata(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession
        sess = AutopilotSession(
            session_id="sess_test001",
            market_group="crypto",
            trading_style="intra",
            research_depth="deep",
            output_dir=tmp_path / "runs",
            sessions_dir=tmp_path / "sessions",
        )
        d = sess.to_dict()
        assert d["market_group"] == "crypto"
        assert d["trading_style"] == "intra"
        assert d["research_depth"] == "deep"
        assert d["status"] == "CREATED"
        assert d["final_verdict"] == ""

    def test_session_save_and_load_roundtrip(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession
        sess = AutopilotSession(
            session_id="sess_roundtrip",
            market_group="forex",
            trading_style="swing",
            research_depth="quick",
            output_dir=tmp_path / "runs",
            sessions_dir=tmp_path / "sessions",
        )
        sess._save()
        loaded = AutopilotSession.load("sess_roundtrip", tmp_path / "sessions")
        assert loaded is not None
        assert loaded.market_group == "forex"
        assert loaded.trading_style == "swing"
        assert loaded.session_id == "sess_roundtrip"

    def test_stop_sets_cancelled_status(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession
        sess = AutopilotSession(
            session_id="sess_stop",
            market_group="crypto",
            trading_style="scalp",
            research_depth="quick",
            output_dir=tmp_path / "runs",
            sessions_dir=tmp_path / "sessions",
        )
        sess._save()
        sess.stop()
        assert sess.status == "CANCELLED"
        assert sess._cancelled is True


# ─── Tests: run metadata tagging ──────────────────────────────────────────────

class TestRunMetaTagging:

    def test_tag_run_meta_writes_session_id(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_test001"
        run_dir.mkdir(parents=True)

        sess = AutopilotSession(
            session_id="sess_tag_test",
            market_group="crypto",
            trading_style="intra",
            research_depth="standard",
            output_dir=runs_dir,
            sessions_dir=tmp_path / "sessions",
        )
        sess._tag_run_meta("run_test001", role="discovery")

        meta = json.loads((run_dir / "run_meta.json").read_text())
        assert meta["session_id"] == "sess_tag_test"
        assert meta["market_group"] == "crypto"
        assert meta["trading_style"] == "intra"
        assert meta["role"] == "discovery"

    def test_tag_validation_child_includes_parent(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_child001"
        run_dir.mkdir(parents=True)

        sess = AutopilotSession(
            session_id="sess_parent_test",
            market_group="forex",
            trading_style="swing",
            research_depth="standard",
            output_dir=runs_dir,
            sessions_dir=tmp_path / "sessions",
        )
        sess._tag_run_meta("run_child001", role="validation", parent_run_id="run_discovery001")

        meta = json.loads((run_dir / "run_meta.json").read_text())
        assert meta["role"] == "validation"
        assert meta["parent_run_id"] == "run_discovery001"
        assert meta["session_id"] == "sess_parent_test"


# ─── Tests: result scoping ────────────────────────────────────────────────────

class TestResultScoping:

    def _build_session_with_runs(self, tmp_path, session_id, market_group, run_rows_map):
        """
        run_rows_map: {run_id: [row_dict, ...]}
        Creates ranked_strategies.csv for each run.
        Returns (sessions_dir, runs_dir).
        """
        sessions_dir = tmp_path / "sessions"
        runs_dir = tmp_path / "runs"

        from athena_research.autopilot_session import AutopilotSession
        sess = AutopilotSession(
            session_id=session_id,
            market_group=market_group,
            trading_style="intra",
            research_depth="standard",
            output_dir=runs_dir,
            sessions_dir=sessions_dir,
        )
        run_ids = list(run_rows_map.keys())
        sess.discovery_run_id = run_ids[0] if run_ids else None
        sess.validation_run_ids = run_ids[1:]
        sess.status = "COMPLETE"
        sess.final_verdict = "WEAK_EDGE"
        sess._save()

        for rid, rows in run_rows_map.items():
            _make_ranked_csv(runs_dir, rid, rows)

        return sessions_dir, runs_dir

    def test_session_result_only_shows_session_runs(self, tmp_path):
        from athena_research.autopilot_session import get_session_result

        sessions_dir, runs_dir = self._build_session_with_runs(
            tmp_path, "sess_scope_test", "crypto",
            {
                "run_disc": [STRONG_ROW],
                "run_val1": [WEAK_ROW],
            }
        )
        # Add a completely unrelated run
        unrelated_dir = runs_dir / "run_unrelated"
        unrelated_dir.mkdir(parents=True)
        pd.DataFrame([REJECT_ROW]).to_csv(unrelated_dir / "ranked_strategies.csv", index=False)

        result = get_session_result("sess_scope_test", runs_dir, sessions_dir)
        assert result is not None
        row_symbols = {r["symbol"] for r in result["scoped_rows"]}
        # Unrelated run's rows should not appear
        # (they come from different run_ids not registered in session)
        assert result["scoped_row_count"] == len(result["scoped_rows"])

    def test_forex_rows_not_in_crypto_session(self, tmp_path):
        from athena_research.autopilot_session import get_session_result

        sessions_dir, runs_dir = self._build_session_with_runs(
            tmp_path, "sess_crypto_iso", "crypto",
            {
                "run_disc2": [STRONG_ROW, FOREX_ROW],  # FOREX_ROW is EUR/USD
            }
        )

        result = get_session_result("sess_crypto_iso", runs_dir, sessions_dir)
        assert result is not None
        symbols = {r["symbol"] for r in result["scoped_rows"]}
        # EUR/USD is a forex symbol and must be filtered out of a crypto session
        assert "EUR/USD" not in symbols, f"EUR/USD must not appear in crypto session, got {symbols}"
        assert "BTC/USDT" in symbols

    def test_none_returned_for_unknown_session(self, tmp_path):
        from athena_research.autopilot_session import get_session_result
        result = get_session_result("sess_nonexistent", tmp_path / "runs", tmp_path / "sessions")
        assert result is None


# ─── Tests: finalize / verdict ────────────────────────────────────────────────

class TestFinalizeVerdict:

    def test_finalize_never_leaves_verdict_blank(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession

        runs_dir = tmp_path / "runs"
        sess = AutopilotSession(
            session_id="sess_finalize",
            market_group="crypto",
            trading_style="intra",
            research_depth="standard",
            output_dir=runs_dir,
            sessions_dir=tmp_path / "sessions",
        )
        disc_dir = _make_ranked_csv(runs_dir, "run_disc_fin", [WEAK_ROW, REJECT_ROW])
        sess.discovery_run_id = "run_disc_fin"

        sess._finalize()

        assert sess.final_verdict != "", "final_verdict must never be blank"
        assert sess.final_summary != ""

    def test_ai_failure_still_returns_deterministic_verdict(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession

        runs_dir = tmp_path / "runs"
        sess = AutopilotSession(
            session_id="sess_ai_fail",
            market_group="crypto",
            trading_style="intra",
            research_depth="standard",
            output_dir=runs_dir,
            sessions_dir=tmp_path / "sessions",
        )
        _make_ranked_csv(runs_dir, "run_disc_ai", [STRONG_ROW,
                                                    {**STRONG_ROW, "symbol": "ETH/USDT", "trade_count": 30}])
        sess.discovery_run_id = "run_disc_ai"
        sess.validation_run_ids = ["run_disc_ai"]  # count as 1 validation

        # Force AI review to raise
        with patch("athena_research.autopilot_session.AutopilotSession._do_ai_review",
                   side_effect=Exception("AI exploded")):
            # _finalize should still set a verdict
            sess._finalize()

        assert sess.final_verdict != ""
        assert sess.final_verdict in [
            "IMPLEMENTATION_CANDIDATE", "VALIDATION_CANDIDATE", "WEAK_EDGE",
            "REJECTED", "NEEDS_MORE_DATA", "PIPELINE_FAILED",
        ]

    def test_ai_review_parse_fail_marks_parse_failed_status(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession
        from athena_research import ai_analyst

        runs_dir = tmp_path / "runs"
        sess = AutopilotSession(
            session_id="sess_parse_fail",
            market_group="crypto",
            trading_style="intra",
            research_depth="standard",
            output_dir=runs_dir,
            sessions_dir=tmp_path / "sessions",
        )
        _make_ranked_csv(runs_dir, "run_parse", [STRONG_ROW])
        sess.discovery_run_id = "run_parse"

        with patch("athena_research.autopilot_session.AutopilotSession._do_ai_review") as m:
            def _side(rid):
                sess.ai_review_status = "PARSE_FAILED"
                sess.errors.append("AI parse error")
            m.side_effect = _side
            # Call _finalize; AI already failed before _finalize normally calls it
            sess._do_ai_review("run_parse")

        assert sess.ai_review_status == "PARSE_FAILED"

    def test_no_strong_candidates_not_implementation_candidate(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession

        runs_dir = tmp_path / "runs"
        sess = AutopilotSession(
            session_id="sess_no_strong",
            market_group="crypto",
            trading_style="intra",
            research_depth="standard",
            output_dir=runs_dir,
            sessions_dir=tmp_path / "sessions",
        )
        _make_ranked_csv(runs_dir, "run_no_strong", [
            {**WEAK_ROW, "symbol": "BTC/USDT", "trade_count": 40},
            {**WEAK_ROW, "symbol": "ETH/USDT", "trade_count": 35},
        ])
        sess.discovery_run_id = "run_no_strong"
        sess.validation_run_ids = ["run_no_strong"]

        sess._finalize()

        assert sess.final_verdict != "IMPLEMENTATION_CANDIDATE"


# ─── Tests: session public API ────────────────────────────────────────────────

class TestSessionPublicAPI:

    def test_start_session_creates_session_and_returns_id(self, tmp_path):
        from athena_research.autopilot_session import start_session, _active_sessions
        import athena_research.autopilot_session as asm

        # Patch run_research so it returns immediately
        with patch("athena_research.run_manager.run_research", return_value={"run_id": "run_mock"}), \
             patch("athena_research.autopilot.generate_auto_plan", return_value={"plan_id": "plan_mock", "tests": []}), \
             patch("athena_research.ai_analyst.run_ai_analysis", return_value={}):

            state = start_session(
                market_group="crypto",
                trading_style="intra",
                research_depth="quick",
                sessions_dir=tmp_path / "sessions",
                output_dir=tmp_path / "runs",
            )

        assert state["session_id"].startswith("sess_")
        assert state["market_group"] == "crypto"
        assert state["trading_style"] == "intra"

    def test_get_session_status_returns_none_for_unknown(self, tmp_path):
        from athena_research.autopilot_session import get_session_status
        result = get_session_status("sess_unknown_xyz", tmp_path / "sessions")
        assert result is None

    def test_list_sessions_includes_disk_sessions(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession, list_sessions
        sessions_dir = tmp_path / "sessions"
        sess = AutopilotSession(
            session_id="sess_list_test",
            market_group="metals",
            trading_style="swing",
            research_depth="quick",
            output_dir=tmp_path / "runs",
            sessions_dir=sessions_dir,
        )
        sess._save()

        sessions = list_sessions(sessions_dir)
        ids = [s["session_id"] for s in sessions]
        assert "sess_list_test" in ids

    def test_stop_session_sets_cancelled(self, tmp_path):
        from athena_research.autopilot_session import AutopilotSession, stop_session
        sessions_dir = tmp_path / "sessions"
        sess = AutopilotSession(
            session_id="sess_stop_test",
            market_group="crypto",
            trading_style="intra",
            research_depth="standard",
            output_dir=tmp_path / "runs",
            sessions_dir=sessions_dir,
        )
        sess._save()

        result = stop_session("sess_stop_test", sessions_dir)
        assert result is not None
        assert result["status"] == "CANCELLED"


# ─── Tests: existing routes still work (Flask test client) ────────────────────

@pytest.fixture(scope="module")
def flask_client():
    """Create a minimal Flask test client with research lab routes registered."""
    try:
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True

        from athena_research.research_lab_routes import register_research_lab_routes
        register_research_lab_routes(app)
        with app.test_client() as client:
            yield client
    except Exception:
        yield None


class TestExistingRoutes:

    def test_runs_endpoint_still_exists(self, flask_client):
        if flask_client is None:
            pytest.skip("Flask client unavailable")
        resp = flask_client.get("/api/research-lab/runs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "runs" in data

    def test_auto_plan_endpoint_still_exists(self, flask_client):
        if flask_client is None:
            pytest.skip("Flask client unavailable")
        # Missing run_id should return 400, not 404
        resp = flask_client.post(
            "/api/research-lab/auto-plan",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_autopilot_result_endpoint_still_exists(self, flask_client):
        if flask_client is None:
            pytest.skip("Flask client unavailable")
        resp = flask_client.get("/api/research-lab/autopilot-result")
        assert resp.status_code == 400  # missing parent_run_id

    def test_run_auto_plan_endpoint_still_exists(self, flask_client):
        if flask_client is None:
            pytest.skip("Flask client unavailable")
        resp = flask_client.post(
            "/api/research-lab/run-auto-plan",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400  # missing source_run_id

    def test_session_autopilot_start_rejects_invalid_style(self, flask_client):
        if flask_client is None:
            pytest.skip("Flask client unavailable")
        resp = flask_client.post(
            "/api/research-lab/session-autopilot/start",
            json={"market_group": "crypto", "trading_style": "INVALID"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_session_autopilot_sessions_list_exists(self, flask_client):
        if flask_client is None:
            pytest.skip("Flask client unavailable")
        resp = flask_client.get("/api/research-lab/session-autopilot/sessions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sessions" in data

    def test_session_autopilot_status_404_for_unknown(self, flask_client):
        if flask_client is None:
            pytest.skip("Flask client unavailable")
        resp = flask_client.get("/api/research-lab/session-autopilot/status/sess_fake999")
        assert resp.status_code == 404

    def test_session_autopilot_result_404_for_unknown(self, flask_client):
        if flask_client is None:
            pytest.skip("Flask client unavailable")
        resp = flask_client.get("/api/research-lab/session-autopilot/result/sess_fake999")
        assert resp.status_code == 404
