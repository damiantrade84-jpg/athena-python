"""Focused tests for the dual-provider reconciler (Phase 0).

Covers: agreement, disagreement with primary/secondary/fail_closed
tiebreakers, single-provider fallback, both-fail error propagation,
conservative entryAllowedNow downgrade. No live API calls.
"""

from __future__ import annotations

import pytest

from ai_review.dual_provider import (
    reconcile_dual_provider,
    run_dual_provider,
)
from ai_review.provider_meta import ProviderChartReviewError


def test_both_succeed_agree_returns_primary_with_agreement_flag():
    p = {"decision": "VALID", "entryAllowedNow": False, "confidence": 80}
    s = {"decision": "VALID", "entryAllowedNow": False, "confidence": 78}
    out = reconcile_dual_provider(
        primary_response=p,
        primary_error=None,
        secondary_response=s,
        secondary_error=None,
        primary_name="openai",
        secondary_name="claude",
    )
    assert out["dualProvider"] is True
    assert out["dualProviderAgreement"] is True
    assert out["dualProviderPrimary"] == "openai"
    assert out["dualProviderSecondary"] == "claude"
    assert out["decision"] == "VALID"
    assert "secondaryDecision" not in out


def test_both_succeed_disagree_primary_tiebreaker_returns_primary():
    p = {"decision": "VALID", "entryAllowedNow": True, "confidence": 80}
    s = {"decision": "NO_TRADE", "entryAllowedNow": False, "confidence": 60}
    out = reconcile_dual_provider(
        primary_response=p,
        primary_error=None,
        secondary_response=s,
        secondary_error=None,
        primary_name="openai",
        secondary_name="claude",
        tiebreaker="primary",
    )
    assert out["dualProviderAgreement"] is False
    assert out["decision"] == "VALID"
    assert out["secondaryDecision"] == "no_trade"
    # Conservative downgrade: secondary said no → entryAllowedNow forced False
    assert out["entryAllowedNow"] is False
    assert out.get("dualProviderDowngradedEntryAllowed") is True


def test_both_succeed_disagree_secondary_tiebreaker_returns_secondary():
    p = {"decision": "VALID", "entryAllowedNow": True, "confidence": 80}
    s = {"decision": "NO_TRADE", "entryAllowedNow": False, "confidence": 60}
    out = reconcile_dual_provider(
        primary_response=p,
        primary_error=None,
        secondary_response=s,
        secondary_error=None,
        primary_name="openai",
        secondary_name="claude",
        tiebreaker="secondary",
    )
    assert out["dualProviderAgreement"] is False
    assert out["decision"] == "NO_TRADE"
    assert out["dualProviderTiebreakerUsed"] == "secondary"
    # Conservative downgrade still applies: primary said yes → forced False
    assert out["entryAllowedNow"] is False


def test_both_succeed_disagree_fail_closed_tiebreaker_returns_no_trade():
    p = {"decision": "VALID", "entryAllowedNow": True, "confidence": 80}
    s = {"decision": "NO_TRADE", "entryAllowedNow": False, "confidence": 60}
    out = reconcile_dual_provider(
        primary_response=p,
        primary_error=None,
        secondary_response=s,
        secondary_error=None,
        primary_name="openai",
        secondary_name="claude",
        tiebreaker="fail_closed",
    )
    assert out["decision"] == "NO_TRADE"
    assert out["entryAllowedNow"] is False
    assert out["dualProviderTiebreakerUsed"] == "fail_closed"
    assert out["primaryDecision"] == "valid"
    assert out["secondaryDecision"] == "no_trade"


def test_only_primary_succeeds_returns_primary_with_fallback_flag():
    p = {"decision": "VALID", "entryAllowedNow": False, "confidence": 80}
    out = reconcile_dual_provider(
        primary_response=p,
        primary_error=None,
        secondary_response=None,
        secondary_error=RuntimeError("claude down"),
        primary_name="openai",
        secondary_name="claude",
    )
    assert out["decision"] == "VALID"
    assert out["dualProviderFallback"] is True
    assert "dualProviderSecondaryError" in out


def test_only_secondary_succeeds_returns_secondary_with_fallback_flag():
    s = {"decision": "VALID", "entryAllowedNow": False, "confidence": 78}
    out = reconcile_dual_provider(
        primary_response=None,
        primary_error=RuntimeError("openai down"),
        secondary_response=s,
        secondary_error=None,
        primary_name="openai",
        secondary_name="claude",
    )
    assert out["decision"] == "VALID"
    assert out["dualProviderFallback"] is True
    assert out["dualProviderPrimaryFailed"] is True
    assert "dualProviderPrimaryError" in out


def test_both_fail_reraises_primary_error():
    err = ProviderChartReviewError("boom", provider_status="unknown", provider="openai", http_status=503)
    with pytest.raises(ProviderChartReviewError):
        reconcile_dual_provider(
            primary_response=None,
            primary_error=err,
            secondary_response=None,
            secondary_error=RuntimeError("claude also down"),
            primary_name="openai",
            secondary_name="claude",
        )


def test_both_fail_reraises_secondary_error_when_primary_error_none():
    err = RuntimeError("claude down")
    with pytest.raises(RuntimeError):
        reconcile_dual_provider(
            primary_response=None,
            primary_error=None,
            secondary_response=None,
            secondary_error=err,
            primary_name="openai",
            secondary_name="claude",
        )


def test_run_dual_provider_parallel_call():
    """End-to-end: two callables run in parallel and reconcile."""

    def primary_fn():
        return {"decision": "VALID", "entryAllowedNow": False, "confidence": 80}

    def secondary_fn():
        return {"decision": "VALID", "entryAllowedNow": False, "confidence": 78}

    out = run_dual_provider(
        primary_fn=primary_fn,
        secondary_fn=secondary_fn,
        primary_name="openai",
        secondary_name="claude",
        timeout_sec=5.0,
    )
    assert out["dualProvider"] is True
    assert out["dualProviderAgreement"] is True


def test_run_dual_provider_one_fails_uses_other():
    def primary_fn():
        raise RuntimeError("openai exploded")

    def secondary_fn():
        return {"decision": "VALID", "entryAllowedNow": False, "confidence": 78}

    out = run_dual_provider(
        primary_fn=primary_fn,
        secondary_fn=secondary_fn,
        primary_name="openai",
        secondary_name="claude",
        timeout_sec=5.0,
    )
    assert out["dualProviderFallback"] is True
    assert out["dualProviderPrimaryFailed"] is True
    assert out["decision"] == "VALID"


def test_conservative_entry_allowed_when_neither_says_no():
    """If both say entryAllowedNow True, reconciler leaves it True (no downgrade)."""
    p = {"decision": "VALID", "entryAllowedNow": True, "confidence": 80}
    s = {"decision": "VALID", "entryAllowedNow": True, "confidence": 78}
    out = reconcile_dual_provider(
        primary_response=p,
        primary_error=None,
        secondary_response=s,
        secondary_error=None,
        primary_name="openai",
        secondary_name="claude",
    )
    assert out["entryAllowedNow"] is True
    assert "dualProviderDowngradedEntryAllowed" not in out
