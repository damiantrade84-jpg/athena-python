from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from athena_research.commodity_data_audit.freeze_quality import audit_freeze_bars
from athena_research.commodity_data_audit.gap_forensic import build_abnormal_gap_events, load_chunk_boundaries
from athena_research.commodity_data_audit.reliable_era_gap_review import (
    ReliableEraGapClass,
    ReliableEraGapReview,
    _price_discontinuity,
    adjust_reliable_quality_for_legitimate_closures,
    classify_reliable_era_gap,
    review_reliable_era_abnormal_gaps,
    review_to_dict,
    run_commodity_reliable_era_gap_review,
)


def _review(**overrides) -> ReliableEraGapReview:
    base = {
        "event_id": "abc",
        "prev_bar_timestamp": "2018-12-24T20:00:00+00:00",
        "next_bar_timestamp": "2018-12-26T00:00:00+00:00",
        "calendar_date": "2018-12-25",
        "weekday": "Tuesday",
        "duration_seconds": 28 * 3600,
        "missing_h4_slots": [0, 4, 8, 12, 16, 20],
        "missing_bar_count": 6,
        "chunk_location": {"within_single_chunk": True},
        "mt5_subject_refetch": {},
        "peer_comparison": {},
        "d1_availability": {},
        "holiday_proximity": {},
        "price_discontinuity": {},
        "reopen_gap_risk": {},
        "classification": ReliableEraGapClass.UNRESOLVED,
        "classification_evidence": [],
    }
    base.update(overrides)
    return ReliableEraGapReview(**base)


def _full_closure_evidence(**overrides):
    base = {
        "event": {"chunk_context": {"at_chunk_boundary": False, "within_single_chunk": True}},
        "refetch_row": {
            "subject": {
                "status": "ok",
                "returned_missing_count": 0,
                "classification": "B_no_bar_returned_terminal_history_or_session_closure_candidate",
            },
            "peer": {
                "status": "ok",
                "shared_session_closure_candidate": True,
                "peer_trades_while_subject_missing_samples": [],
            },
        },
        "peer_frozen": {"shared_frozen_absence_candidate": True},
        "d1_info": {"status": "ok", "frozen_d1_present": False, "live_d1_present": False},
        "holiday": {"holiday_hits": ["christmas"], "any_common_closure": True},
        "price": {"large_discontinuity": False},
    }
    base.update(overrides)
    return base


def _valid_price_discontinuity(next_open: float):
    return _price_discontinuity(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {
            "open": next_open,
            "high": max(next_open, 100.0) + 1.0,
            "low": min(next_open, 100.0) - 1.0,
            "close": next_open,
        },
    )


def test_classify_acquisition_defect_when_mt5_returns_missing_bars():
    classification, evidence = classify_reliable_era_gap(
        event={},
        refetch_row={"subject": {"returned_missing_count": 2, "classification": "A_bar_now_returned"}},
        peer_frozen=None,
        d1_info={},
        holiday={},
        price={},
    )
    assert classification == ReliableEraGapClass.ACQUISITION_DEFECT
    assert "absent from frozen raw" in evidence[0]


def test_classify_provider_history_gap_when_peer_trades():
    classification, evidence = classify_reliable_era_gap(
        event={},
        refetch_row={
            "subject": {"returned_missing_count": 0, "classification": "B_no_bar_returned", "status": "ok"},
            "peer": {"peer_trades_while_subject_missing_samples": ["2018-12-25T04:00:00+00:00"]},
        },
        peer_frozen=None,
        d1_info={},
        holiday={},
        price={},
    )
    assert classification == ReliableEraGapClass.PROVIDER_HISTORY_GAP
    assert "Peer H4 traded" in evidence[0]


def test_classify_legitimate_closure_requires_combined_evidence():
    classification, evidence = classify_reliable_era_gap(**_full_closure_evidence())
    assert classification == ReliableEraGapClass.LEGITIMATE_MARKET_CLOSURE
    assert any("Combined closure evidence" in item for item in evidence)


def test_mt5_refetch_alone_is_not_legitimate_without_corroboration():
    classification, evidence = classify_reliable_era_gap(
        event={"chunk_context": {"at_chunk_boundary": False, "within_single_chunk": True}},
        refetch_row={
            "subject": {
                "status": "ok",
                "returned_missing_count": 0,
                "classification": "B_no_bar_returned",
            },
            "peer": {"shared_session_closure_candidate": False},
        },
        peer_frozen={"shared_frozen_absence_candidate": False},
        d1_info={"frozen_d1_present": False, "live_d1_present": False},
        holiday={"holiday_hits": [], "any_common_closure": False},
        price={"large_discontinuity": False},
    )
    assert classification == ReliableEraGapClass.UNRESOLVED
    assert "insufficient corroboration" in evidence[-1]


def test_peer_disagreement_blocks_legitimate_closure():
    classification, _ = classify_reliable_era_gap(
        **_full_closure_evidence(
            refetch_row={
                "subject": {
                    "status": "ok",
                    "returned_missing_count": 0,
                    "classification": "B_no_bar_returned",
                },
                "peer": {
                    "peer_trades_while_subject_missing_samples": ["2018-12-25T08:00:00+00:00"]
                },
            }
        )
    )
    assert classification == ReliableEraGapClass.PROVIDER_HISTORY_GAP


def test_d1_presence_blocks_legitimate_closure():
    classification, evidence = classify_reliable_era_gap(
        **_full_closure_evidence(d1_info={"frozen_d1_present": True, "live_d1_present": False})
    )
    assert classification == ReliableEraGapClass.UNRESOLVED
    assert any("D1 bar present" in item for item in evidence)


def test_proven_christmas_closure_with_2_6_percent_reopen_gap_remains_legitimate():
    classification, _ = classify_reliable_era_gap(
        **_full_closure_evidence(
            holiday={"holiday_hits": ["christmas"], "any_common_closure": True},
            price=_valid_price_discontinuity(102.6),
        )
    )
    assert classification == ReliableEraGapClass.LEGITIMATE_MARKET_CLOSURE


def test_proven_new_year_closure_with_1_percent_reopen_gap_remains_legitimate():
    classification, _ = classify_reliable_era_gap(
        **_full_closure_evidence(
            holiday={"holiday_hits": ["new_year"], "any_common_closure": True},
            price=_valid_price_discontinuity(101.0),
        )
    )
    assert classification == ReliableEraGapClass.LEGITIMATE_MARKET_CLOSURE


def test_reopening_gap_risk_is_visible_with_percentage_and_atr_units():
    price = _price_discontinuity(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 2.0},
        {"open": 102.596, "high": 103.0, "low": 102.0, "close": 102.8},
    )
    risk = price["reopen_gap_risk"]
    assert risk["absolute_gap"] == pytest.approx(2.596)
    assert risk["relative_gap_pct"] == pytest.approx(2.596)
    assert risk["atr_normalised_gap"] == pytest.approx(1.298)
    assert risk["severity"] == "HIGH"


def test_scale_decimal_corruption_blocks_legitimate_closure():
    corrupt_price = _price_discontinuity(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"open": 1000.0, "high": 1001.0, "low": 999.0, "close": 1000.0},
    )
    classification, evidence = classify_reliable_era_gap(
        **_full_closure_evidence(price=corrupt_price)
    )
    assert classification == ReliableEraGapClass.UNRESOLVED
    assert any("scale_or_decimal_shift" in item for item in evidence)


def test_holiday_label_alone_is_insufficient_for_legitimate_closure():
    classification, evidence = classify_reliable_era_gap(
        event={"chunk_context": {"at_chunk_boundary": False, "within_single_chunk": True}},
        refetch_row=None,
        peer_frozen=None,
        d1_info={"frozen_d1_present": False, "live_d1_present": False},
        holiday={"holiday_hits": ["christmas"], "any_common_closure": True},
        price={"large_discontinuity": False, "corruption_reasons": []},
    )
    assert classification == ReliableEraGapClass.UNRESOLVED
    assert "insufficient corroboration" in evidence[-1]


def test_adjust_reliable_quality_excludes_legitimate_closures():
    bars = []
    for hour in (0, 4, 8, 12, 16, 20):
        bars.append(
            {
                "time": int(datetime(2017, 1, 3, hour, tzinfo=timezone.utc).timestamp()),
                "open": 1200.0,
                "high": 1201.0,
                "low": 1199.0,
                "close": 1200.0,
                "tick_volume": 10,
                "spread": 5,
            }
        )
    quality = audit_freeze_bars(
        bars,
        timeframe="H4",
        requested_start=datetime(2017, 1, 1, tzinfo=timezone.utc),
        requested_end=datetime(2017, 2, 1, tzinfo=timezone.utc),
        min_bars=1,
        as_of=datetime(2017, 2, 1, tzinfo=timezone.utc),
    )
    reviews = [
        _review(
            classification=ReliableEraGapClass.LEGITIMATE_MARKET_CLOSURE,
            missing_bar_count=6,
        )
    ]
    adjusted, meta = adjust_reliable_quality_for_legitimate_closures(quality, reviews)
    assert meta["adjusted"] is True
    assert "abnormal_active_session_gaps" not in adjusted.issues or adjusted.ok


def test_review_to_dict_records_symbol_and_peer():
    payload = review_to_dict(
        [_review(classification=ReliableEraGapClass.UNRESOLVED)],
        canonical_symbol="XAG/USD",
        peer_symbol="XAU/USD",
        timeframe="H4",
    )
    assert payload["canonical_symbol"] == "XAG/USD"
    assert payload["peer_symbol"] == "XAU/USD"
    assert payload["timeframe"] == "H4"


def test_symbol_and_peer_arguments_control_refetch_terminals():
    calls: list[tuple[str, str]] = []

    def _copy_rates(sym: str, tf: str, start, end):
        calls.append((sym, tf))
        return []

    subject_bars = [
        {
            "time": int(datetime(2018, 12, 24, 20, tzinfo=timezone.utc).timestamp()),
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "tick_volume": 1,
            "spread": 1,
            "real_volume": 0,
        },
        {
            "time": int(datetime(2018, 12, 26, 0, tzinfo=timezone.utc).timestamp()),
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "tick_volume": 1,
            "spread": 1,
            "real_volume": 0,
        },
    ]
    fake_events = [
        {
            "event_id": "evt1",
            "prev_bar_timestamp": "2018-12-24T20:00:00+00:00",
            "next_bar_timestamp": "2018-12-26T00:00:00+00:00",
            "duration_seconds": 28 * 3600,
            "missing_hour_slots_utc": [0, 4, 8, 12, 16, 20],
            "missing_bar_count": 6,
            "expected_missing_timestamps": ["2018-12-25T00:00:00+00:00"],
            "chunk_context": {"within_single_chunk": True},
        }
    ]
    with patch(
        "athena_research.commodity_data_audit.reliable_era_gap_review.build_abnormal_gap_events",
        return_value=fake_events,
    ):
        review_reliable_era_abnormal_gaps(
            bars=subject_bars,
            chunks=[],
            reliable_start="2016-07-29",
            copy_rates=_copy_rates,
            subject_terminal="XAGUSD",
            peer_terminal="XAUUSD",
        )
    assert ("XAGUSD", "H4") in calls
    assert ("XAUUSD", "H4") in calls


def test_xag_review_does_not_inherit_xau_classifications():
    from athena_research.commodity_data_audit.freeze_store import resolve_path

    raw_root = resolve_path("logs/commodity_data_audit/raw/v1")
    if not (raw_root / "XAG_USD" / "H4").exists():
        pytest.skip("XAG H4 store not present")

    xau = run_commodity_reliable_era_gap_review(
        canonical_symbol="XAU/USD",
        timeframe="H4",
        peer_symbol="XAG/USD",
        raw_root=raw_root,
        copy_rates=None,
    )
    xag = run_commodity_reliable_era_gap_review(
        canonical_symbol="XAG/USD",
        timeframe="H4",
        peer_symbol="XAU/USD",
        raw_root=raw_root,
        copy_rates=None,
    )
    assert xau["review_payload"]["canonical_symbol"] == "XAU/USD"
    assert xag["review_payload"]["canonical_symbol"] == "XAG/USD"
    assert xau["review_payload"]["peer_symbol"] == "XAG/USD"
    assert xag["review_payload"]["peer_symbol"] == "XAU/USD"
    assert xau["review_path"] != xag["review_path"]
    assert xau["review_payload"]["raw_content_hash"] != xag["review_payload"]["raw_content_hash"]
    assert all(
        gap["classification"] == ReliableEraGapClass.UNRESOLVED.value
        for gap in xag["review_payload"]["gaps"]
    )


def test_all_eight_xau_reliable_gaps_are_christmas_or_new_year_pattern():
    from athena_research.commodity_data_audit.freeze_store import load_raw_bars_from_store, resolve_path

    raw_root = resolve_path("logs/commodity_data_audit/raw/v1")
    bars = load_raw_bars_from_store(raw_root, "XAU_USD", "H4")
    chunks = load_chunk_boundaries(raw_root, "XAU_USD", "H4")
    reviews = review_reliable_era_abnormal_gaps(
        bars=bars,
        chunks=chunks,
        reliable_start="2016-07-29",
        copy_rates=None,
        subject_terminal="XAUUSD",
        peer_terminal="XAGUSD",
    )
    assert len(reviews) == 8
    for review in reviews:
        assert review.calendar_date.endswith("-12-25") or review.calendar_date.endswith("-01-01")
        assert review.holiday_proximity.get("any_common_closure") is True
        assert review.duration_seconds == 28 * 3600
        assert review.missing_bar_count == 6
