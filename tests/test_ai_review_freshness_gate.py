"""Review-findings F1 and F5: freshness gate and unconditional symbol parity.

F1: the original P2-7 filter matched only "chart captured ..." strings and
compared against ``review_timestamp``, which is stamped ``now()`` at context
assembly. Capture-vs-review is therefore always small on a normal submit, so
replaying audit scan 1 dispatched to the provider exactly as before the fix.
The candidate age is the signal that matters, because entry/SL/TP are copied
from the origin candidate while factors are rebuilt live.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_review.symbol_parity import evaluate_symbol_parity
from ai_review.timestamp_contract import evaluate_review_freshness

CFG = {"MISMATCH_WARN_MAX_SECONDS": 120}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime(2026, 8, 12, 6, 38, 7, tzinfo=timezone.utc)


# --- F1 ---------------------------------------------------------------------


def test_scan1_replay_blocks_on_stale_candidate_despite_fresh_capture():
    """Audit scan 1: capture ~9s old, candidate 1429s old -> must NOT dispatch."""
    now = _now()
    result = evaluate_review_freshness(
        {
            "review_timestamp": _iso(now),
            "scan_timestamp": _iso(now - timedelta(seconds=1429)),
        },
        {"captured_at": _iso(now - timedelta(seconds=9))},
        CFG,
    )
    assert result["ok"] is False
    reasons = [b["reason"] for b in result["blocking"]]
    assert "stale_candidate_levels" in reasons
    # The capture itself was genuinely fresh — that is the whole trap.
    assert result["captureSkewSeconds"] == pytest.approx(9, abs=1)
    assert "capture_skew_exceeded" not in reasons
    assert result["candidateAgeSeconds"] == pytest.approx(1429, abs=1)


def test_levels_provenance_names_the_two_clocks():
    now = _now()
    result = evaluate_review_freshness(
        {
            "review_timestamp": _iso(now),
            "scan_timestamp": _iso(now - timedelta(seconds=1429)),
        },
        {"captured_at": _iso(now - timedelta(seconds=9))},
        CFG,
    )
    provenance = result["levelsProvenance"]
    assert provenance["levelsFrom"] == "origin_candidate"
    assert provenance["sharedClock"] is False
    assert provenance["levelsAsOf"] != provenance["factorsRebuiltAt"]


def test_fresh_candidate_and_capture_passes():
    now = _now()
    result = evaluate_review_freshness(
        {
            "review_timestamp": _iso(now),
            "scan_timestamp": _iso(now - timedelta(seconds=8)),
        },
        {"captured_at": _iso(now - timedelta(seconds=3))},
        CFG,
    )
    assert result["ok"] is True
    assert result["blocking"] == []
    assert result["levelsProvenance"]["sharedClock"] is True


def test_stale_capture_still_blocks():
    now = _now()
    result = evaluate_review_freshness(
        {
            "review_timestamp": _iso(now),
            "scan_timestamp": _iso(now - timedelta(seconds=5)),
        },
        {"captured_at": _iso(now - timedelta(seconds=600))},
        CFG,
    )
    assert result["ok"] is False
    assert "capture_skew_exceeded" in [b["reason"] for b in result["blocking"]]


def test_missing_capture_timestamp_blocks_rather_than_warns():
    """Unprovable capture age must fail closed."""
    now = _now()
    result = evaluate_review_freshness(
        {"review_timestamp": _iso(now), "scan_timestamp": _iso(now)},
        {},
        CFG,
    )
    assert result["ok"] is False
    assert "capture_timestamp_missing" in [b["reason"] for b in result["blocking"]]


def test_candidate_age_enforcement_is_config_reversible():
    now = _now()
    ctx = {
        "review_timestamp": _iso(now),
        "scan_timestamp": _iso(now - timedelta(seconds=1429)),
    }
    meta = {"captured_at": _iso(now - timedelta(seconds=9))}
    off = evaluate_review_freshness(
        ctx, meta, {**CFG, "ENFORCE_CANDIDATE_AGE_PRE_PROVIDER": False}
    )
    assert off["ok"] is True
    # Still reported, just not blocking.
    assert any("candidate scan_timestamp" in w for w in off["warnings"])
    assert off["candidateAgeSeconds"] == pytest.approx(1429, abs=1)


def test_comparison_basis_is_reported():
    now = _now()
    with_review = evaluate_review_freshness(
        {"review_timestamp": _iso(now), "scan_timestamp": _iso(now)},
        {"captured_at": _iso(now)},
        CFG,
    )
    assert with_review["comparisonBasis"] == "review_timestamp"
    without_review = evaluate_review_freshness(
        {"scan_timestamp": _iso(now)}, {"captured_at": _iso(now)}, CFG
    )
    assert without_review["comparisonBasis"] == "scan_timestamp"
    # No review_timestamp means no candidate age to compare.
    assert without_review["candidateAgeSeconds"] is None


# --- F5 ---------------------------------------------------------------------


def test_parity_rejects_when_there_is_nothing_to_compare_against():
    """No candidate and no resolved pair is a reject, not a skip."""
    parity = evaluate_symbol_parity("SI=F", None, engine_pair=None)
    assert parity["ok"] is False
    assert parity["symbol_alias_applied"] is None


def test_parity_rejects_empty_engine_keys_for_any_symbol():
    parity = evaluate_symbol_parity("BTCUSDT", {}, engine_pair={})
    assert parity["ok"] is False
