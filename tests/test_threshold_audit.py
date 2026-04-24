import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk_engine import risk_check
from threshold_audit import (
    REQUIRED_FIELDS,
    audit_enabled,
    build_signal_funnel_row,
    write_signal_funnel_rows,
)
from tools.threshold_audit_report import build_report


def _pair():
    return {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex", "enabled": True}


def _signal(score=2.0):
    return {
        "pair": "EUR/USD",
        "display": "EUR/USD",
        "symbol": "EURUSD",
        "type": "forex",
        "direction": "LONG",
        "confluenceScore": score,
        "maxScore": 3.0,
        "scoreNorm": round(score / 3.0, 4),
        "price": 1.10,
        "sl": 1.09,
        "timestamp": "2026-04-24T10:00:00+00:00",
        "factorScores": {
            "trend": 2.4,
            "momentum": 0.6,
            "addon": 0.0,
            "adx_value": 18.0,
            "adx_multiplier": 0.65,
            "session_multiplier": 0.75,
        },
        "factorDiagnostics": {},
        "scanDiagnostics": [{"code": "low_confluence", "detail": "Below threshold"}],
        "_threshold_audit_b_res": {"structural_verdict": "CLEAR", "direction": "LONG"},
        "_threshold_audit_b_conf": {
            "score": 2.5,
            "max_possible": 5.0,
            "passed": False,
            "structure_ok": True,
            "location_ok": True,
            "entry_ok": False,
            "room_ok": True,
            "rr_ok": False,
            "engine_b_diagnostics": {"reason_codes": ["no_trigger_pattern"]},
        },
        "_threshold_audit_b_threshold": 3.0,
    }


def test_signal_funnel_logging_writes_required_fields():
    row = build_signal_funnel_row(_pair(), _signal(), tier="skip", tier_reason="Below discovery threshold")
    out = Path("tests") / "_artifacts" / "threshold_audit_test.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    write_signal_funnel_rows([row], out)
    loaded = json.loads(out.read_text(encoding="utf-8").strip())
    missing = [field for field in REQUIRED_FIELDS if field not in loaded]
    assert missing == []
    assert loaded["risk_check_allowed"] is False
    assert loaded["risk_check_fail_reasons"] == ["not_evaluated_threshold_audit_report_only"]


def test_threshold_report_handles_empty_no_signal_scans():
    report = build_report([])
    assert "Total scanned symbols: 0" in report
    assert "do not change yet" in report


def test_near_miss_classification_works():
    row = build_signal_funnel_row(_pair(), _signal(score=2.0), tier="skip")
    assert row["final_scan_result"] == "A_NEAR_MISS"


def test_shadow_thresholds_do_not_affect_execution_decisions():
    sig = _signal(score=2.0)
    row = build_signal_funnel_row(_pair(), sig, tier="skip")
    before = sig.get("confluenceScore")
    assert row["shadow_thresholds"]["ENGINE_A"]["current_minus_10pct"] < row["thresholds"]["engine_a"]
    assert sig.get("confluenceScore") == before
    assert "shadow_thresholds" not in sig


def test_fail_reason_counts_are_reported():
    rows = [build_signal_funnel_row(_pair(), _signal(score=1.9), tier="skip")]
    report = build_report(rows)
    assert "below_engine_a_threshold" in report
    assert "engine_b_confidence_passed_false" in report
    assert "no_trigger_pattern" in report


def test_threshold_audit_mode_does_not_change_freshness_or_risk_gates():
    row = build_signal_funnel_row(_pair(), _signal(score=2.0), tier="skip")
    assert row["risk_check_fail_reasons"] == ["not_evaluated_threshold_audit_report_only"]
    approval = risk_check(
        {
            "pair": "EUR/USD",
            "type": "forex",
            "direction": "LONG",
            "price": 1.10,
            "sl": 1.09,
            "timestamp": "2020-01-01T00:00:00+00:00",
            "confluenceScore": 2.4,
            "maxScore": 3.0,
        },
        account_balance=10000,
        account_equity=10000,
        open_positions=[],
        kill_switch=False,
    )
    assert approval.approved is False
    assert approval.reason in {"STALE_SIGNAL", "STALE_DATA_BLOCK", "MISSING_CANDLE_FRESHNESS"}


def test_threshold_audit_env_override_enables_without_default_config_change(monkeypatch):
    monkeypatch.setenv("ATHENA_THRESHOLD_AUDIT", "1")
    assert audit_enabled() is True
