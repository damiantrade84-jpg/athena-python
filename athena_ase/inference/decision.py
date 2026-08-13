"""ASE TRADE/WATCH/FLAT decision rule (v2.1 §8)."""

from __future__ import annotations

import math
from typing import Any, Literal

DecisionStatus = Literal["TRADE", "WATCH", "FLAT", "ERROR"]

# Deterministic floor on the TRADE expected-R threshold. thr_family is chosen
# by the training pipeline's expectancy search, which has no positivity
# constraint and has produced negative thresholds (e.g. crypto/swing -0.387 on
# 2026-07-03) — a negative threshold would let barely-positive or even
# negative expected-R signals reach TRADE. 0.10R matches the training
# pipeline's own fallback threshold (select_expected_net_r_threshold).
MIN_TRADE_EXPECTED_R = 0.10


def apply_decision_rule(
    *,
    p_cal: float,
    expected_net_r: float,
    thr_family: float,
    data_quality: dict[str, Any] | None = None,
) -> DecisionStatus:
    dq = data_quality or {}
    if not dq.get("coreOk", True):
        return "FLAT"
    try:
        p_cal = float(p_cal)
        expected_net_r = float(expected_net_r)
        thr_family = float(thr_family)
    except (TypeError, ValueError):
        return "FLAT"
    if not all(math.isfinite(value) for value in (p_cal, expected_net_r, thr_family)):
        return "FLAT"
    margin = abs(p_cal - 0.5)
    thr_effective = max(thr_family, MIN_TRADE_EXPECTED_R)
    if expected_net_r >= thr_effective and p_cal >= 0.55 and margin >= 0.05:
        return "TRADE"
    if expected_net_r > 0:
        return "WATCH"
    return "FLAT"


def signal_strength(expected_net_r: float, p_cal: float = 0.5) -> int:
    """Honest 0-100 conviction.

    Blends directional confidence (the calibrated probability's edge over a
    coin flip) with expected payoff (expected net R vs the 1R take-profit unit,
    K_TP), combined as a geometric mean so a high score requires *both*. Unlike
    the legacy expected-R-only proxy — which pinned to 100 once expected_net_R
    reached 0.5R, i.e. at a mere ~0.60 win probability — this does not saturate
    for marginal-confidence setups: 100 is reserved for near-certain, ~1R+
    trades. Mirrors Engine A's normalized-conviction (score_norm) convention,
    scaled to 0-100.
    """
    try:
        p_cal = float(p_cal)
        expected_net_r = float(expected_net_r)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(p_cal) or not math.isfinite(expected_net_r):
        return 0
    prob_edge = max(0.0, min(1.0, 2.0 * (p_cal - 0.5)))
    payoff = max(0.0, min(1.0, expected_net_r))  # 1R reference (K_TP)
    conviction = math.sqrt(prob_edge * payoff)
    return int(round(100.0 * conviction))


def legacy_expected_r_strength(expected_net_r: float) -> int:
    """Pre-honest strength proxy: expected net R clamped to [0, 0.5R] → 0-100.

    Retained as an additional, transparent scoring option (surfaced under
    predictionDiagnostics) so the raw expected-R magnitude is not lost.
    """
    try:
        expected_net_r = float(expected_net_r)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(expected_net_r):
        return 0
    return int(round(max(0.0, min(1.0, expected_net_r / 0.5)) * 100))
