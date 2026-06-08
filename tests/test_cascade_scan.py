from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.route_contract_helpers import endpoint_map_from_files


ROOT = Path(__file__).resolve().parents[1]
ATHENA_PATH = ROOT / "athena.py"
CASCADE_PATH = ROOT / "cascade_scan.py"
CONTRACT_DOC_PATH = ROOT / "docs" / "cascade_scan_contract.md"


def _fresh_meta(source: str = "mt5") -> dict:
    return {
        "D1": {"stalenessSeverity": "fresh"},
        "H4": {"stalenessSeverity": "fresh"},
        "H1": {"stalenessSeverity": "fresh"},
        "pairSource": source,
    }


def _freshness(reason: str = "fresh") -> dict:
    return {"allowed": True, "reason": reason, "blocked": []}


def _atr_diag(source: str = "mt5") -> dict:
    return {
        "atr_value": 0.0012,
        "atr_tf": "H4",
        "atr_source": source,
        "atr_source_engine": "engine_a",
        "atr_confirmed_only": True,
    }


def _signal(symbol: str, **overrides) -> dict:
    base = {
        "pair": symbol,
        "display": symbol,
        "symbol": symbol.replace("/", ""),
        "type": "forex",
        "style": "intraday",
        "direction": "LONG",
        "confluenceScore": 2.4,
        "engineAVerdict": "TRADE_READY",
        "rr": 2.0,
        "regime": "TRENDING",
        "session": {"name": "London"},
        "spreadStatus": "ok",
        "engineAReasons": ["trend aligned"],
        "warnings": [],
        "dataFreshness": _freshness(),
        "candleFetchMeta": _fresh_meta(),
        "atrDiagnostics": _atr_diag(),
        "atrFreshness": {"enabled": True, "stale": False, "reason": "fresh"},
        "factorScores": {"trend": 0.82},
        "factorDiagnostics": {
            "trendCoherence": {
                "coherence_ratio": 0.75,
                "agreement_count": 2,
                "tf_coverage": 0.67,
                "trend_timeframes": ["D1", "H4", "H1"],
            },
            "feedStatus": {"session": "disabled"},
        },
    }
    base.update(overrides)
    return base


def _scan_result(*signals: dict, watchlist: list[dict] | None = None) -> dict:
    return {
        "success": True,
        "totalPairs": 12,
        "activePairs": 10,
        "signals": list(signals),
        "tradeSignals": list(signals),
        "watchlist": list(watchlist or []),
        "errors": [],
        "skipped": [],
    }


def test_run_cascade_scan_reuses_run_full_scan_and_emits_review_contract():
    from cascade_scan import run_cascade_scan

    calls: list[tuple[str, str]] = []

    def fake_run_full_scan(*, style: str, asset_class: str):
        calls.append((style, asset_class))
        return _scan_result(_signal("EUR/USD"))

    out = run_cascade_scan(
        asset_class="forex",
        style="intraday",
        enable_triage=False,
        top_n=5,
        run_full_scan_fn=fake_run_full_scan,
    )

    assert calls == [("intraday", "forex")]
    assert out["success"] is True
    assert re.fullmatch(r"[0-9a-f]{32}", out["scanRunId"])
    assert out["assetClass"] == "forex"
    assert out["universeCount"] == 12
    assert out["engineACandidateCount"] == 1
    assert out["shortlistCount"] == 1
    assert out["triageEnabled"] is False

    candidate = out["candidates"][0]
    assert candidate["analysisTimeframes"] == ["D1", "H4", "H1"]
    assert candidate["displayTimeframe"] == "D1/H4/H1"
    assert candidate["reviewTimeframe"] == "H1"
    assert candidate["readyForChartReview"] is True
    assert candidate["reviewAction"] == "OPEN_CHART_REVIEW"
    assert candidate["chartAiStatus"] == "NOT_RUN"
    assert candidate["actionState"] == "READY_FOR_CHART_REVIEW"


def test_run_cascade_scan_busy_response_is_clean():
    from cascade_scan import run_cascade_scan

    def busy_scan(**_kwargs):
        return {"success": False, "error": "Scan already in progress"}

    out = run_cascade_scan(run_full_scan_fn=busy_scan)

    assert out == {
        "success": False,
        "busy": True,
        "error": "Scan already in progress",
    }


def test_build_shortlist_drops_hard_filter_failures():
    from cascade_scan import build_shortlist

    stale = _signal(
        "STALE",
        dataFreshness={"allowed": False, "reason": "H4_stale", "blocked": ["H4"]},
    )
    blocked = _signal(
        "BLOCKED",
        dataFreshness={"allowed": False, "reason": "blocked_by_quality", "blocked": ["D1"]},
    )
    low_score = _signal("LOW", confluenceScore=1.0)
    bad_rr = _signal("BADRR", rr=1.1)
    missing_direction = _signal("NODIR")
    missing_direction.pop("direction")
    missing_atr = _signal("NOATR")
    missing_atr.pop("atrDiagnostics")
    good = _signal("GOOD", confluenceScore=2.6, rr=2.2)

    shortlist, hard_blocked, hard_count = build_shortlist(
        _scan_result(stale, blocked, low_score, bad_rr, missing_direction, missing_atr, good),
        rules={"min_confluence": 2.0, "min_rr": 1.5},
        asset_class="forex",
    )

    assert [row["symbol"] for row in shortlist] == ["GOOD"]
    assert hard_count == 6
    assert {row["symbol"] for row in hard_blocked} == {
        "STALE",
        "BLOCKED",
        "LOW",
        "BADRR",
        "NODIR",
        "NOATR",
    }


def test_build_shortlist_reads_nested_coherence_and_missing_defaults_to_not_available():
    from cascade_scan import build_shortlist

    nested = _signal(
        "NESTED",
        factorDiagnostics={
            "trendCoherence": {
                "coherence_ratio": 0.91,
                "agreement_count": 3,
                "tf_coverage": 1.0,
                "trend_timeframes": ["D1", "H4", "H1"],
            }
        },
    )
    missing = _signal("MISSING", confluenceScore=2.3, factorDiagnostics={})

    shortlist, _, _ = build_shortlist(_scan_result(nested, missing), rules={}, asset_class="forex")
    by_symbol = {row["symbol"]: row for row in shortlist}

    assert by_symbol["NESTED"]["coherenceScore"] == pytest.approx(0.91)
    assert by_symbol["NESTED"]["coherenceReason"] == "trendCoherence.coherence_ratio"
    assert by_symbol["MISSING"]["coherenceScore"] == 0
    assert by_symbol["MISSING"]["coherenceReason"] == "not_available"


def test_ranking_is_deterministic_and_source_fidelity_is_only_tiebreaker():
    from cascade_scan import build_shortlist

    low_fidelity_fresh = _signal(
        "BBB",
        candleFetchMeta=_fresh_meta("fallback_yfinance"),
        dataFreshness=_freshness(),
        confluenceScore=2.5,
        rr=2.0,
    )
    high_fidelity = _signal("AAA", confluenceScore=2.5, rr=2.0)
    lower_score = _signal("CCC", confluenceScore=2.4, rr=3.0)

    first, _, _ = build_shortlist(
        _scan_result(low_fidelity_fresh, lower_score, high_fidelity),
        rules={"min_confluence": 2.0, "min_rr": 1.5},
        asset_class="forex",
    )
    second, _, _ = build_shortlist(
        _scan_result(low_fidelity_fresh, lower_score, high_fidelity),
        rules={"min_confluence": 2.0, "min_rr": 1.5},
        asset_class="forex",
    )

    assert [row["symbol"] for row in first] == [row["symbol"] for row in second]
    assert [row["symbol"] for row in first] == ["AAA", "BBB", "CCC"]
    assert first[1]["sourceFidelity"] == {
        "score": pytest.approx(0.45),
        "pairSource": "fallback_yfinance",
        "dataFreshness": "fresh",
        "derived": True,
    }
    assert first[1]["shortlistPassed"] is True


def test_forex_session_is_ranking_neutral_and_ranging_is_not_default_blocked():
    from cascade_scan import build_shortlist

    ranging = _signal("RANGE", regime="RANGING")

    shortlist, _, _ = build_shortlist(_scan_result(ranging), rules={}, asset_class="forex")

    assert len(shortlist) == 1
    assert shortlist[0]["sessionQualityScore"] == 0
    assert shortlist[0]["sessionQualityReason"] == "neutral_for_forex"
    assert shortlist[0]["regime"] == "RANGING"


def test_triage_noops_without_client_and_rejects_execution_verdicts():
    from cascade_scan import build_shortlist, triage_candidates

    candidate, _, _ = build_shortlist(_scan_result(_signal("EUR/USD")), rules={}, asset_class="forex")
    candidate = candidate[0]

    no_client = triage_candidates([candidate], client=None)
    assert no_client[0]["triageVerdict"] is None
    assert no_client[0]["triageReason"] is None
    assert no_client[0]["triageConfidence"] is None

    captured = {}

    def bad_client(payload):
        captured["payload"] = payload
        return {
            "triageVerdict": "TRADE_NOW",
            "priority": 1,
            "reason": "execute immediately",
            "confidence": 0.99,
        }

    triaged = triage_candidates([candidate], client=bad_client)

    assert triaged[0]["triageVerdict"] == "SKIP"
    assert triaged[0]["triageReason"] == "invalid_triage_verdict"
    assert triaged[0]["triageConfidence"] == 0.0
    assert "screenshot" not in str(captured["payload"]).lower()
    assert "vision" not in str(captured["payload"]).lower()
    assert set(captured["payload"][0]) == {
        "symbol",
        "direction",
        "displayTimeframe",
        "confluenceScore",
        "trendScore",
        "regime",
        "session",
        "spreadStatus",
        "rr",
        "engineAReasons",
        "warnings",
    }


def test_cascade_endpoint_contract_is_static_and_does_not_call_vision_or_execution():
    ep = endpoint_map_from_files([ATHENA_PATH])
    assert "/api/cascade-scan" in ep and "POST" in ep["/api/cascade-scan"]

    src = ATHENA_PATH.read_text(encoding="utf-8")
    route_start = src.index('def api_cascade_scan')
    route_end = src.index("\n@app.route", route_start + 1)
    route_src = src[route_start:route_end]

    forbidden = [
        "api_chart_analysis",
        "/api/chart-analysis",
        "execute_trade",
        "mt5_send_order",
        "bybit_send_order",
        "place_order",
        "open_position",
        "close_position",
    ]
    for token in forbidden:
        assert token not in route_src


def test_engine_a_scan_payload_exposes_top_level_rr_for_cascade_contract():
    src = ATHENA_PATH.read_text(encoding="utf-8")

    assert '"rr1": round(float(lvl["rr1"]), 2),' in src
    assert '"rr2": round(float(lvl["rr2"]), 2),' in src
    assert '"rr": round(float(lvl["rr2"]), 2),' in src


def test_cascade_module_has_no_execution_or_chart_ai_calls():
    src = CASCADE_PATH.read_text(encoding="utf-8")
    forbidden = [
        "api_chart_analysis",
        "chart_analysis",
        "execute_trade",
        "mt5_send_order",
        "bybit_send_order",
        "place_order",
        "open_position",
        "close_position",
    ]
    for token in forbidden:
        assert token not in src


def test_contract_doc_describes_frontend_fields_and_button_behaviors():
    text = CONTRACT_DOC_PATH.read_text(encoding="utf-8")

    for field in (
        "scanRunId",
        "analysisTimeframes",
        "displayTimeframe",
        "reviewTimeframe",
        "sourceFidelity",
        "coherenceScore",
        "actionState",
        "readyForChartReview",
        "chartAiStatus",
    ):
        assert field in text
    assert "Open chart" in text
    assert "Review with chart AI" in text
    assert "Re-run fresh ENTRY_NOW review" in text
    assert "server-side Vision" in text
