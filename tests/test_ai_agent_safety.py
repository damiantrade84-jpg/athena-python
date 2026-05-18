from ai_agent_safety import validate_ai_chat_response


def _packet(**overrides):
    packet = {
        "direction": "LONG",
        "engine_d": {
            "gate_result": "PASS",
            "executable": True,
            "rr1": 2.0,
            "rr_ok": True,
            "strict_fabio_pass": True,
            "spread_pips": 0.2,
            "fee_guard": {"engine_d_reject_reason": None},
        },
        "risk": {
            "rr": 2.0,
            "min_rr": 1.5,
            "guardian_status": "clean",
            "kill_switch_status": "off",
        },
        "data_quality": {"freshness_status": "fresh"},
        "deterministic_gates": {},
    }
    packet.update(overrides)
    return packet


def test_unsafe_valid_setup_is_downgraded_when_gate_failed():
    response = {"decision": "VALID_SETUP", "answer": "Decision: VALID_SETUP", "final_action": "advisory_review_only"}
    packet = _packet(engine_d={**_packet()["engine_d"], "executable": False})

    out = validate_ai_chat_response(response, packet)

    assert out["decision"] == "BLOCKED_BY_RISK"
    assert out["safety"]["read_only"] is True
    assert out["safety"]["can_execute"] is False
    assert "engine_d_executable_false" in out["safety_flags"]


def test_execution_language_is_downgraded():
    response = {
        "decision": "WATCHLIST",
        "answer": "Place the trade now.",
        "final_action": "place the order",
    }

    out = validate_ai_chat_response(response, _packet())

    assert out["final_action"] == "require_user_review_advisory_only"
    assert "unsupported_execution_language_downgraded" in out["safety_flags"]


def test_approve_trade_language_is_downgraded():
    response = {
        "decision": "WATCHLIST",
        "answer": "Approve the trade now.",
        "final_action": "advisory_review_only",
    }

    out = validate_ai_chat_response(response, _packet())

    assert out["final_action"] == "require_user_review_advisory_only"
    assert "unsupported_execution_language_downgraded" in out["safety_flags"]


def test_kill_switch_active_blocks_valid_setup():
    response = {"decision": "VALID_SETUP", "answer": "valid", "final_action": "advisory_review_only"}
    packet = _packet(risk={**_packet()["risk"], "kill_switch_status": "ACTIVE"})

    out = validate_ai_chat_response(response, packet)

    assert out["decision"] == "BLOCKED_BY_RISK"
    assert "kill_switch_not_clean" in out["safety_flags"]


def test_fee_guard_failed_blocks_valid_setup():
    response = {"decision": "VALID_SETUP", "answer": "valid", "final_action": "advisory_review_only"}
    engine_d = _packet()["engine_d"]
    engine_d["fee_guard"] = {"engine_d_reject_reason": "spread too high"}
    packet = _packet(engine_d=engine_d)

    out = validate_ai_chat_response(response, packet)

    assert out["decision"] == "BLOCKED_BY_RISK"
    assert "fee_guard_failed" in out["safety_flags"]


def test_missing_rr_blocks_valid_setup():
    response = {"decision": "VALID_SETUP", "answer": "valid", "final_action": "advisory_review_only"}
    packet = _packet(risk={**_packet()["risk"], "rr": None}, engine_d={**_packet()["engine_d"], "rr1": None})

    out = validate_ai_chat_response(response, packet)

    assert out["decision"] == "BLOCKED_BY_RISK"
    assert "RR_MISSING" in out["safety_flags"]


def test_missing_scalp_spread_blocks_valid_setup():
    response = {"decision": "VALID_SETUP", "answer": "valid", "final_action": "advisory_review_only"}
    engine_d = _packet()["engine_d"]
    engine_d.pop("spread_pips")
    packet = _packet(
        requested_style="scalp",
        engine_source="engine_d",
        engine_d=engine_d,
        risk={**_packet()["risk"], "spread": None},
    )

    out = validate_ai_chat_response(response, packet)

    assert out["decision"] == "BLOCKED_BY_RISK"
    assert "SCALP_SPREAD_MISSING" in out["safety_flags"]
