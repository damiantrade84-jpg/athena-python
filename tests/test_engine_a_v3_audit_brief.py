from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config import CONFIG
from engine_a_v3.audit import (
    AUDIT_BRIEF_VERSION,
    AUDIT_REPRESENTATIVE_PAIRS,
    V3_BASELINE_TRADE_THRESHOLD,
    audit_entry_points,
    audit_execution_gates,
    audit_score_group_parity,
    audit_threshold_layers,
    audit_validation_lanes,
    run_audit_brief,
)
from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.promotion import demo_unvalidated_registry
from engine_a_v3.routing import KNOWN_SCORE_GROUPS
from scoring import get_pair_score_group, get_score_threshold


def _candles(
    *,
    count: int,
    start: datetime,
    step: timedelta,
    base: float,
    slope: float,
) -> list[dict]:
    rows = []
    for idx in range(count):
        close = base + slope * idx
        open_ = close - (0.3 if slope >= 0 else -0.3)
        rows.append(
            {
                "time": (start + step * idx).isoformat(),
                "open": open_,
                "high": max(open_, close) + 0.5,
                "low": min(open_, close) - 0.5,
                "close": close,
                "vol": 1_000 + idx,
            }
        )
    return rows


def _trend_pullback_candles() -> dict[str, list[dict]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    d1 = _candles(count=260, start=start, step=timedelta(days=1), base=80, slope=0.2)
    h4 = _candles(count=300, start=start, step=timedelta(hours=4), base=90, slope=0.12)
    h1 = _candles(count=320, start=start, step=timedelta(hours=1), base=100, slope=0.05)
    prior_close = h1[-2]["close"]
    h1[-2].update(
        {
            "open": prior_close + 0.8,
            "high": prior_close + 1.0,
            "low": prior_close - 1.2,
            "close": prior_close - 0.6,
        }
    )
    h1[-1].update(
        {
            "open": prior_close - 0.5,
            "high": prior_close + 1.2,
            "low": prior_close - 0.8,
            "close": prior_close + 0.9,
        }
    )
    return {"D1": d1, "H4": h4, "H1": h1}


def test_audit_entry_points_passes():
    section = audit_entry_points()
    assert section.status == "PASS"
    assert any(check.id == "public_evaluator_export" for check in section.checks)
    assert any(check.id == "v3_no_legacy_threshold_imports" for check in section.checks)


def test_audit_threshold_layers_v3_uniform_and_legacy_separate():
    section = audit_threshold_layers(config=CONFIG)
    assert section.status in {"PASS", "WARN"}

    baseline_check = next(c for c in section.checks if c.id == "v3_baseline_uniform_2_0")
    assert baseline_check.status == "PASS"
    assert all(
        abs(value - V3_BASELINE_TRADE_THRESHOLD) < 1e-9
        for value in baseline_check.evidence["thresholds"].values()
    )

    legacy_check = next(c for c in section.checks if c.id == "legacy_threshold_map_present")
    assert legacy_check.status == "PASS"

    pair = AUDIT_REPRESENTATIVE_PAIRS["forex_majors"]
    assert get_score_threshold(pair) != V3_BASELINE_TRADE_THRESHOLD


def test_audit_execution_gates_on_evaluated_v3_signal():
    pair = AUDIT_REPRESENTATIVE_PAIRS["forex_majors"]
    signal = evaluate_engine_a_v3(
        pair,
        _trend_pullback_candles(),
        horizon="intraday",
        registry=demo_unvalidated_registry(),
    ).to_dict()

    section = audit_execution_gates(signal, pair, config=CONFIG)
    assert section.status in {"PASS", "WARN"}
    assert all(
        check.status in {"PASS", "WARN"}
        for check in section.checks
        if check.id != "trade_gate_research_only_by_default"
    )


def test_audit_score_group_parity_all_representative_pairs():
    section = audit_score_group_parity()
    assert section.status == "PASS"
    assert len(AUDIT_REPRESENTATIVE_PAIRS) == len(KNOWN_SCORE_GROUPS)
    for group, pair in AUDIT_REPRESENTATIVE_PAIRS.items():
        assert get_pair_score_group(pair) == group


def test_audit_validation_lanes_five_enabled_twenty_one_unvalidated():
    section = audit_validation_lanes()
    assert section.status in {"PASS", "WARN"}

    enabled = next(c for c in section.checks if c.id == "five_validation_enabled_lanes")
    assert enabled.status == "PASS"
    assert set(enabled.evidence["validation_enabled"]) == {
        "crypto_btc",
        "crypto_eth",
        "crypto_doge",
        "forex_majors",
        "forex_crosses",
    }

    unvalidated = next(c for c in section.checks if c.id == "twenty_one_unvalidated_lanes")
    assert unvalidated.status == "PASS"
    assert unvalidated.evidence["unvalidated_count"] == 21


def test_run_audit_brief_end_to_end():
    pair = AUDIT_REPRESENTATIVE_PAIRS["crypto_btc"]
    report = run_audit_brief(
        pair=pair,
        candles=_trend_pullback_candles(),
        horizon="intraday",
        config=CONFIG,
    )
    assert report["auditBriefVersion"] == AUDIT_BRIEF_VERSION
    assert report["overall"] in {"PASS", "WARN"}
    assert "audit_entry" in report["sections"]
    assert "threshold_layer" in report["sections"]
    assert "group_coverage" in report["sections"]
    assert "validation_lanes" in report["sections"]
    assert "exec_gates" in report["sections"]
    assert report["signalSample"]["confluenceThreshold"] == V3_BASELINE_TRADE_THRESHOLD
