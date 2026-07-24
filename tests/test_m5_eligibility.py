"""Tests for m5_eligibility.evaluate_m5_eligibility (Phase 3)."""

from __future__ import annotations

import pytest

from m5_eligibility import (
    M5EligibilityContext,
    M5EntryEvent,
    M5Policy,
    REASON_DISPLACEMENT_MISSING,
    REASON_EXCESSIVE_DISPLACEMENT,
    REASON_HTF_STRUCTURE_INVALID,
    REASON_LTF_DIRECTION_CONFLICT,
    REASON_M5_POLICY_DISABLED,
    REASON_M5_POLICY_MISSING,
    REASON_NO_M5_ENTRY_EVENT,
    REASON_QUOTE_INSIDE_OPPOSING_ZONE,
    REASON_RR_BELOW_MIN,
    REASON_RR_MISSING,
    REASON_SESSION_NOT_ELIGIBLE,
    REASON_SETUP_NOT_ARMED,
    REASON_SPREAD_EXCEEDS_THRESHOLD,
    REASON_SPREAD_MISSING,
    REASON_TRIGGER_EXPIRED,
    evaluate_m5_eligibility,
)


def _ok_context(**overrides) -> M5EligibilityContext:
    """A fully eligible context; callers override one field per failure case."""
    base = dict(
        m5_policy=M5Policy.CONDITIONAL,
        htf_structure_valid=True,
        setup_armed=True,
        session_or_participation_eligible=True,
        current_spread=0.8,
        spread_threshold=1.5,
        distance_from_setup_atr=0.4,
        max_displacement_atr=1.0,
        entry_event=M5EntryEvent.SWEEP_RECLAIM,
        trigger_expired=False,
        structural_rr_at_current_price=2.5,
        min_rr=2.0,
        quote_inside_opposing_zone=False,
        ltf_direction_agrees_with_thesis=True,
        fallback_trigger_tf="M15",
    )
    base.update(overrides)
    return M5EligibilityContext(**base)


def test_all_checks_pass_is_eligible() -> None:
    result = evaluate_m5_eligibility(_ok_context())
    assert result.eligible is True
    assert result.reasons == ["entry_event:sweep_reclaim"]
    assert result.fallback_trigger_tf is None


@pytest.mark.parametrize("event", list(M5EntryEvent))
def test_every_entry_event_passes(event: M5EntryEvent) -> None:
    result = evaluate_m5_eligibility(_ok_context(entry_event=event))
    assert result.eligible is True
    assert result.reasons == [f"entry_event:{event.value}"]


# --- The 10 checks, failing individually -----------------------------------


def test_check1_htf_structure_invalid() -> None:
    result = evaluate_m5_eligibility(_ok_context(htf_structure_valid=False))
    assert result.eligible is False
    assert result.reasons == [REASON_HTF_STRUCTURE_INVALID]


def test_check2_setup_not_armed() -> None:
    result = evaluate_m5_eligibility(_ok_context(setup_armed=False))
    assert result.eligible is False
    assert result.reasons == [REASON_SETUP_NOT_ARMED]


def test_check3_session_not_eligible() -> None:
    result = evaluate_m5_eligibility(
        _ok_context(session_or_participation_eligible=False)
    )
    assert result.eligible is False
    assert result.reasons == [REASON_SESSION_NOT_ELIGIBLE]


def test_check4_spread_exceeds_threshold() -> None:
    result = evaluate_m5_eligibility(_ok_context(current_spread=2.0))
    assert result.eligible is False
    assert result.reasons == [REASON_SPREAD_EXCEEDS_THRESHOLD]


def test_check5_excessive_displacement() -> None:
    result = evaluate_m5_eligibility(_ok_context(distance_from_setup_atr=1.5))
    assert result.eligible is False
    assert result.reasons == [REASON_EXCESSIVE_DISPLACEMENT]


def test_check6_no_entry_event() -> None:
    result = evaluate_m5_eligibility(_ok_context(entry_event=None))
    assert result.eligible is False
    assert result.reasons == [REASON_NO_M5_ENTRY_EVENT]


def test_check7_trigger_expired() -> None:
    result = evaluate_m5_eligibility(_ok_context(trigger_expired=True))
    assert result.eligible is False
    assert result.reasons == [REASON_TRIGGER_EXPIRED]


def test_check8_structural_rr_below_min() -> None:
    result = evaluate_m5_eligibility(_ok_context(structural_rr_at_current_price=1.5))
    assert result.eligible is False
    assert result.reasons == [REASON_RR_BELOW_MIN]


def test_check9_quote_inside_opposing_zone() -> None:
    result = evaluate_m5_eligibility(_ok_context(quote_inside_opposing_zone=True))
    assert result.eligible is False
    assert result.reasons == [REASON_QUOTE_INSIDE_OPPOSING_ZONE]


def test_check10_ltf_direction_conflict() -> None:
    result = evaluate_m5_eligibility(
        _ok_context(ltf_direction_agrees_with_thesis=False)
    )
    assert result.eligible is False
    assert result.reasons == [REASON_LTF_DIRECTION_CONFLICT]


# --- Policy gating ----------------------------------------------------------


def test_disabled_policy_never_eligible() -> None:
    result = evaluate_m5_eligibility(_ok_context(m5_policy=M5Policy.DISABLED))
    assert result.eligible is False
    assert result.reasons == [REASON_M5_POLICY_DISABLED]
    # Base trigger remains valid, so the profile's normal trigger applies.
    assert result.fallback_trigger_tf == "M15"


def test_disabled_policy_string_value() -> None:
    result = evaluate_m5_eligibility(_ok_context(m5_policy="disabled"))
    assert result.eligible is False
    assert result.reasons == [REASON_M5_POLICY_DISABLED]


def test_conditional_policy_string_value() -> None:
    result = evaluate_m5_eligibility(_ok_context(m5_policy="CONDITIONAL"))
    assert result.eligible is True


def test_missing_policy_fails_closed() -> None:
    result = evaluate_m5_eligibility(_ok_context(m5_policy=None))
    assert result.eligible is False
    assert result.reasons == [REASON_M5_POLICY_MISSING]
    assert result.fallback_trigger_tf == "M15"


def test_unknown_policy_string_fails_closed() -> None:
    result = evaluate_m5_eligibility(_ok_context(m5_policy="auto"))
    assert result.eligible is False
    assert result.reasons == [REASON_M5_POLICY_MISSING]


# --- Missing/None inputs fail closed ----------------------------------------


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"current_spread": None}, REASON_SPREAD_MISSING),
        ({"spread_threshold": None}, REASON_SPREAD_MISSING),
        ({"distance_from_setup_atr": None}, REASON_DISPLACEMENT_MISSING),
        ({"max_displacement_atr": None}, REASON_DISPLACEMENT_MISSING),
        ({"structural_rr_at_current_price": None}, REASON_RR_MISSING),
        ({"min_rr": None}, REASON_RR_MISSING),
        ({"entry_event": None}, REASON_NO_M5_ENTRY_EVENT),
    ],
)
def test_none_inputs_fail_closed(override: dict, reason: str) -> None:
    result = evaluate_m5_eligibility(_ok_context(**override))
    assert result.eligible is False
    assert result.reasons == [reason]


def test_unknown_entry_event_string_fails_closed() -> None:
    result = evaluate_m5_eligibility(_ok_context(entry_event="moon_pattern"))
    assert result.eligible is False
    assert result.reasons == [REASON_NO_M5_ENTRY_EVENT]


# --- Multiple simultaneous failures -----------------------------------------


def test_multiple_failures_report_all_reasons() -> None:
    result = evaluate_m5_eligibility(
        _ok_context(current_spread=5.0, trigger_expired=True)
    )
    assert result.eligible is False
    assert result.reasons == [REASON_SPREAD_EXCEEDS_THRESHOLD, REASON_TRIGGER_EXPIRED]


# --- Fallback trigger behaviour ---------------------------------------------


def test_fallback_offered_for_m5_only_failures() -> None:
    # Spread/displacement/session/event/RR/zone/direction failures only block
    # the M5 refinement; the profile's normal M15 trigger stays valid.
    result = evaluate_m5_eligibility(_ok_context(current_spread=5.0))
    assert result.eligible is False
    assert result.fallback_trigger_tf == "M15"


def test_no_fallback_without_htf_setup() -> None:
    result = evaluate_m5_eligibility(_ok_context(setup_armed=False))
    assert result.eligible is False
    assert result.fallback_trigger_tf is None


def test_no_fallback_when_htf_structure_invalid() -> None:
    result = evaluate_m5_eligibility(_ok_context(htf_structure_valid=False))
    assert result.eligible is False
    assert result.fallback_trigger_tf is None


def test_no_fallback_when_trigger_expired() -> None:
    result = evaluate_m5_eligibility(_ok_context(trigger_expired=True))
    assert result.eligible is False
    assert result.fallback_trigger_tf is None


def test_no_fallback_when_profile_permits_none() -> None:
    result = evaluate_m5_eligibility(
        _ok_context(current_spread=5.0, fallback_trigger_tf=None)
    )
    assert result.eligible is False
    assert result.fallback_trigger_tf is None


# --- Hard prohibitions --------------------------------------------------------


def test_prohibition_no_htf_setup_no_m5_entry() -> None:
    """M5 must not create a trade without a higher-timeframe setup."""
    result = evaluate_m5_eligibility(_ok_context(setup_armed=False))
    assert result.eligible is False
    assert REASON_SETUP_NOT_ARMED in result.reasons
    assert result.fallback_trigger_tf is None


def test_prohibition_never_reverses_direction() -> None:
    """An M5 trigger against the established thesis fails; the module exposes
    no direction field to flip."""
    result = evaluate_m5_eligibility(
        _ok_context(ltf_direction_agrees_with_thesis=False)
    )
    assert result.eligible is False
    assert REASON_LTF_DIRECTION_CONFLICT in result.reasons
    assert not hasattr(result, "direction")


def test_prohibition_no_conviction_inflation() -> None:
    """Agreement adds nothing: the result carries no score/conviction fields."""
    result = evaluate_m5_eligibility(_ok_context())
    assert result.eligible is True
    for field_name in ("score", "conviction", "confidence", "weight"):
        assert not hasattr(result, field_name)


def test_prohibition_never_rescues_invalid_location() -> None:
    """A quote inside the opposing structural zone fails even with a valid
    M5 entry event and agreeing direction."""
    result = evaluate_m5_eligibility(_ok_context(quote_inside_opposing_zone=True))
    assert result.eligible is False
    assert REASON_QUOTE_INSIDE_OPPOSING_ZONE in result.reasons


def test_prohibition_never_promotes_stale_trigger() -> None:
    result = evaluate_m5_eligibility(_ok_context(trigger_expired=True))
    assert result.eligible is False
    assert REASON_TRIGGER_EXPIRED in result.reasons
    assert result.fallback_trigger_tf is None


def test_prohibition_never_enters_after_excessive_displacement() -> None:
    result = evaluate_m5_eligibility(_ok_context(distance_from_setup_atr=3.0))
    assert result.eligible is False
    assert REASON_EXCESSIVE_DISPLACEMENT in result.reasons


def test_module_importable_without_repo_config() -> None:
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['config'] = None; "
        "import m5_eligibility; print('ok')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
