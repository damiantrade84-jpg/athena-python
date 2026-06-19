"""Energy-specific residual-gap reclassification (v4) evidence patterns.

Covers the deterministic energy-session logic that resolves the WTI Oil and
Nat Gas reliable-era H4 gaps left UNRESOLVED by the v3 run:

  * zero-range holiday placeholder days -> LEGITIMATE_MARKET_CLOSURE;
  * isolated non-holiday gaps on demonstrably-traded days -> PROVIDER_HISTORY_GAP;
  * peer agreement/disagreement, early close, sustained degradation, acquisition
    revision precedence, conflicting-evidence UNRESOLVED, and no raw mutation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from athena_research.commodity_data_audit.consolidated_gap_review import (
    GapGate,
    ResidualGapClass,
    ResidualGapEvidence,
    classify_residual_event,
    evaluate_gap_gate,
    residual_evidence_from_v3_event,
)

UTC = timezone.utc
H4 = 4 * 3600


def _bar(epoch: int, value: float, *, rng: float = 0.0) -> dict:
    return {
        "time": epoch,
        "open": value,
        "high": value + rng,
        "low": value,
        "close": value,
    }


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _v3_event(**overrides) -> dict:
    base = {
        "event_id": "evt",
        "canonical_symbol": "WTI Oil",
        "prev_bar_timestamp": "2017-12-25T08:00:00+00:00",
        "next_bar_timestamp": "2017-12-25T20:00:00+00:00",
        "missing_timestamps": [
            "2017-12-25T12:00:00+00:00",
            "2017-12-25T16:00:00+00:00",
        ],
        "in_reliable_era": True,
        "recognized_holiday": True,
        "session_closure_pattern": True,
        "subject_refetch_empty": True,
        "subject_refetch_returned_bars": False,
        "d1_absent": False,
        "chunk_integrity": True,
        "acquisition_defect": False,
        "corruption_reasons": [],
        "primary_peer_shared_absence": True,
        "primary_peer_traded": False,
        "secondary_peer_shared_absence": False,
        "provider_history_absence": False,
        "historical_degradation": False,
        "reopen_gap_risk": {"severity": "MINIMAL"},
        "evidence": {
            "d1": {
                "calendar_date": "2017-12-25",
                "frozen_d1_present": False,
                "live_d1_present": True,
                "frozen_d1": None,
                "live_d1": {"open": 58.42, "high": 58.42, "low": 58.42, "close": 58.42},
                "status": "ok",
            }
        },
    }
    base.update(overrides)
    return base


def _flat_holiday_lookup() -> dict[int, dict]:
    # 2017-12-25 present neighbours are zero-range placeholders; the next session
    # (2017-12-26) trades for real and must be ignored (different calendar date).
    return {
        _epoch("2017-12-25T00:00:00+00:00"): _bar(_epoch("2017-12-25T00:00:00+00:00"), 58.42),
        _epoch("2017-12-25T08:00:00+00:00"): _bar(_epoch("2017-12-25T08:00:00+00:00"), 58.42),
        _epoch("2017-12-25T20:00:00+00:00"): _bar(_epoch("2017-12-25T20:00:00+00:00"), 58.42),
        _epoch("2017-12-26T00:00:00+00:00"): _bar(
            _epoch("2017-12-26T00:00:00+00:00"), 58.42, rng=0.26
        ),
    }


# --- WTI / Brent peer agreement: flat placeholder holiday closure -------------

def test_wti_flat_placeholder_holiday_with_peer_absence_is_closure():
    event = residual_evidence_from_v3_event(_v3_event(), subject_bar_lookup=_flat_holiday_lookup())
    assert event.d1_holiday_placeholder is True
    assert event.subject_day_traded is False
    assert classify_residual_event(event) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE


# --- WTI / Brent disagreement: peer traded -> conflicting -> UNRESOLVED --------

def test_wti_peer_trade_during_holiday_window_is_unresolved():
    event = residual_evidence_from_v3_event(
        _v3_event(primary_peer_traded=True),
        subject_bar_lookup=_flat_holiday_lookup(),
    )
    assert event.d1_holiday_placeholder is True
    assert classify_residual_event(event) == ResidualGapClass.UNRESOLVED


# --- Nat Gas independently proven holiday closure (no peer, D1 absent) ---------

def test_nat_gas_holiday_closure_without_peer_and_absent_d1():
    event = residual_evidence_from_v3_event(
        _v3_event(
            canonical_symbol="Nat Gas",
            primary_peer_shared_absence=False,
            d1_absent=True,
            missing_timestamps=[
                "2018-12-25T00:00:00+00:00",
                "2018-12-25T04:00:00+00:00",
                "2018-12-25T08:00:00+00:00",
            ],
            evidence={
                "d1": {
                    "calendar_date": "2018-12-25",
                    "frozen_d1_present": False,
                    "live_d1_present": False,
                    "frozen_d1": None,
                    "live_d1": None,
                    "status": "ok",
                }
            },
        ),
        subject_bar_lookup={},
    )
    assert event.d1_holiday_placeholder is False
    assert classify_residual_event(event) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE


# --- Energy early close: partial-day flat placeholder is still a closure -------

def test_energy_early_close_partial_flat_day_is_closure():
    lookup = {
        _epoch("2017-12-25T00:00:00+00:00"): _bar(_epoch("2017-12-25T00:00:00+00:00"), 64.83),
        _epoch("2017-12-25T08:00:00+00:00"): _bar(_epoch("2017-12-25T08:00:00+00:00"), 64.83),
    }
    event = residual_evidence_from_v3_event(
        _v3_event(
            evidence={
                "d1": {
                    "calendar_date": "2017-12-25",
                    "frozen_d1_present": False,
                    "live_d1_present": True,
                    "frozen_d1": None,
                    "live_d1": {"open": 64.83, "high": 64.83, "low": 64.83, "close": 64.83},
                    "status": "ok",
                }
            }
        ),
        subject_bar_lookup=lookup,
    )
    assert event.d1_holiday_placeholder is True
    assert classify_residual_event(event) == ResidualGapClass.LEGITIMATE_MARKET_CLOSURE


# --- Nat Gas isolated provider gap on a traded day -> CLEAR_WITH_GAP_EMBARGO ---

def _nat_gas_provider_event() -> ResidualGapEvidence:
    real_lookup = {
        _epoch("2018-07-17T00:00:00+00:00"): _bar(_epoch("2018-07-17T00:00:00+00:00"), 2.736, rng=0.006),
        _epoch("2018-07-17T04:00:00+00:00"): _bar(_epoch("2018-07-17T04:00:00+00:00"), 2.736, rng=0.008),
        _epoch("2018-07-17T08:00:00+00:00"): _bar(_epoch("2018-07-17T08:00:00+00:00"), 2.743, rng=0.013),
    }
    return residual_evidence_from_v3_event(
        _v3_event(
            canonical_symbol="Nat Gas",
            prev_bar_timestamp="2018-07-17T08:00:00+00:00",
            next_bar_timestamp="2018-07-18T08:00:00+00:00",
            missing_timestamps=[
                "2018-07-17T12:00:00+00:00",
                "2018-07-17T16:00:00+00:00",
                "2018-07-17T20:00:00+00:00",
                "2018-07-18T00:00:00+00:00",
                "2018-07-18T04:00:00+00:00",
            ],
            recognized_holiday=False,
            session_closure_pattern=False,
            primary_peer_shared_absence=False,
            evidence={
                "d1": {
                    "calendar_date": "2018-07-17",
                    "frozen_d1_present": False,
                    "live_d1_present": True,
                    "frozen_d1": None,
                    "live_d1": {"open": 2.736, "high": 2.745, "low": 2.732, "close": 2.736},
                    "status": "ok",
                }
            },
        ),
        subject_bar_lookup=real_lookup,
    )


def test_nat_gas_traded_day_gap_is_provider_history_gap():
    event = _nat_gas_provider_event()
    assert event.subject_day_traded is True
    assert event.d1_holiday_placeholder is False
    assert classify_residual_event(event) == ResidualGapClass.PROVIDER_HISTORY_GAP


def test_isolated_nat_gas_provider_gap_clears_with_embargo():
    event = _nat_gas_provider_event()
    origin = datetime(2018, 1, 1, tzinfo=UTC)
    expected = [origin + timedelta(seconds=H4 * i) for i in range(4000)]
    expected = sorted(set(expected).union(event.missing_timestamps))
    result = evaluate_gap_gate([event], expected)
    assert result.gate == GapGate.CLEAR_WITH_GAP_EMBARGO
    assert result.metrics.maximum_event_missing_bars == 5
    assert float(result.metrics.missing_percentage_decimal) < 0.5


# --- Sustained provider degradation stays blocked -----------------------------

def test_sustained_provider_degradation_blocks():
    origin = datetime(2018, 1, 1, tzinfo=UTC)
    expected = [origin + timedelta(seconds=H4 * i) for i in range(4000)]

    def provider(idx: int, length: int) -> ResidualGapEvidence:
        miss = tuple(expected[idx : idx + length])
        return residual_evidence_from_v3_event(
            _v3_event(
                event_id=f"deg{idx}",
                canonical_symbol="Nat Gas",
                prev_bar_timestamp=expected[idx - 1].isoformat(),
                next_bar_timestamp=expected[idx + length].isoformat(),
                missing_timestamps=[ts.isoformat() for ts in miss],
                recognized_holiday=False,
                session_closure_pattern=False,
                primary_peer_shared_absence=False,
                evidence={
                    "d1": {
                        "calendar_date": miss[0].date().isoformat(),
                        "frozen_d1_present": False,
                        "live_d1_present": True,
                        "frozen_d1": None,
                        "live_d1": {"open": 2.7, "high": 2.8, "low": 2.6, "close": 2.75},
                        "status": "ok",
                    }
                },
            ),
            subject_bar_lookup={
                int(expected[idx - 1].timestamp()): _bar(
                    int(expected[idx - 1].timestamp()), 2.7, rng=0.05
                )
            },
        )

    # Two provider gaps only 5 bars apart -> separation < 20 days -> blocked.
    events = [provider(100, 3), provider(108, 3)]
    assert all(e.subject_day_traded for e in events)
    assert evaluate_gap_gate(events, expected).gate == GapGate.BLOCKED_PROVIDER_DEGRADATION


# --- Acquisition revision precedence ------------------------------------------

def test_acquisition_refetch_returned_bars_takes_precedence():
    event = residual_evidence_from_v3_event(
        _v3_event(subject_refetch_returned_bars=True),
        subject_bar_lookup=_flat_holiday_lookup(),
    )
    assert classify_residual_event(event) == ResidualGapClass.ACQUISITION_DEFECT


# --- No interpolation / no raw mutation ---------------------------------------

def test_reconstruction_is_read_only_and_preserves_missing_timestamps():
    lookup = _flat_holiday_lookup()
    snapshot = {ts: dict(bar) for ts, bar in lookup.items()}
    raw_event = _v3_event()
    event = residual_evidence_from_v3_event(raw_event, subject_bar_lookup=lookup)
    # Raw lookup untouched (no inserted/removed/edited bars).
    assert lookup == snapshot
    # Missing timestamps preserved verbatim, none synthesized.
    assert [ts.isoformat() for ts in event.missing_timestamps] == raw_event["missing_timestamps"]
    assert len(event.missing_timestamps) == 2
