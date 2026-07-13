"""Tests for the ATR freshness policy (F3 + F5).

The policy lives in ``atr_diagnostics.evaluate_freshness`` and is exposed via
``evaluate_freshness_from_config``. Default behaviour is OFF — the helper
returns ``stale=False, reason='freshness_disabled'`` so observability data
flows through but no execution gating fires.

When ENABLED is true and the ATR age exceeds the per-TF threshold, ``stale``
becomes true and ``would_block`` follows ``BLOCK_EXECUTION_ON_STALE_ATR``.
"""

from __future__ import annotations


def test_evaluate_freshness_disabled_returns_fresh():
    from atr_diagnostics import evaluate_freshness

    diag = {"atr_tf": "H4", "atr_age_seconds": 999999.0, "atr_value": 0.005}
    result = evaluate_freshness(
        diag, enabled=False, block_on_stale=False, max_age_overrides=None
    )
    assert result["enabled"] is False
    assert result["stale"] is False
    assert result["reason"] == "freshness_disabled"
    assert result["would_block"] is False


def test_evaluate_freshness_default_thresholds_h4_stale():
    from atr_diagnostics import evaluate_freshness

    # Default H4 threshold = 28800s, covering a confirmed H4 candle's
    # timestamp through the next four-hour close. 30000s is genuinely stale.
    diag = {"atr_tf": "H4", "atr_age_seconds": 30000.0, "atr_value": 0.005}
    result = evaluate_freshness(
        diag, enabled=True, block_on_stale=False, max_age_overrides=None
    )
    assert result["enabled"] is True
    assert result["stale"] is True
    assert "H4" in result["reason"]
    assert result["would_block"] is False  # block_on_stale=False


def test_evaluate_freshness_block_on_stale_true_makes_would_block_true():
    from atr_diagnostics import evaluate_freshness

    diag = {"atr_tf": "H1", "atr_age_seconds": 10000.0, "atr_value": 0.0005}
    result = evaluate_freshness(
        diag, enabled=True, block_on_stale=True, max_age_overrides=None
    )
    assert result["stale"] is True
    assert result["would_block"] is True


def test_evaluate_freshness_age_unknown_marks_stale():
    from atr_diagnostics import evaluate_freshness

    diag = {"atr_tf": "D1", "atr_age_seconds": None, "atr_value": 0.005}
    result = evaluate_freshness(
        diag, enabled=True, block_on_stale=False, max_age_overrides=None
    )
    assert result["stale"] is True
    assert result["reason"] == "atr_age_unknown"
    assert result["would_block"] is False


def test_evaluate_freshness_missing_atr_value_marks_stale():
    from atr_diagnostics import evaluate_freshness

    diag = {"atr_tf": "D1", "atr_age_seconds": 100.0, "atr_value": 0.0}
    result = evaluate_freshness(
        diag, enabled=True, block_on_stale=True, max_age_overrides=None
    )
    assert result["stale"] is True
    assert result["reason"] == "atr_value_missing"
    assert result["would_block"] is True


def test_evaluate_freshness_no_threshold_for_tf_not_stale():
    from atr_diagnostics import evaluate_freshness

    diag = {"atr_tf": "M1", "atr_age_seconds": 60.0, "atr_value": 0.001}
    result = evaluate_freshness(
        diag,
        enabled=True,
        block_on_stale=True,
        max_age_overrides={"H4": 18000},  # no M1 entry
    )
    assert result["stale"] is False
    assert result["reason"] == "no_threshold_for_tf"
    assert result["would_block"] is False


def test_evaluate_freshness_from_config_default_disabled():
    """``ATR_FRESHNESS`` defaults to ENABLED=false; nothing should block."""
    from atr_diagnostics import evaluate_freshness_from_config

    diag = {"atr_tf": "H4", "atr_age_seconds": 100000.0, "atr_value": 0.005}
    result = evaluate_freshness_from_config(diag, {})
    assert result["enabled"] is False
    assert result["would_block"] is False


def test_evaluate_freshness_from_config_full_block():
    from atr_diagnostics import evaluate_freshness_from_config

    diag = {"atr_tf": "H4", "atr_age_seconds": 20000.0, "atr_value": 0.005}
    cfg = {
        "ENABLED": True,
        "BLOCK_EXECUTION_ON_STALE_ATR": True,
        "MAX_AGE_SECONDS": {"H4": 18000},
    }
    result = evaluate_freshness_from_config(diag, cfg)
    assert result["stale"] is True
    assert result["would_block"] is True
    assert result["threshold_sec"] == 18000


def test_h4_confirmed_only_atr_age_follows_explicit_atr_freshness_policy():
    from atr_diagnostics import evaluate_freshness_from_config

    diag = {
        "atr_tf": "H4",
        "atr_age_seconds": 21471.5,
        "atr_value": 0.00329,
        "atr_confirmed_only": True,
    }

    disabled = evaluate_freshness_from_config(
        diag,
        {
            "ENABLED": False,
            "BLOCK_EXECUTION_ON_STALE_ATR": False,
            "MAX_AGE_SECONDS": {"H4": 18000},
        },
    )
    enabled = evaluate_freshness_from_config(
        diag,
        {
            "ENABLED": True,
            "BLOCK_EXECUTION_ON_STALE_ATR": False,
            "MAX_AGE_SECONDS": {"H4": 18000},
        },
    )

    assert disabled["reason"] == "freshness_disabled"
    assert disabled["stale"] is False
    assert enabled["stale"] is True
    assert enabled["reason"] == "atr_age_21471s_exceeds_H4_limit_18000s"
    assert enabled["would_block"] is False


def test_evaluate_freshness_handles_missing_diagnostics():
    from atr_diagnostics import evaluate_freshness

    result = evaluate_freshness(
        None, enabled=True, block_on_stale=True, max_age_overrides=None
    )
    assert result["stale"] is False
    assert result["reason"] == "no_atr_diagnostics"
    assert result["would_block"] is False


def test_evaluate_freshness_handles_invalid_age_type():
    from atr_diagnostics import evaluate_freshness

    diag = {"atr_tf": "H4", "atr_age_seconds": "not-a-number", "atr_value": 0.005}
    result = evaluate_freshness(
        diag, enabled=True, block_on_stale=True, max_age_overrides=None
    )
    assert result["stale"] is True
    assert result["reason"] == "atr_age_invalid"
    assert result["would_block"] is True
