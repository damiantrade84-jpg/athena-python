from ai_contracts import AIReviewPacket, AITradeDecision


def test_ai_trade_decision_forces_advisory_execution_flags():
    decision = AITradeDecision(
        decision="VALID_SETUP",
        confidence=0.9,
        confidence_type="reasoned_only",
        thesis="Clean read",
        market_read="Trend up",
        supports=["trend"],
        contradictions=[],
        confirmation_needed=[],
        invalidation="below low",
        risk_notes=[],
        historical_evidence_summary="No calibrated sample.",
        final_action="advisory_only",
        ai_may_execute=True,
        deterministic_gates_required=False,
    )

    out = decision.model_dump()
    assert out["ai_may_execute"] is False
    assert out["deterministic_gates_required"] is True


def test_ai_review_packet_serializes_required_sections():
    packet = AIReviewPacket(
        schema_version="1.0",
        trace_id="trace-1",
        symbol="BTCUSDT",
        asset_type="crypto",
        direction="LONG",
        requested_style="scalp",
        engine_source="engine_d",
        created_at="2026-05-14T00:00:00+00:00",
        market_context={"session": "London"},
        engine_a={"missing_fields": ["score"]},
        engine_b={"missing_fields": []},
        engine_c={"missing_fields": []},
        engine_d={"gate_result": "PASS", "missing_fields": []},
        vision={"missing_fields": []},
        risk={"rr": 1.8, "missing_fields": []},
        data_quality={"freshness_status": "fresh", "missing_fields": []},
        similar_outcomes={"aggregate_sample_size": 0, "missing_fields": []},
        user_question="Review this trade",
        context_completeness={"overall_complete": 0.75},
    )

    out = packet.model_dump(mode="json")
    assert out["schema_version"] == "1.0"
    assert out["engine_d"]["gate_result"] == "PASS"
    assert out["context_completeness"]["overall_complete"] == 0.75
