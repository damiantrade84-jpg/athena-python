from ai_orchestrator import _primary_sections, build_packet_for_signal, run_ai_trade_review


def test_primary_sections_does_not_match_single_letter_substrings():
    packet = {"engine_source": "Engine B naked market structure"}

    assert _primary_sections(packet) == ("engine_b",)


def test_failed_deterministic_gate_cannot_become_valid_setup():
    packet = build_packet_for_signal(
        {
            "pair": "BTCUSDT",
            "type": "crypto",
            "direction": "LONG",
            "rr1": 0.8,
            "gate_result": "WATCHLIST",
            "executable": False,
            "candidate_status": "rr_below_min",
            "spread_pips": 0.2,
            "dataFreshness": {"allowed": True, "reason": "fresh"},
            "fee_guard": {"engine_d_reject_reason": None},
        }
    )

    decision = run_ai_trade_review(packet)

    assert decision["decision"] != "VALID_SETUP"
    assert decision["ai_may_execute"] is False
    assert decision["deterministic_gates_required"] is True


def test_missing_data_returns_data_insufficient_or_watchlist_not_valid():
    packet = build_packet_for_signal({"pair": "EUR/USD", "type": "forex", "direction": "LONG"})

    decision = run_ai_trade_review(packet)

    assert decision["decision"] in {"DATA_INSUFFICIENT", "WATCHLIST", "WAIT_FOR_CONFIRMATION"}
    assert decision["decision"] != "VALID_SETUP"


def test_insufficient_similar_sample_prevents_calibrated_confidence():
    packet = build_packet_for_signal(
        {
            "pair": "EUR/USD",
            "type": "forex",
            "direction": "LONG",
            "rr1": 2.0,
            "spread_pips": 0.4,
            "dataFreshness": {"allowed": True, "reason": "fresh"},
            "similar_outcomes": {"aggregate_sample_size": 3, "reliability": "insufficient"},
        }
    )

    decision = run_ai_trade_review(packet)

    assert decision["confidence_type"] != "calibrated_from_outcomes"


def test_freshness_gate_denial_cannot_become_valid_setup():
    packet = {
        "schema_version": "1.0",
        "trace_id": "trace-freshness-denied",
        "symbol": "BTCUSDT",
        "asset_type": "crypto",
        "direction": "LONG",
        "requested_style": "scalp",
        "engine_source": "engine_d",
        "created_at": "2026-05-14T00:00:00+00:00",
        "engine_a": {"missing_fields": [], "direction": "LONG"},
        "engine_b": {"missing_fields": [], "direction": "LONG", "verdict": "CLEAR"},
        "engine_c": {"missing_fields": []},
        "engine_d": {
            "missing_fields": [],
            "gate_result": "PASS",
            "executable": True,
            "rr1": 2.0,
            "rr_ok": True,
            "strict_fabio_pass": True,
            "spread_pips": 0.2,
            "fee_guard": {"engine_d_reject_reason": None},
        },
        "vision": {"missing_fields": [], "confirms_direction": True},
        "risk": {"missing_fields": [], "rr": 2.0, "min_rr": 1.5, "guardian_status": "clean", "kill_switch_status": "clean"},
        "data_quality": {"missing_fields": [], "freshness_status": "allowed_false:market_closed"},
        "similar_outcomes": {"aggregate_sample_size": 0, "reliability": "unavailable"},
    }

    decision = run_ai_trade_review(packet)

    assert decision["decision"] != "VALID_SETUP"
    assert "DATA_FRESHNESS_STALE_OR_UNKNOWN" in decision["contradiction_flags"]


def test_explicit_deterministic_trade_false_cannot_become_valid_setup():
    packet = {
        "schema_version": "1.0",
        "trace_id": "trace-trade-false",
        "symbol": "BTCUSDT",
        "asset_type": "crypto",
        "direction": "LONG",
        "requested_style": "scalp",
        "engine_source": "engine_d",
        "created_at": "2026-05-14T00:00:00+00:00",
        "engine_a": {"missing_fields": [], "direction": "LONG"},
        "engine_b": {"missing_fields": [], "direction": "LONG", "verdict": "CLEAR"},
        "engine_c": {"missing_fields": []},
        "engine_d": {
            "missing_fields": [],
            "gate_result": "PASS",
            "executable": True,
            "rr1": 2.0,
            "rr_ok": True,
            "strict_fabio_pass": True,
            "spread_pips": 0.2,
            "fee_guard": {"engine_d_reject_reason": None},
        },
        "vision": {"missing_fields": [], "confirms_direction": True},
        "risk": {"missing_fields": [], "rr": 2.0, "min_rr": 1.5, "guardian_status": "clean", "kill_switch_status": "clean"},
        "data_quality": {"missing_fields": [], "freshness_status": "fresh"},
        "similar_outcomes": {"aggregate_sample_size": 0, "reliability": "unavailable"},
        "deterministic_gates": {"trade": False, "advisory_rule_trade_allowed": False},
    }

    decision = run_ai_trade_review(packet)

    assert decision["decision"] != "VALID_SETUP"
    assert decision["decision"] == "BLOCKED_BY_RISK"


def test_missing_rr_flag_cannot_become_valid_setup_from_complete_packet():
    packet = {
        "schema_version": "1.0",
        "trace_id": "trace-missing-rr",
        "symbol": "EURUSD",
        "asset_type": "forex",
        "direction": "LONG",
        "requested_style": "intraday",
        "engine_source": "engine_a",
        "created_at": "2026-05-14T00:00:00+00:00",
        "engine_a": {"missing_fields": [], "direction": "LONG"},
        "engine_b": {"missing_fields": [], "direction": "LONG", "verdict": "CLEAR"},
        "engine_c": {"missing_fields": []},
        "engine_d": {
            "missing_fields": [],
            "gate_result": "PASS",
            "executable": True,
            "rr_ok": True,
            "strict_fabio_pass": True,
            "spread_pips": 0.2,
            "fee_guard": {"engine_d_reject_reason": None},
        },
        "vision": {"missing_fields": [], "confirms_direction": True},
        "risk": {"missing_fields": [], "min_rr": 1.5, "guardian_status": "clean", "kill_switch_status": "clean"},
        "data_quality": {"missing_fields": [], "freshness_status": "fresh"},
        "similar_outcomes": {"aggregate_sample_size": 0, "reliability": "unavailable"},
    }

    decision = run_ai_trade_review(packet)

    assert decision["decision"] == "DATA_INSUFFICIENT"
    assert "RR_MISSING" in decision["contradiction_flags"]


def test_scalp_signal_with_engine_d_only_is_not_data_insufficient():
    """A live scalp signal naturally has Engine D context but no Engine A/B/C
    nested dicts and no Vision. Counting A/B/C/vision missing_fields against
    such a signal forces DATA_INSUFFICIENT every time. Only sections relevant
    to the originating engine should drive the DATA_INSUFFICIENT decision."""
    raw_scalp_signal = {
        "pair": "ADAUSDT",
        "symbol": "ADAUSDT",
        "type": "crypto",
        "direction": "SHORT",
        "engine": "SCALP",
        "engine_source": "scalp",
        "price": 0.2500,
        "entry": 0.2500,
        "sl": 0.2609,
        "tp1": 0.2393,
        "tp2": 0.2300,
        "rr1": 2.0,
        "min_rr": 1.5,
        "spread_pips": 0.2,
        "ai_score": 78,
        "ai_grade": "B",
        "gate_result": "PASS",
        "executable": True,
        "candidate_status": "PASS",
        "rr_ok": True,
        "strict_fabio_pass": True,
        "strict_fabio_reason": None,
        "strict_fabio_missing_pillars": [],
        "strict_fabio_pillars": {},
        "zone_type": "SUPPLY",
        "zone_level": 0.2609,
        "market_state": "TRENDING_DOWN",
        "session": "LONDON",
        "vp_poc": 0.2540,
        "vp_vah": 0.2580,
        "vp_val": 0.2510,
        "vp_volume_source": "tick",
        "vp_fidelity": "real",
        "vp_is_proxy": False,
        "vp_uses_real_trade_buckets": True,
        "vwap": 0.2545,
        "absorption_count": 2,
        "absorption_source": "tick",
        "cvd_direction": "SHORT",
        "cvd_source": "tick",
        "aaa_complete": True,
        "aggression_source": "tick",
        "aggression_confirmed": True,
        "aggression_uses_real_order_flow": True,
        "sl_method": "structural",
        "fee_guard": {"engine_d_reject_reason": None},
        "dataFreshness": {"allowed": True, "reason": "fresh"},
        "freshnessStatus": "fresh",
        "trade": True,
    }

    packet = build_packet_for_signal(raw_scalp_signal)
    decision = run_ai_trade_review(packet)

    assert decision["decision"] != "DATA_INSUFFICIENT", (
        f"Scalp signal with full Engine D context was flagged DATA_INSUFFICIENT "
        f"because Engine A/B/C sections were empty. missing={decision.get('context_missing_fields')}"
    )


def test_missing_scalp_spread_flag_cannot_become_valid_setup_from_complete_packet():
    packet = {
        "schema_version": "1.0",
        "trace_id": "trace-missing-spread",
        "symbol": "EURUSD",
        "asset_type": "forex",
        "direction": "LONG",
        "requested_style": "scalp",
        "engine_source": "engine_d",
        "created_at": "2026-05-14T00:00:00+00:00",
        "engine_a": {"missing_fields": [], "direction": "LONG"},
        "engine_b": {"missing_fields": [], "direction": "LONG", "verdict": "CLEAR"},
        "engine_c": {"missing_fields": []},
        "engine_d": {
            "missing_fields": [],
            "gate_result": "PASS",
            "executable": True,
            "rr1": 2.0,
            "rr_ok": True,
            "strict_fabio_pass": True,
            "fee_guard": {"engine_d_reject_reason": None},
        },
        "vision": {"missing_fields": [], "confirms_direction": True},
        "risk": {"missing_fields": [], "rr": 2.0, "min_rr": 1.5, "guardian_status": "clean", "kill_switch_status": "clean"},
        "data_quality": {"missing_fields": [], "freshness_status": "fresh"},
        "similar_outcomes": {"aggregate_sample_size": 0, "reliability": "unavailable"},
    }

    decision = run_ai_trade_review(packet)

    assert decision["decision"] == "DATA_INSUFFICIENT"
    assert "SCALP_SPREAD_MISSING" in decision["contradiction_flags"]
