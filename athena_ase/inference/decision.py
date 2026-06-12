"""ASE TRADE/WATCH/FLAT decision rule (v2.1 §8)."""

from __future__ import annotations

from typing import Any, Literal

DecisionStatus = Literal["TRADE", "WATCH", "FLAT", "ERROR"]


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
    margin = abs(p_cal - 0.5)
    if expected_net_r >= thr_family and p_cal >= 0.55 and margin >= 0.05:
        return "TRADE"
    if expected_net_r > 0:
        return "WATCH"
    return "FLAT"


def signal_strength(expected_net_r: float) -> int:
    return int(round(max(0.0, min(1.0, expected_net_r / 0.5)) * 100))
