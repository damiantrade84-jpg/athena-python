"""Tests for Scalp Workbench backend contracts."""

from scalp_workbench_contracts import (
    attach_scalp_workbench_extensions,
    build_scalp_advisory_context,
    derive_candidate_phase,
    map_setup_model,
)


def test_workbench_contracts_advisory_maps_old_blockers():
    row = {
        "direction": "LONG",
        "ai_grade": "B",
        "executable": False,
        "gate_result": "WATCHLIST",
        "strict_fabio_pass": False,
        "rr_ok": False,
        "fee_guard": {"high_cost": True},
        "sourceContract": {"strictOrderflowSourcePass": False, "warnings": []},
    }
    advisory = build_scalp_advisory_context(row, source_contract=row["sourceContract"])
    assert "old_executable" in advisory["warnings"]
    assert "strict_fabio_failed" in advisory["warnings"]
    assert "rr_below_min" in advisory["warnings"]
    assert "fee_guard_high_cost" in advisory["warnings"]
    assert "proxy_orderflow" in advisory["warnings"]


def test_workbench_contracts_candidate_phase_ai_review_for_grade_ab():
    assert derive_candidate_phase({"ai_grade": "A", "direction": "LONG"}) == "AI_REVIEW_REQUIRED"
    assert derive_candidate_phase({"ai_grade": "B", "direction": "SHORT", "gate_result": "WATCHLIST"}) == "AI_REVIEW_REQUIRED"
    assert derive_candidate_phase({"ai_grade": "D", "direction": "LONG"}) == "CONTEXT_ONLY"


def test_workbench_contracts_setup_model_mapping():
    assert map_setup_model({"zone_type": "mean_reversion", "location": "at_poc"}) == "BALANCE_MEAN_REVERSION_TO_POC"
    assert map_setup_model({"sweep_detected": True, "zone_type": "reversal"}) == "SWEEP_RECLAIM_REVERSAL"


def test_attach_scalp_workbench_extensions_adds_liquidity_and_phase():
    row = attach_scalp_workbench_extensions(
        {
            "direction": "LONG",
            "ai_grade": "B",
            "marketLocation": {"poc": 100.0, "vah": 101.0, "val": 99.0, "lvnLevels": [], "hvnLevels": []},
            "profile_anchor_shadow": {"candidates": {"prior_session": {"valid": True, "high": 102.0, "low": 98.0}}},
        },
        source_contract={"strictOrderflowSourcePass": True, "warnings": [], "unavailableReasons": []},
    )
    assert "liquidityContext" in row
    assert row["liquidityContext"]["priorDayHigh"] == 102.0
    assert row["candidatePhase"] == "AI_REVIEW_REQUIRED"
    assert row["setupModel"]
