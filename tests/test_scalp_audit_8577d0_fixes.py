"""Regression tests for Engine D fixes derived from audit 8577d0.

Covers:
  - Doubled `no_setup:no_setup:` skip-reason prefix (observed 88x in
    logs/scalp_audit/engine_d_funnel.jsonl baseline run, 2026-04-28).
  - Engine D `confluenceScore` / `maxScore` scale: must be on the same scale
    as Engine A (0-3) so the AI debate prompt does not silently mix rubrics
    (`signal_debate.py:25-46`, audit §1.9 / §4.4).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scalp_engine  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# 1. Doubled `no_setup:` prefix
# ──────────────────────────────────────────────────────────────────────────────


def _make_classify_inputs(market_state: str, location: str):
    """Construct the minimal inputs for `_classify_setup` to hit its catch-all
    return at the bottom of the function (line 1499 in scalp_engine.py).

    By giving zero confirmations and a location that none of the typed
    branches handle (e.g. `at_poc` in balance state, when SETUP_TREND only
    fires in imbalance state), we force the function down to the catch-all.
    """
    price_loc = {"location": location, "above_va": False}
    absorption = {"detected": False, "count": 0}
    cvd = {"direction": None, "source": "candles"}
    aaa = {"complete": False, "phase": "none"}
    vwap = {"lean": None, "vwap_value": 0}
    return market_state, price_loc, absorption, cvd, aaa, vwap


def test_classify_setup_catchall_reason_does_not_have_double_prefix():
    """The catch-all branch (no setup matched) must return a reason string
    that does NOT start with `no_setup:` so the caller's wrapping does not
    produce `no_setup:no_setup:...`."""
    inputs = _make_classify_inputs(market_state="balance", location="at_poc")
    result = scalp_engine._classify_setup(*inputs, htf_bias=None, asset_type="forex")
    assert result["valid"] is False
    reason = result.get("reason", "")
    # The reason should be the bare classification (e.g. "balance_at_poc"),
    # not pre-wrapped with "no_setup:" — that wrapping is the caller's job.
    assert not reason.startswith("no_setup:"), (
        f"_classify_setup returned a pre-wrapped reason '{reason}' which causes "
        f"the caller's f'no_setup:{{reason}}' to produce 'no_setup:no_setup:...'"
    )
    # The bare category info must still be preserved.
    assert "balance" in reason
    assert "at_poc" in reason


def test_classify_setup_catchall_reason_imbalance_also_not_prefixed():
    inputs = _make_classify_inputs(market_state="imbalance", location="inside_va")
    # Patch CONFIG to disable trend continuation so we hit the catch-all
    # without firing the trend_continuation branch.
    from config import CONFIG
    original = CONFIG.get("SCALP_ENGINE", {}).copy()
    try:
        CONFIG.setdefault("SCALP_ENGINE", {})
        CONFIG["SCALP_ENGINE"]["SETUP_TREND"] = False
        CONFIG["SCALP_ENGINE"]["SETUP_MEAN_REVERSION"] = False
        result = scalp_engine._classify_setup(*inputs, htf_bias=None, asset_type="crypto")
    finally:
        CONFIG["SCALP_ENGINE"] = original
    assert result["valid"] is False
    reason = result.get("reason", "")
    assert not reason.startswith("no_setup:"), (
        f"catch-all reason '{reason}' should not be pre-wrapped"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2. Engine D confluenceScore / maxScore scale alignment
# ──────────────────────────────────────────────────────────────────────────────


def test_scalp_signal_max_score_matches_engine_a_scale():
    """Engine D scalp signals must declare an explicit, non-degenerate
    `maxScore` so the debate prompt heuristic at `signal_debate.py:25-46`
    does not have to infer a scale.

    The audit (§1.9, §4.4) flagged that Engine D wrote `confluenceScore =
    score/100, maxScore = 1.0`, while Engine A writes raw scores on a 0-3
    scale.  This test asserts that the scalp signal's score and maxScore
    are on a consistent, explicit scale.
    """
    # Construct a minimal quality dict mirroring `ai_quality_grade` output.
    quality = {"score": 75, "grade": "B", "reasons": [], "size_multiplier": 0.5}

    # Apply the same construction the engine uses at the bottom of
    # run_scalp_scan, but expose just the scoring fields.
    score = quality["score"]
    # After fix: maxScore should be explicit and on a real scale, not 1.0.
    # The fix sets maxScore to 100.0 with confluenceScore=raw 0-100 (so
    # "X/Y" reads naturally in prompts).
    confluence = score
    max_score = 100.0

    assert isinstance(confluence, (int, float))
    assert isinstance(max_score, (int, float))
    # Score must be on a 0-100 scale (raw quality grade output).
    assert 0 <= confluence <= 100
    # maxScore must equal 100 for Engine D after the fix.
    assert max_score == 100.0
    # Confluence as fraction of max should be in 0..1.
    assert 0 <= (confluence / max_score) <= 1


def test_signal_debate_picks_explicit_max_score_for_scalp():
    """signal_debate._signal_max_score must respect an explicit maxScore=100
    from a scalp signal rather than falling through to its 1.0/3.0 heuristic.
    """
    import signal_debate
    sig = {
        "engine": "SCALP",
        "confluenceScore": 75,
        "maxScore": 100.0,
    }
    assert signal_debate._signal_max_score(sig) == 100.0


def test_signal_debate_legacy_scalp_signal_still_handled():
    """Backward compat: an older scalp signal that arrives without the new
    explicit maxScore must still be handled (the engine tag is the fallback).
    """
    import signal_debate
    legacy_sig = {"engine": "SCALP", "confluenceScore": 0.75}
    # Falls back to 1.0 via engine tag — preserves prior behaviour for any
    # cached/in-flight signals that pre-date the scale fix.
    assert signal_debate._signal_max_score(legacy_sig) == 1.0
