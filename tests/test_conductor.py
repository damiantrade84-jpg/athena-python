"""Unit tests for deterministic AI conductor (no LLM)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

import conductor as conductor_mod

_CREATE_LEARNING_LOG = """
CREATE TABLE IF NOT EXISTS learning_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    trade_ts         TEXT,
    ticket           TEXT,
    pair             TEXT NOT NULL,
    asset_type       TEXT NOT NULL,
    direction        TEXT,
    engine           TEXT,
    outcome          TEXT,
    regime           TEXT,
    win              INTEGER,
    r_multiple       REAL
);
"""


def test_extract_conductor_microstructure_from_setup():
    sig = {
        "setup": {
            "volume_divergence": {"divergence": True, "type": "bearish"},
            "stop_run": {"stop_run": True, "confidence": "high"},
        }
    }
    v, s = conductor_mod.extract_conductor_microstructure(sig)
    assert v["divergence"] is True
    assert s["stop_run"] is True


def test_get_recent_performance_counts_only_engine_a_b():
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with sqlite3.connect(path) as con:
            con.executescript(_CREATE_LEARNING_LOG)
            ts = datetime.now(timezone.utc).isoformat()
            rows = [
                (ts, "EURUSD", "forex", "TRENDING", "scalp", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "scalp", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "scalp", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "scalp", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "scalp", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "engine_a", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "engine_a", 0, None),
                (ts, "EURUSD", "forex", "TRENDING", "engine_a", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "engine_b", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "engine_b", 1, None),
                (ts, "EURUSD", "forex", "TRENDING", "engine_b", 0, None),
            ]
            con.executemany(
                "INSERT INTO learning_log(ts,pair,asset_type,regime,engine,win,r_multiple) "
                "VALUES(?,?,?,?,?,?,?)",
                rows,
            )
            con.commit()

        perf = conductor_mod._get_recent_performance(path, "EURUSD", "TRENDING")
        assert perf["sample_size"] == 6
        assert perf["engine_a_wr"] == pytest.approx(2 / 3)
        assert perf["engine_b_wr"] == pytest.approx(2 / 3)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_route_respects_vision_config(monkeypatch):
    monkeypatch.setitem(conductor_mod.CONFIG, "CONDUCTOR", {"ENABLED": True, "VISION_ON_DIVERGENCE": False})
    sig = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "confluenceScore": 2.0,
        "maxScore": 3.0,
        "direction": "LONG",
        "engine_b": {"structural_verdict": "PASS"},
    }
    r = conductor_mod.route_ai_functions(
        sig,
        "TRENDING",
        volume_divergence={"divergence": True, "type": "bearish"},
        stop_run=None,
        news_risk=None,
        db_path=None,
    )
    assert r["run_vision"] is False


def test_run_marcus_false_when_config_off(monkeypatch):
    monkeypatch.setitem(
        conductor_mod.CONFIG,
        "CONDUCTOR",
        {"ENABLED": True, "RUN_MARCUS_ON_ANALYZE": False, "DEBATE_MAX_SCORE_PCT": 75},
    )
    sig = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "confluenceScore": 2.5,
        "maxScore": 3.0,
        "direction": "LONG",
        "engine_b": {"structural_verdict": "PASS"},
    }
    r = conductor_mod.route_ai_functions(sig, "TRENDING", db_path=None)
    assert r["run_debate"] is False
    assert r["run_marcus"] is False


def test_skip_debate_above_band_without_strong_score_gap(monkeypatch):
    """Score above DEBATE_MAX + clear B skips debate without needing STRONG_SCORE_PCT."""
    monkeypatch.setitem(
        conductor_mod.CONFIG,
        "CONDUCTOR",
        {"ENABLED": True, "DEBATE_MIN_SCORE_PCT": 50, "DEBATE_MAX_SCORE_PCT": 75, "HARD_FAIL_SCORE_PCT": 35},
    )
    sig = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "confluenceScore": 2.31,
        "maxScore": 3.0,
        "direction": "LONG",
        "engine_b": {"structural_verdict": "PASS"},
    }
    r = conductor_mod.route_ai_functions(sig, "TRENDING", db_path=None)
    assert r["score_pct"] == pytest.approx(77.0, rel=0, abs=0.1)
    assert r["run_debate"] is False


def test_news_risk_routes_sentiment(monkeypatch):
    monkeypatch.setitem(
        conductor_mod.CONFIG,
        "CONDUCTOR",
        {"ENABLED": True, "SENTIMENT_ON_NEWS": True, "DYNAMIC_WEIGHTING_ENABLED": False},
    )
    sig = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "confluenceScore": 2.0,
        "maxScore": 3.0,
        "direction": "LONG",
    }

    r = conductor_mod.route_ai_functions(sig, "TRENDING", news_risk="FOMC risk", db_path=None)

    assert r["run_sentiment"] is True
    assert r["advisoryOnly"] is True
    assert r["notUsedForExecution"] is True


def test_dynamic_weights_disabled_does_not_query_learning_log(monkeypatch):
    calls = {"perf": 0}

    def fake_perf(*_args, **_kwargs):
        calls["perf"] += 1
        return {"sample_size": 99, "engine_a_wr": 1.0, "engine_b_wr": 0.0}

    monkeypatch.setattr(conductor_mod, "_get_recent_performance", fake_perf)
    monkeypatch.setitem(
        conductor_mod.CONFIG,
        "CONDUCTOR",
        {
            "ENABLED": True,
            "DYNAMIC_WEIGHTING_ENABLED": False,
            "DIAGNOSTIC_WEIGHTS_ENABLED": False,
            "MIN_SAMPLE_SIZE": 1,
        },
    )
    sig = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "confluenceScore": 2.0,
        "maxScore": 3.0,
        "direction": "LONG",
    }

    r = conductor_mod.route_ai_functions(sig, "TRENDING", db_path="audit.db")

    assert calls["perf"] == 0
    assert r["engine_weights"] == conductor_mod.DEFAULT_REGIME_WEIGHTS["TRENDING"]
    assert r["engineWeightsAdvisoryOnly"] is True


def test_diagnostic_weights_are_marked_advisory(monkeypatch):
    monkeypatch.setattr(
        conductor_mod,
        "_get_recent_performance",
        lambda *_args, **_kwargs: {
            "sample_size": 20,
            "engine_a_wr": 1.0,
            "engine_b_wr": 0.0,
        },
    )
    monkeypatch.setitem(
        conductor_mod.CONFIG,
        "CONDUCTOR",
        {
            "ENABLED": True,
            "DYNAMIC_WEIGHTING_ENABLED": False,
            "DIAGNOSTIC_WEIGHTS_ENABLED": True,
            "DYNAMIC_WEIGHT_BLEND": 0.5,
            "MIN_SAMPLE_SIZE": 1,
        },
    )
    sig = {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "confluenceScore": 2.0,
        "maxScore": 3.0,
        "direction": "LONG",
    }

    r = conductor_mod.route_ai_functions(sig, "TRENDING", db_path="audit.db")

    assert r["engine_weights"] == conductor_mod.DEFAULT_REGIME_WEIGHTS["TRENDING"]
    assert r["engine_weights_advisory"]["engine_a"] > r["engine_weights"]["engine_a"]
    assert r["notUsedForExecution"] is True
