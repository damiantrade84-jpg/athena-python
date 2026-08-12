"""P2-1 / P2-2 / P2-3 review-layer gate hygiene."""

from __future__ import annotations

from ai_review.gate_hygiene import (
    in_session,
    normalize_review_gates,
    parse_session_window,
    volume_feed_excluded,
)


def _predicate(name: str, passed: bool, actual=None, expected: str = "") -> dict:
    return {"name": name, "passed": passed, "actual": actual, "expected": expected}


# --- P2-1 -------------------------------------------------------------------


def test_parse_session_window_reads_engine_a_label():
    assert parse_session_window("London/NY 06:00-21:00 UTC") == (6, 21)
    assert parse_session_window("US cash session 13:00-21:00 UTC") == (13, 21)
    assert parse_session_window("no window here") is None


def test_session_window_wrapping_midnight():
    window = parse_session_window("Asian 22:00-06:00 UTC")
    assert window == (22, 6)
    from datetime import datetime, timezone

    assert in_session(datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc), window) is True
    assert in_session(datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc), window) is True
    assert in_session(datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc), window) is False


def test_active_session_passes_at_0638_with_0100_confirmed_candle():
    """The exact audit case: 06:38 UTC scan is inside 06:00-21:00 and must PASS.

    Engine A evaluated the gate against the 01:00 UTC confirmed candle and
    returned FAIL. The review layer re-checks against scan time.
    """
    result = normalize_review_gates(
        predicates=[
            _predicate(
                "active_session_utc",
                False,
                actual="2026-08-12T01:00:00+00:00",
                expected="London/NY 06:00-21:00 UTC",
            )
        ],
        scan_timestamp="2026-08-12T06:38:07+00:00",
    )
    session_gate = next(g for g in result["gates"] if g["name"] == "active_session_utc")
    assert session_gate["status"] == "PASS"
    assert session_gate["engineValue"] is False
    assert session_gate["evaluatedAgainst"] == "scan_timestamp"

    recheck = result["sessionRecheck"]
    assert recheck["disagrees"] is True
    assert recheck["reviewValue"] is True
    assert recheck["engineEvaluatedAt"] == "2026-08-12T01:00:00+00:00"
    assert any("active_session_utc disagrees" in note for note in result["notes"])


def test_active_session_outside_window_still_fails():
    result = normalize_review_gates(
        predicates=[
            _predicate(
                "active_session_utc",
                False,
                actual="2026-08-12T01:00:00+00:00",
                expected="London/NY 06:00-21:00 UTC",
            )
        ],
        scan_timestamp="2026-08-12T03:12:00+00:00",
    )
    session_gate = next(g for g in result["gates"] if g["name"] == "active_session_utc")
    assert session_gate["status"] == "FAIL"
    assert result["sessionRecheck"]["disagrees"] is False


def test_active_session_falls_back_to_engine_value_without_scan_time():
    result = normalize_review_gates(
        predicates=[
            _predicate(
                "active_session_utc",
                True,
                actual="2026-08-12T01:00:00+00:00",
                expected="London/NY 06:00-21:00 UTC",
            )
        ],
        scan_timestamp=None,
    )
    session_gate = next(g for g in result["gates"] if g["name"] == "active_session_utc")
    assert session_gate["status"] == "PASS"
    assert session_gate["evaluatedAgainst"] == "engine_a_confirmed_candle"


# --- P2-2 -------------------------------------------------------------------


def test_context_trend_both_failing_collapses_to_one_neutral_gate():
    result = normalize_review_gates(
        predicates=[
            _predicate("context_trend_long", False, 0.0001, "> 0"),
            _predicate("context_trend_short", False, 0.0001, "< 0"),
        ],
    )
    names = [g["name"] for g in result["gates"]]
    assert "context_trend" in names
    assert "context_trend_long" not in names
    assert "context_trend_short" not in names

    gate = next(g for g in result["gates"] if g["name"] == "context_trend")
    assert gate["state"] == "NEUTRAL"
    assert gate["status"] == "FAIL"
    # One observation, one denominator slot — not two.
    assert result["applicableTotal"] == 1
    assert result["contextTrend"] == "NEUTRAL"


def test_context_trend_directional_reads_pass_once():
    for passing, expected_state in (
        ("context_trend_long", "LONG"),
        ("context_trend_short", "SHORT"),
    ):
        result = normalize_review_gates(
            predicates=[
                _predicate("context_trend_long", passing == "context_trend_long"),
                _predicate("context_trend_short", passing == "context_trend_short"),
            ],
        )
        gate = next(g for g in result["gates"] if g["name"] == "context_trend")
        assert gate["state"] == expected_state
        assert gate["status"] == "PASS"
        assert result["applicableTotal"] == 1


# --- P2-3 -------------------------------------------------------------------


def test_volume_feed_excluded_reads_quant_weights():
    weights = {
        "precious_trackers": {"trend": 0.4886, "location": 0.2273, "volume": 0.0},
        "crypto_major": {"trend": 0.4, "volume": 0.2},
    }
    assert volume_feed_excluded("precious_trackers", weights) is True
    assert volume_feed_excluded("crypto_major", weights) is False
    assert volume_feed_excluded("unknown_group", weights) is False


def test_volume_gate_on_excluded_feed_renders_na_and_leaves_denominator():
    result = normalize_review_gates(
        predicates=[
            _predicate("trend_supports_direction", True),
            _predicate("volume_supports_direction", False, 0.0, "volume >= 1.0x"),
        ],
        score_group="precious_trackers",
        quant_weights_by_group={"precious_trackers": {"volume": 0.0}},
    )
    volume_gate = next(
        g for g in result["gates"] if g["name"] == "volume_supports_direction"
    )
    assert volume_gate["status"] == "NA"
    assert volume_gate["applicable"] is False
    assert result["applicableTotal"] == 1
    assert result["applicablePassed"] == 1
    assert result["display"] == "1/1"
    assert result["excludedCount"] == 1


def test_volume_gate_still_counts_when_feed_is_used():
    result = normalize_review_gates(
        predicates=[
            _predicate("trend_supports_direction", True),
            _predicate("volume_supports_direction", False, 0.4, "volume >= 1.0x"),
        ],
        score_group="crypto_major",
        quant_weights_by_group={"crypto_major": {"volume": 0.2}},
    )
    volume_gate = next(
        g for g in result["gates"] if g["name"] == "volume_supports_direction"
    )
    assert volume_gate["status"] == "FAIL"
    assert result["display"] == "1/2"


# --- combined ---------------------------------------------------------------


def test_combined_audit_case_denominator_is_honest():
    """The audit's displayed 3/7 against the applicable-gate view.

    Two context_trend failures collapse to one neutral gate, the excluded volume
    gate leaves the denominator, and the session gate passes at scan time.
    """
    result = normalize_review_gates(
        predicates=[
            _predicate("trend_supports_direction", True),
            _predicate("momentum_supports_direction", True),
            _predicate("structure_confirmed", True),
            _predicate(
                "active_session_utc",
                False,
                actual="2026-08-12T01:00:00+00:00",
                expected="London/NY 06:00-21:00 UTC",
            ),
            _predicate("context_trend_long", False),
            _predicate("context_trend_short", False),
            _predicate("volume_supports_direction", False),
        ],
        score_group="precious_trackers",
        scan_timestamp="2026-08-12T06:38:07+00:00",
        quant_weights_by_group={"precious_trackers": {"volume": 0.0}},
    )
    # 7 raw predicates -> 5 applicable gates (volume excluded, trend pair collapsed).
    assert result["applicableTotal"] == 5
    assert result["applicablePassed"] == 4
    assert result["display"] == "4/5"
    assert result["contextTrend"] == "NEUTRAL"


def test_engine_a_decision_is_never_mutated():
    """Gate hygiene is presentation only — engineValue preserves Engine A."""
    predicates = [
        _predicate(
            "active_session_utc",
            False,
            actual="2026-08-12T01:00:00+00:00",
            expected="London/NY 06:00-21:00 UTC",
        ),
        _predicate("volume_supports_direction", False),
    ]
    snapshot = [dict(p) for p in predicates]
    result = normalize_review_gates(
        predicates=predicates,
        score_group="precious_trackers",
        scan_timestamp="2026-08-12T06:38:07+00:00",
        quant_weights_by_group={"precious_trackers": {"volume": 0.0}},
    )
    assert predicates == snapshot
    for gate in result["gates"]:
        assert gate["engineValue"] is False


def test_accepts_dataclass_predicates():
    from engine_a_v3.contract import PredicateResult

    result = normalize_review_gates(
        predicates=[
            PredicateResult(
                name="active_session_utc",
                passed=False,
                actual="2026-08-12T01:00:00+00:00",
                expected="London/NY 06:00-21:00 UTC",
            )
        ],
        scan_timestamp="2026-08-12T06:38:07+00:00",
    )
    assert result["gates"][0]["status"] == "PASS"
