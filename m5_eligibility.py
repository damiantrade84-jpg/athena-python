"""Conditional M5 execution eligibility (Phase 3, timeframe-architecture migration).

M5 is never auto-activated by speed or liquidity. It may only refine the
*timing* of an entry that the higher-timeframe (setup/trigger) chain has
already established, and only when all 10 checks in
:func:`evaluate_m5_eligibility` pass.

Hard prohibitions (module contract — enforced fail-closed):
- M5 must not create a trade without a higher-timeframe setup
  (``setup_armed`` and ``htf_structure_valid`` are mandatory).
- M5 never reverses the higher-timeframe direction; this module neither
  accepts nor returns a direction — it only verifies the lower-timeframe
  trigger agrees with the already-established thesis.
- M5 never increases conviction merely because it agrees; the result carries
  no score, weight, or conviction field.
- M5 never rescues an invalid structural location; a quote inside the
  opposing structural zone or a broken HTF structure fails closed.
- M5 never promotes a stale trigger; an expired trigger yields no valid
  entry on any timeframe (no fallback).
- M5 never enters after excessive displacement from the setup zone.
- ``m5_policy`` DISABLED (or unknown/missing) => never eligible.

Threshold provenance: all thresholds (``spread_threshold``,
``max_displacement_atr``, ``min_rr``) and the profile's normal
``fallback_trigger_tf`` are caller-supplied inputs, expected to originate
from the resolved timeframe policy / instrument profile. This module is
deliberately importable without the repo ``config`` module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union


class M5EntryEvent(str, Enum):
    """Distinct M5 entry events recognised as valid refinement evidence."""

    SWEEP_RECLAIM = "sweep_reclaim"
    BREAK_RETEST = "break_retest"
    ZONE_REJECTION = "zone_rejection"
    PULLBACK_CONTINUATION = "pullback_continuation"
    LTF_STRUCTURE_SHIFT = "ltf_structure_shift"


class M5Policy(str, Enum):
    """M5 execution policy resolved by the timeframe policy layer."""

    DISABLED = "disabled"
    CONDITIONAL = "conditional"


class M5ValidationStatus(str, Enum):
    """Evidence state for promoting an instrument group to an M5 trigger.

    This is an evidence gate, not a data-quality check: the other conditions
    all describe the current bar, whereas this one records whether frozen
    out-of-sample work has ever shown M5 refinement to help for this group.

    Only APPROVED permits promotion.  UNVALIDATED is the default so a new
    instrument or group can never inherit M5 by omission, and RESEARCH_ONLY
    marks a group under active study whose results are not yet promotable.
    Runtime never sets these — they are recorded by a human after research.
    """

    UNVALIDATED = "unvalidated"
    RESEARCH_ONLY = "research_only"
    APPROVED = "approved"
    DISABLED = "disabled"


_M5_PROMOTABLE_VALIDATION = frozenset({M5ValidationStatus.APPROVED})


# Machine-readable reason codes (one per failed check, or the passing event).
REASON_M5_POLICY_DISABLED = "m5_policy_disabled"
REASON_M5_POLICY_MISSING = "m5_policy_missing"
REASON_HTF_STRUCTURE_INVALID = "htf_structure_invalid"
REASON_SETUP_NOT_ARMED = "setup_not_armed"
REASON_SESSION_NOT_ELIGIBLE = "session_not_eligible"
REASON_SPREAD_EXCEEDS_THRESHOLD = "spread_exceeds_threshold"
REASON_SPREAD_MISSING = "spread_missing"
REASON_EXCESSIVE_DISPLACEMENT = "excessive_displacement"
REASON_DISPLACEMENT_MISSING = "displacement_missing"
REASON_NO_M5_ENTRY_EVENT = "no_m5_entry_event"
REASON_TRIGGER_EXPIRED = "trigger_expired"
REASON_RR_BELOW_MIN = "structural_rr_below_min"
REASON_RR_MISSING = "structural_rr_missing"
REASON_QUOTE_INSIDE_OPPOSING_ZONE = "quote_inside_opposing_zone"
REASON_LTF_DIRECTION_CONFLICT = "ltf_direction_conflict"
REASON_M5_VALIDATION_NOT_APPROVED = "m5_validation_not_approved"

# Failures that invalidate the entry on every timeframe: with no HTF setup or
# an expired trigger there is no trade at all, so no trigger-TF fallback.
_NO_FALLBACK_REASONS = frozenset(
    {
        REASON_HTF_STRUCTURE_INVALID,
        REASON_SETUP_NOT_ARMED,
        REASON_TRIGGER_EXPIRED,
    }
)


@dataclass(frozen=True)
class M5EligibilityContext:
    """Inputs for the 10-check M5 eligibility evaluation.

    Optional fields fail closed when ``None`` (a missing input is recorded as
    a ``*_missing`` reason and the check fails). ``m5_policy`` accepts an
    :class:`M5Policy` or its string value. ``fallback_trigger_tf`` is the
    profile's normal trigger timeframe (e.g. ``"M15"``/``"M30"``) resolved by
    the caller from the timeframe policy; ``None`` means the profile permits
    no fallback entry.
    """

    m5_policy: Optional[Union[M5Policy, str]]
    htf_structure_valid: bool
    setup_armed: bool
    session_or_participation_eligible: bool
    current_spread: Optional[float]
    spread_threshold: Optional[float]
    distance_from_setup_atr: Optional[float]
    max_displacement_atr: Optional[float]
    entry_event: Optional[Union[M5EntryEvent, str]]
    trigger_expired: bool
    structural_rr_at_current_price: Optional[float]
    min_rr: Optional[float]
    quote_inside_opposing_zone: bool
    ltf_direction_agrees_with_thesis: bool
    fallback_trigger_tf: Optional[str] = None
    # Frozen out-of-sample evidence state for this instrument's group.
    # Defaults to UNVALIDATED so an omitted value can never permit promotion.
    m5_validation_status: Optional[Union[M5ValidationStatus, str]] = None


@dataclass(frozen=True)
class M5Eligibility:
    """Result of :func:`evaluate_m5_eligibility`.

    ``reasons`` holds one machine-readable code per failed check; when
    eligible it holds the single passing ``entry_event:<name>`` code.
    ``fallback_trigger_tf`` is the profile's normal trigger timeframe the
    caller should use instead when M5 is not eligible, or ``None`` when no
    valid entry exists on any timeframe.
    """

    eligible: bool
    reasons: list[str] = field(default_factory=list)
    fallback_trigger_tf: Optional[str] = None


def _normalise_policy(value: Optional[Union[M5Policy, str]]) -> Optional[M5Policy]:
    if isinstance(value, M5Policy):
        return value
    if isinstance(value, str):
        try:
            return M5Policy(value.strip().lower())
        except ValueError:
            return None
    return None


def _normalise_validation(
    value: Optional[Union[M5ValidationStatus, str]],
) -> Optional[M5ValidationStatus]:
    """Coerce a validation status; unknown or missing values return None.

    None is deliberately not mapped to UNVALIDATED — both fail the gate, and
    keeping them distinct means a typo cannot silently read as a real state.
    """
    if isinstance(value, M5ValidationStatus):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return None
    try:
        return M5ValidationStatus(text)
    except ValueError:
        return None


def _normalise_event(value: Optional[Union[M5EntryEvent, str]]) -> Optional[M5EntryEvent]:
    if isinstance(value, M5EntryEvent):
        return value
    if isinstance(value, str):
        try:
            return M5EntryEvent(value.strip().lower())
        except ValueError:
            return None
    return None


def evaluate_m5_eligibility(context: M5EligibilityContext) -> M5Eligibility:
    """Evaluate the 10 conditional-M5 checks; all must pass.

    The evaluation is side-effect-free and fail-closed: unknown or missing
    inputs count as failures. When the checks fail, the caller falls back to
    ``context.fallback_trigger_tf`` unless the failure also invalidates the
    base trigger (no HTF structure, no armed setup, or expired trigger), in
    which case there is no valid entry on any timeframe.
    """
    policy = _normalise_policy(context.m5_policy)
    if policy is None:
        return _ineligible(context, [REASON_M5_POLICY_MISSING])
    if policy is M5Policy.DISABLED:
        return _ineligible(context, [REASON_M5_POLICY_DISABLED])
    # Evidence gate. Checked before the per-bar conditions because no amount of
    # favourable current state substitutes for never having validated that M5
    # refinement helps this group. Unrecognised or absent values fail closed.
    if _normalise_validation(context.m5_validation_status) not in _M5_PROMOTABLE_VALIDATION:
        return _ineligible(context, [REASON_M5_VALIDATION_NOT_APPROVED])

    reasons: list[str] = []

    # 1) Higher-timeframe structure remains valid.
    if not context.htf_structure_valid:
        reasons.append(REASON_HTF_STRUCTURE_INVALID)
    # 2) Setup timeframe has armed a valid setup.
    if not context.setup_armed:
        reasons.append(REASON_SETUP_NOT_ARMED)
    # 3) Current session or participation state is eligible.
    if not context.session_or_participation_eligible:
        reasons.append(REASON_SESSION_NOT_ELIGIBLE)
    # 4) Spread within threshold.
    if context.current_spread is None or context.spread_threshold is None:
        reasons.append(REASON_SPREAD_MISSING)
    elif context.current_spread > context.spread_threshold:
        reasons.append(REASON_SPREAD_EXCEEDS_THRESHOLD)
    # 5) Price has not displaced too far from the setup zone (ATR-normalised).
    if context.distance_from_setup_atr is None or context.max_displacement_atr is None:
        reasons.append(REASON_DISPLACEMENT_MISSING)
    elif abs(context.distance_from_setup_atr) > context.max_displacement_atr:
        reasons.append(REASON_EXCESSIVE_DISPLACEMENT)
    # 6) A distinct M5 entry event exists.
    event = _normalise_event(context.entry_event)
    if event is None:
        reasons.append(REASON_NO_M5_ENTRY_EVENT)
    # 7) Trigger has not expired.
    if context.trigger_expired:
        reasons.append(REASON_TRIGGER_EXPIRED)
    # 8) Current-price structural RR remains valid (>= min).
    if context.structural_rr_at_current_price is None or context.min_rr is None:
        reasons.append(REASON_RR_MISSING)
    elif context.structural_rr_at_current_price < context.min_rr:
        reasons.append(REASON_RR_BELOW_MIN)
    # 9) Current quote is not inside the opposing structural zone.
    if context.quote_inside_opposing_zone:
        reasons.append(REASON_QUOTE_INSIDE_OPPOSING_ZONE)
    # 10) The lower-timeframe trigger agrees with the established thesis.
    if not context.ltf_direction_agrees_with_thesis:
        reasons.append(REASON_LTF_DIRECTION_CONFLICT)

    if reasons:
        return _ineligible(context, reasons)
    return M5Eligibility(
        eligible=True,
        reasons=[f"entry_event:{event.value}"],
        fallback_trigger_tf=None,
    )


def _ineligible(context: M5EligibilityContext, reasons: list[str]) -> M5Eligibility:
    fallback: Optional[str] = None
    if not _NO_FALLBACK_REASONS.intersection(reasons):
        fallback = context.fallback_trigger_tf
    return M5Eligibility(eligible=False, reasons=reasons, fallback_trigger_tf=fallback)
