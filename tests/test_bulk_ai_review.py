"""Tests for bulk_ai_review composer + ai_review.bulk_persistence.

Uses DI fakes (no LLM, no scanner) mirroring tests/test_cascade_scan.py style.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bulk_ai_review as bar
from ai_review import bulk_persistence

ATHENA_PATH = Path(__file__).resolve().parents[1] / "athena.py"
BULK_PATH = Path(__file__).resolve().parents[1] / "bulk_ai_review.py"


def _candidate(symbol: str, confluence: float = 3.0, rr: float = 2.0) -> dict:
    return {
        "symbol": symbol,
        "direction": "LONG",
        "confluenceScore": confluence,
        "rr": rr,
        "shortlistRank": 1,
        "regime": "TRENDING",
    }


def _cascade_fn(candidates):
    def fn(**kwargs):
        return {
            "success": True,
            "scanRunId": "run123",
            "universeCount": 50,
            "hardBlockedCount": 2,
            "candidates": candidates,
        }

    return fn


def _ai_fn(grades: dict):
    calls = []

    def fn(signal):
        calls.append(signal)
        pair = signal["pair"]
        out = dict(grades.get(pair) or {"grade": "C"})
        return out

    fn.calls = calls
    return fn


def setup_function(_fn):
    bar._REVIEW_CACHE.clear()


def test_only_a_and_b_grades_in_results_and_all_persisted():
    candidates = [_candidate(s) for s in ("AAA", "BBB", "CCC", "DDD")]
    grades = {
        "AAA": {"grade": "A", "edgeProbability": 80},
        "BBB": {"grade": "B", "edgeProbability": 65},
        "CCC": {"grade": "C"},
        "DDD": {"grade": "F"},
    }
    persisted = []
    result = bar.run_bulk_ai_review(
        run_cascade_scan_fn=_cascade_fn(candidates),
        run_ai_fn=_ai_fn(grades),
        persist_fn=persisted.append,
    )
    assert result["success"] is True
    assert result["scanRunId"] == "run123"
    assert [r["symbol"] for r in result["results"]] == ["AAA", "BBB"]
    assert {r["symbol"] for r in result["filteredOut"]} == {"CCC", "DDD"}
    assert len(persisted) == 4
    assert result["summary"]["reviewed"] == 4
    assert result["summary"]["aGrades"] == 1
    assert result["summary"]["bGrades"] == 1


def test_a_plus_grade_passes():
    result = bar.run_bulk_ai_review(
        run_cascade_scan_fn=_cascade_fn([_candidate("XAU")]),
        run_ai_fn=_ai_fn({"XAU": {"grade": "A+"}}),
    )
    assert [r["symbol"] for r in result["results"]] == ["XAU"]


def test_safe_default_excluded_flagged_and_not_cached():
    ai = _ai_fn({"EURUSD": {"grade": "F", "error": True}})
    persisted = []
    result = bar.run_bulk_ai_review(
        run_cascade_scan_fn=_cascade_fn([_candidate("EURUSD")]),
        run_ai_fn=ai,
        persist_fn=persisted.append,
    )
    assert result["results"] == []
    assert result["filteredOut"][0]["isSafeDefault"] is True
    assert result["summary"]["safeDefaults"] == 1
    assert persisted[0]["is_safe_default"] is True
    assert bar._REVIEW_CACHE == {}
    # Second run re-calls the AI (no poisoned cache).
    bar.run_bulk_ai_review(
        run_cascade_scan_fn=_cascade_fn([_candidate("EURUSD")]),
        run_ai_fn=ai,
    )
    assert len(ai.calls) == 2


def test_cache_hit_within_ttl_skips_ai_and_expires_after_ttl():
    ai = _ai_fn({"GBPUSD": {"grade": "A"}})
    clock = {"now": 0.0}

    def now_fn():
        return clock["now"]

    kwargs = dict(
        run_cascade_scan_fn=_cascade_fn([_candidate("GBPUSD")]),
        run_ai_fn=ai,
        cache_ttl_s=3600,
        now_fn=now_fn,
    )
    r1 = bar.run_bulk_ai_review(**kwargs)
    assert len(ai.calls) == 1 and r1["summary"]["cacheHits"] == 0
    clock["now"] = 100.0
    r2 = bar.run_bulk_ai_review(**kwargs)
    assert len(ai.calls) == 1 and r2["summary"]["cacheHits"] == 1
    assert r2["results"][0]["cacheHit"] is True
    clock["now"] = 4000.0
    r3 = bar.run_bulk_ai_review(**kwargs)
    assert len(ai.calls) == 2 and r3["summary"]["cacheHits"] == 0


def test_budget_exhaustion_skips_remaining():
    candidates = [_candidate(s) for s in ("S1", "S2", "S3")]
    clock = {"now": 0.0}

    def now_fn():
        return clock["now"]

    def slow_ai(signal):
        clock["now"] += 60.0
        return {"grade": "A"}

    result = bar.run_bulk_ai_review(
        run_cascade_scan_fn=_cascade_fn(candidates),
        run_ai_fn=slow_ai,
        budget_s=120.0,
        now_fn=now_fn,
    )
    assert result["summary"]["reviewed"] == 2
    assert result["summary"]["skippedBudget"] == 1
    assert result["skipped"] == [{"symbol": "S3", "reason": "skipped_budget"}]


def test_top_n_clamped_and_default():
    seen = {}

    def cascade_fn(**kwargs):
        seen.update(kwargs)
        return {"success": True, "scanRunId": "x", "candidates": []}

    bar.run_bulk_ai_review(
        top_n=99, run_cascade_scan_fn=cascade_fn, run_ai_fn=_ai_fn({})
    )
    assert seen["top_n"] == bar.MAX_TOP_N
    bar.run_bulk_ai_review(
        top_n=None, run_cascade_scan_fn=cascade_fn, run_ai_fn=_ai_fn({})
    )
    assert seen["top_n"] == bar.DEFAULT_TOP_N


def test_default_rules_zero_cascade_thresholds_and_are_overridable():
    seen = {}

    def cascade_fn(**kwargs):
        seen.update(kwargs)
        return {"success": True, "scanRunId": "x", "candidates": []}

    bar.run_bulk_ai_review(run_cascade_scan_fn=cascade_fn, run_ai_fn=_ai_fn({}))
    assert seen["rules"] == {"min_confluence": 0.0, "min_rr": 0.0}
    bar.run_bulk_ai_review(
        rules={"min_rr": 1.2},
        run_cascade_scan_fn=cascade_fn,
        run_ai_fn=_ai_fn({}),
    )
    assert seen["rules"] == {"min_confluence": 0.0, "min_rr": 1.2}


def test_busy_or_failed_cascade_passes_through():
    def busy_fn(**kwargs):
        return {"success": False, "busy": True, "error": "Scan already in progress"}

    result = bar.run_bulk_ai_review(
        run_cascade_scan_fn=busy_fn, run_ai_fn=_ai_fn({})
    )
    assert result["success"] is False and result["busy"] is True


def test_persistence_round_trip_and_filters():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "audit_test.db")
    bulk_persistence.ensure_schema(db)
    base = {
        "scan_run_id": "runA",
        "symbol": "EURUSD",
        "pair": "EURUSD",
        "direction": "LONG",
        "asset_class": "forex",
        "style": "auto",
        "confluence_score": 3.1,
        "rr": 2.2,
        "grade": "A",
        "edge_probability": 78,
        "risk_level": "Medium",
        "verdict": "Solid setup",
        "review": {"grade": "A", "verdict": "Solid setup"},
        "is_safe_default": False,
        "cache_hit": False,
        "provider": "grok",
        "model": "grok-4.3",
        "latency_ms": 1200,
    }
    bulk_persistence.insert_review(base, audit_db=db)
    bulk_persistence.insert_review(
        {**base, "symbol": "GBPUSD", "pair": "GBPUSD", "grade": "C"}, audit_db=db
    )
    bulk_persistence.insert_review(
        {**base, "scan_run_id": "runB", "grade": "B"}, audit_db=db
    )

    all_rows = bulk_persistence.list_reviews(audit_db=db)
    assert len(all_rows) == 3
    a_rows = bulk_persistence.list_reviews(grade="a", audit_db=db)
    assert len(a_rows) == 1 and a_rows[0]["symbol"] == "EURUSD"
    assert a_rows[0]["review"]["verdict"] == "Solid setup"
    run_rows = bulk_persistence.list_reviews(scan_run_id="runA", audit_db=db)
    assert len(run_rows) == 2

    runs = bulk_persistence.list_runs(audit_db=db)
    assert {r["scan_run_id"] for r in runs} == {"runA", "runB"}
    run_a = next(r for r in runs if r["scan_run_id"] == "runA")
    assert run_a["reviewed"] == 2 and run_a["a_grades"] == 1


def test_bulk_module_has_no_execution_or_chart_ai_calls():
    src = BULK_PATH.read_text(encoding="utf-8")
    forbidden = [
        "api_chart_analysis",
        "chart_analysis",
        "execute_trade",
        "mt5_send_order",
        "bybit_send_order",
        "place_order",
        "open_position",
        "close_position",
        "risk_engine",
        "guardian",
    ]
    for token in forbidden:
        assert token not in src


def test_bulk_route_has_no_execution_calls_and_respects_kill_switch():
    src = ATHENA_PATH.read_text(encoding="utf-8")
    route_start = src.index("def api_bulk_ai_review(")
    route_end = src.index("\n@app.route", route_start + 1)
    route_src = src[route_start:route_end]
    assert "_kill_switch" in route_src
    for token in (
        "execute_trade",
        "mt5_send_order",
        "bybit_send_order",
        "place_order",
        "open_position",
        "close_position",
    ):
        assert token not in route_src
