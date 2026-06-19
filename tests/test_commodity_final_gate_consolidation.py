"""Final commodity data-gate consolidation: XAU/XAG frozen-corroboration
closure onboarding, WTI NON_TRADING_PLACEHOLDER detection + exclusion mask.

All additive: existing refetch-based closure and provider-gap logic is unchanged
(defaults preserve prior behaviour). No raw mutation, no interpolation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from athena_research.commodity_data_audit.consolidated_gap_review import (
    PLACEHOLDER_CLASS,
    ResidualGapClass,
    classify_residual_event,
    family_policy_for_symbol,
    non_trading_placeholder_events,
    residual_evidence_from_gap_rows,
)
from athena_research.commodity_data_audit.gap_embargo import build_placeholder_exclusion_mask

UTC = timezone.utc


# --- family policies for precious metals --------------------------------------

def test_xau_xag_family_policies_pair_each_other():
    assert family_policy_for_symbol("XAU/USD").primary_peers == ("XAG/USD",)
    assert family_policy_for_symbol("XAG/USD").primary_peers == ("XAU/USD",)
    # Precious metals do not claim independent closure (peer corroboration used).
    assert family_policy_for_symbol("XAU/USD").allow_independent_closure is False


# --- frozen-corroboration closure onboarding (no live refetch) ----------------

def _xau_holiday_gap(**peer_over) -> dict:
    peer_comparison = {"status": "skipped", "frozen_peer_evidence": {"shared_frozen_absence_candidate": True}}
    peer_comparison.update(peer_over)
    return {
        "event_id": "xau_xmas_2018",
        "prev_bar_timestamp": "2018-12-24T20:00:00+00:00",
        "next_bar_timestamp": "2018-12-26T00:00:00+00:00",
        "missing_bar_count": 6,
        "chunk_location": {"within_single_chunk": True},
        "mt5_subject_refetch": {"status": "skipped"},
        "peer_comparison": peer_comparison,
        "d1_availability": {"status": "ok", "frozen_d1_present": False, "live_d1_present": False, "calendar_date": "2018-12-25"},
        "holiday_proximity": {"holiday_hits": ["christmas"]},
        "price_discontinuity": {"corruption_reasons": []},
        "classification": "UNRESOLVED",
    }


def test_xau_frozen_holiday_closure_is_legitimate():
    ev = residual_evidence_from_gap_rows(
        canonical_symbol="XAU/USD",
        peer_gap_rows=[("XAG/USD", _xau_holiday_gap())],
    )
    assert ev.frozen_closure_corroborated is True
    assert ev.d1_absent is True
    assert ev.primary_peer_shared_absence is True
    assert classify_residual_event(ev) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE


def test_frozen_path_requires_peer_corroboration():
    # Peer NOT frozen-absent -> no frozen corroboration -> not a closure.
    gap = _xau_holiday_gap(frozen_peer_evidence={"shared_frozen_absence_candidate": False})
    ev = residual_evidence_from_gap_rows(
        canonical_symbol="XAU/USD",
        peer_gap_rows=[("XAG/USD", gap)],
    )
    assert ev.frozen_closure_corroborated is False
    assert classify_residual_event(ev) == ResidualGapClass.UNRESOLVED


def test_frozen_path_does_not_fire_when_live_refetch_present():
    # A real empty refetch (status ok, B_ classification) uses the existing live
    # path, not the frozen fallback.
    gap = _xau_holiday_gap()
    gap["mt5_subject_refetch"] = {
        "status": "ok",
        "returned_missing_count": 0,
        "classification": "B_no_bar_returned_terminal_history_or_session_closure_candidate",
    }
    ev = residual_evidence_from_gap_rows(
        canonical_symbol="XAU/USD",
        peer_gap_rows=[("XAG/USD", gap)],
    )
    assert ev.frozen_closure_corroborated is False
    assert ev.subject_refetch_empty is True
    assert classify_residual_event(ev) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE


def test_frozen_corroboration_does_not_rescue_acquisition_defect():
    gap = _xau_holiday_gap()
    gap["mt5_subject_refetch"] = {"status": "ok", "returned_missing_count": 2, "classification": "A_bar_returned"}
    ev = residual_evidence_from_gap_rows(
        canonical_symbol="XAU/USD",
        peer_gap_rows=[("XAG/USD", gap)],
    )
    assert classify_residual_event(ev) == ResidualGapClass.ACQUISITION_DEFECT


# --- NON_TRADING_PLACEHOLDER detection ----------------------------------------

def _bar(iso: str, value: float, rng: float = 0.0) -> dict:
    return {
        "time": int(datetime.fromisoformat(iso).timestamp()),
        "open": value,
        "high": value + rng,
        "low": value,
        "close": value,
    }


def test_zero_range_bars_flagged_real_bars_kept():
    bars = [
        _bar("2017-12-25T08:00:00+00:00", 58.42),          # flat placeholder, holiday
        _bar("2018-05-28T20:00:00+00:00", 66.39),          # flat placeholder, non-holiday (Memorial Day)
        _bar("2017-12-26T00:00:00+00:00", 58.42, rng=0.26),  # real trade
        _bar("2015-01-01T00:00:00+00:00", 50.00),          # flat but pre-reliable-era
    ]
    events = non_trading_placeholder_events(bars, reliable_start="2016-07-29", usable_start="2017-01-17")
    flagged = {e["timestamp"]: e for e in events}
    assert all(e["classification"] == PLACEHOLDER_CLASS for e in events)
    # all three zero-range bars detected; the real bar is not
    assert len(events) == 3
    assert "2017-12-26T00:00:00+00:00" not in flagged
    xmas = flagged["2017-12-25T08:00:00+00:00"]
    assert xmas["holiday_hits"] == ["christmas"] and xmas["in_reliable_era"] is True
    # pre-era placeholder flagged but marked out of active research window
    assert flagged["2015-01-01T00:00:00+00:00"]["in_reliable_era"] is False


# --- placeholder exclusion mask -----------------------------------------------

def test_placeholder_mask_excludes_placeholders_only_and_is_read_only():
    c0 = datetime(2017, 12, 25, 8, tzinfo=UTC)
    c1 = datetime(2017, 12, 26, 0, tzinfo=UTC)
    c2 = datetime(2018, 5, 28, 20, tzinfo=UTC)
    candidates = [c0, c1, c2]
    placeholders = [c0, c2]
    before = list(candidates)
    mask = build_placeholder_exclusion_mask(candidates, placeholders, {"symbol": "WTI Oil"})
    assert mask.allowed == (False, True, False)
    assert mask.reasons[0] == ("non_trading_placeholder",)
    assert mask.reasons[1] == ()
    assert set(mask.excluded_timestamps) == {c0.isoformat(), c2.isoformat()}
    assert len(mask.run_hash) == 64
    # inputs untouched (no interpolation / mutation)
    assert candidates == before
