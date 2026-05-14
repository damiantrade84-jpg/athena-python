from ai_contradiction_detector import detect_ai_contradictions


def _packet(**overrides):
    base = {
        "direction": "LONG",
        "requested_style": "scalp",
        "engine_a": {"direction": "LONG"},
        "engine_b": {"direction": "LONG", "verdict": "CLEAR"},
        "engine_d": {
            "gate_result": "PASS",
            "executable": True,
            "vp_is_proxy": False,
            "cvd_is_proxy": False,
            "aggression_source_is_proxy": False,
            "fee_guard": {"engine_d_reject_reason": None},
            "spread_pips": 0.4,
        },
        "vision": {"confirms_direction": True},
        "risk": {"rr": 2.0, "min_rr": 1.5, "guardian_status": "clean", "kill_switch_status": "clean"},
        "data_quality": {"freshness_status": "fresh"},
    }
    base.update(overrides)
    return base


def test_flags_engine_a_direction_conflict():
    result = detect_ai_contradictions(_packet(engine_a={"direction": "SHORT"}))
    assert "ENGINE_A_DIRECTION_OPPOSES_TRADE_DIRECTION" in result["flags"]


def test_flags_engine_b_direction_or_verdict_conflict():
    result = detect_ai_contradictions(_packet(engine_b={"direction": "SHORT", "verdict": "CLEAR"}))
    assert "ENGINE_B_DIRECTION_OPPOSES_TRADE_DIRECTION" in result["flags"]


def test_flags_vision_rr_and_freshness_problems():
    result = detect_ai_contradictions(
        _packet(
            vision={"confirms_direction": False},
            risk={"rr": 1.0, "min_rr": 1.5, "guardian_status": "clean", "kill_switch_status": "clean"},
            data_quality={"freshness_status": "stale"},
        )
    )
    assert "VISION_DOES_NOT_CONFIRM_DIRECTION" in result["flags"]
    assert "RR_BELOW_MIN" in result["flags"]
    assert "DATA_FRESHNESS_STALE_OR_UNKNOWN" in result["flags"]


def test_flags_engine_d_proxy_fee_spread_and_ai_upgrade():
    result = detect_ai_contradictions(
        _packet(
            engine_d={
                "gate_result": "WATCHLIST",
                "executable": False,
                "vp_is_proxy": True,
                "cvd_is_proxy": True,
                "aggression_source_is_proxy": True,
                "fee_guard": {"engine_d_reject_reason": "fee_guard_high_cost"},
                "spread_pips": None,
            },
        ),
        {"decision": "VALID_SETUP", "confidence": 0.9},
    )

    assert "ENGINE_D_PROXY_FLOW_TREATED_AS_REAL" in result["flags"]
    assert "FEE_GUARD_FAILED" in result["flags"]
    assert "SCALP_SPREAD_MISSING" in result["flags"]
    assert "AI_VALID_SETUP_WITH_BLOCKED_DETERMINISTIC_GATES" in result["flags"]
    assert result["severity"] == "critical"


def test_flags_guardian_or_kill_switch_not_clean_and_rr_missing():
    result = detect_ai_contradictions(
        _packet(risk={"rr": None, "min_rr": 1.5, "guardian_status": "blocked", "kill_switch_status": "active"})
    )

    assert "RR_MISSING" in result["flags"]
    assert "GUARDIAN_OR_KILL_SWITCH_NOT_CLEAN" in result["flags"]


def test_flags_valid_setup_when_explicit_trade_false():
    result = detect_ai_contradictions(
        _packet(deterministic_gates={"trade": False, "advisory_rule_trade_allowed": False}),
        {"decision": "VALID_SETUP", "confidence": 0.6},
    )

    assert "AI_VALID_SETUP_WITH_BLOCKED_DETERMINISTIC_GATES" in result["flags"]
