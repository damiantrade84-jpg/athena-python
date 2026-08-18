"""Live Cockpit card state: Engine D must not mask Engine A/B, and trade grading.

Regression guard for the defect where a missing Engine D (scalp) cache entry
reported BLOCKED for every symbol, hiding the real Engine A/B/C state beneath
it. Engines are independent: Engine D's own gates govern Engine D setups only.
"""

from __future__ import annotations

from athena_app.api.routes_live_dashboard import (
    _ld_final_state,
    _ld_trade_grade,
)

ALLOW = {"gateDecision": "ALLOW"}
BLOCK = {"gateDecision": "BLOCK", "blockReason": "stale_candles", "consistencyStatus": "STALE"}
D_MISSING = {"gateResult": "DATA_MISSING", "missingData": ["engine_d_cache_missing"]}
D_PASS = {"gateResult": "PASS"}
# _ld_has_valid_levels requires a positive rr as well as entry/sl/tp.
GOOD_LEVELS_A = {"entry": 1.1, "sl": 1.09, "tp": 1.12, "tp1": 1.12, "rr": 2.0, "source": "engineA"}
GOOD_LEVELS_B = {"entry": 1.1, "sl": 1.09, "tp": 1.12, "tp1": 1.12, "rr": 2.0, "source": "engineB"}
GOOD_LEVELS_D = {"entry": 1.1, "sl": 1.09, "tp": 1.12, "tp1": 1.12, "rr": 2.0, "source": "engineD"}


# --- Engine D no longer masks Engine A/B/C ----------------------------------


def test_missing_engine_d_does_not_block_an_aligned_ab_setup():
    state, reason, block = _ld_final_state(
        {"decisionState": "ALIGNED"}, D_MISSING, ALLOW, GOOD_LEVELS_B
    )
    assert state == "PAPER CANDIDATE"
    assert reason == "Aligned paper candidate"
    assert block is None


def test_missing_engine_d_does_not_block_a_watchlist_ab_setup():
    state, reason, _ = _ld_final_state(
        {"decisionState": "A_ONLY", "reason": "Engine A only"}, D_MISSING, ALLOW, GOOD_LEVELS_A
    )
    assert state == "WATCHLIST"
    assert reason == "Engine A only"


def test_missing_engine_d_with_no_setup_reports_no_setup_not_blocked():
    """A pair with no signal at all is NO SETUP - 'BLOCKED' would be misinformation."""
    state, _, block = _ld_final_state({"decisionState": "NO_SETUP"}, D_MISSING, ALLOW, {})
    assert state == "NO SETUP"
    # The missing cache is still surfaced, as a diagnostic rather than a verdict.
    assert block == "engine_d_cache_missing"


def test_engine_d_gates_still_apply_to_engine_d_setups():
    state, reason, block = _ld_final_state(
        {"decisionState": "NO_SETUP"}, D_MISSING, ALLOW, GOOD_LEVELS_D
    )
    assert state == "BLOCKED"
    assert reason == "Engine D data missing"
    assert block == "engine_d_cache_missing"


def test_engine_d_blocked_still_blocks_its_own_setup():
    state, _, _ = _ld_final_state(
        {"decisionState": "NO_SETUP"}, {"gateResult": "BLOCKED", "failReasons": ["tvq"]},
        ALLOW, GOOD_LEVELS_D,
    )
    assert state == "BLOCKED"


def test_engine_d_watchlist_still_downgrades_its_own_setup():
    state, _, _ = _ld_final_state(
        {"decisionState": "NO_SETUP"}, {"gateResult": "WATCHLIST"}, ALLOW, GOOD_LEVELS_D
    )
    assert state == "WATCHLIST"


def test_engine_d_pass_is_still_a_paper_candidate():
    state, reason, _ = _ld_final_state(
        {"decisionState": "NO_SETUP"}, D_PASS, ALLOW, GOOD_LEVELS_D
    )
    assert state == "PAPER CANDIDATE"
    assert reason == "Engine D paper candidate"


# --- Gates that must NOT have been weakened ---------------------------------


def test_freshness_block_still_wins_over_everything():
    state, reason, _ = _ld_final_state(
        {"decisionState": "ALIGNED"}, D_PASS, BLOCK, GOOD_LEVELS_B
    )
    assert state == "BLOCKED"
    assert reason == "Freshness gate blocked"


def test_engine_c_blocked_still_blocks():
    state, _, _ = _ld_final_state(
        {"decisionState": "BLOCKED", "blockReason": "direction_conflict"},
        D_PASS, ALLOW, GOOD_LEVELS_B,
    )
    assert state == "BLOCKED"


def test_aligned_without_valid_levels_is_not_a_paper_candidate():
    state, _, _ = _ld_final_state({"decisionState": "ALIGNED"}, D_MISSING, ALLOW, {})
    assert state != "PAPER CANDIDATE"


def test_partial_levels_do_not_qualify():
    for partial in (
        {"entry": 1.1, "source": "engineB"},
        {"entry": 1.1, "sl": 1.09, "tp": 1.12, "source": "engineB"},          # no rr
        {"entry": 1.1, "sl": 1.09, "tp": 1.12, "rr": 0, "source": "engineB"},  # rr not > 0
        {"entry": 1.1, "sl": 1.1, "tp": 1.12, "rr": 2.0, "source": "engineB"},  # entry == sl
    ):
        state, _, _ = _ld_final_state({"decisionState": "ALIGNED"}, D_MISSING, ALLOW, partial)
        assert state != "PAPER CANDIDATE", partial


# --- Trade grading -----------------------------------------------------------


def _row(engine_a=None, engine_b=None, engine_d=None, freshness=None, levels=None):
    return {
        "engineA": engine_a or {},
        "engineB": engine_b or {},
        "engineD": engine_d or {},
        "freshness": freshness if freshness is not None else ALLOW,
        "levels": levels if levels is not None else GOOD_LEVELS_A,
    }


def test_engine_a_needs_decision_qualified_and_ready():
    ready = {"decision": "TRADE", "qualified": True, "entryReadiness": "READY"}
    grade = _ld_trade_grade(_row(engine_a=ready))
    assert grade["isTradeGrade"] is True
    assert "engineA_trade_ready" in grade["engines"]


def test_engine_a_trade_without_ready_is_not_trade_grade():
    """decision=TRADE and a high score are not executable proof on their own."""
    for partial in (
        {"decision": "TRADE", "qualified": True, "entryReadiness": "PENDING"},
        {"decision": "TRADE", "qualified": True},
        {"decision": "TRADE", "qualified": False, "entryReadiness": "READY"},
        {"decision": "WATCH", "qualified": True, "entryReadiness": "READY"},
        {"score": 2.9, "passed": True},
    ):
        grade = _ld_trade_grade(_row(engine_a=partial))
        assert grade["isTradeGrade"] is False, partial
        assert "no_engine_reached_trade" in grade["blockers"]


def test_engine_b_confidence_pass_is_trade_grade():
    grade = _ld_trade_grade(_row(engine_b={"confidencePassed": True}, levels=GOOD_LEVELS_B))
    assert grade["isTradeGrade"] is True
    assert grade["engineB"] is True


def test_engine_b_clear_structure_alone_is_not_trade_grade():
    grade = _ld_trade_grade(_row(engine_b={"structuralVerdict": "CLEAR"}, levels=GOOD_LEVELS_B))
    assert grade["isTradeGrade"] is False


def test_engine_d_pass_is_trade_grade():
    grade = _ld_trade_grade(_row(engine_d=D_PASS, levels=GOOD_LEVELS_D))
    assert grade["isTradeGrade"] is True
    assert grade["engineD"] is True


def test_stale_freshness_disqualifies_even_a_ready_engine_a():
    ready = {"decision": "TRADE", "qualified": True, "entryReadiness": "READY"}
    grade = _ld_trade_grade(_row(engine_a=ready, freshness=BLOCK))
    assert grade["isTradeGrade"] is False
    assert "freshness_block" in grade["blockers"]
    # The engine still reports what it found; only the verdict flips.
    assert "engineA_trade_ready" in grade["engines"]


def test_missing_levels_disqualify_even_a_ready_engine_a():
    ready = {"decision": "TRADE", "qualified": True, "entryReadiness": "READY"}
    grade = _ld_trade_grade(_row(engine_a=ready, levels={}))
    assert grade["isTradeGrade"] is False
    assert "levels_incomplete" in grade["blockers"]


def test_empty_row_is_not_trade_grade():
    grade = _ld_trade_grade({})
    assert grade["isTradeGrade"] is False
    assert grade["engines"] == []
    assert set(grade["blockers"]) >= {"no_engine_reached_trade", "levels_incomplete"}
