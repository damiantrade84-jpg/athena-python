from __future__ import annotations

from engine_a_v3.session_forex import (
    parse_utc,
    session_quality_score,
    volume_spike_ratio,
)
from engine_a_v3.setups import _efficiency_ratio


def session_context_score(
    primary: list[dict],
    *,
    direction: str | None,
    level_style: str = "trend",
) -> tuple[float, dict[str, float]]:
    """Session-context composite score for forex-native setups.

    score = session_quality * direction_evidence * flow_confirmation * timing_precision
    Weights match the v3 forex rebuild spec; flow uses volume proxy until DXY/COT wired.
    ``level_style`` "mean_reversion" mirrors the direction evidence: a fade is
    confirmed by the stretch it reverts, whose net move points against the fade.
    """
    if not primary or direction not in {"LONG", "SHORT"}:
        return 0.0, {}

    as_of = parse_utc(primary[-1].get("time") or primary[-1].get("datetime"))
    if as_of is None:
        return 0.0, {}

    session_quality, session_label = session_quality_score(as_of)
    vol_ratio = volume_spike_ratio(primary)
    efficiency, net = _efficiency_ratio(primary, 20)
    if str(level_style or "").strip().lower() == "mean_reversion":
        direction_aligned = (direction == "SHORT" and net > 0) or (
            direction == "LONG" and net < 0
        )
    else:
        direction_aligned = (direction == "LONG" and net > 0) or (
            direction == "SHORT" and net < 0
        )
    direction_evidence = 0.35 * min(1.0, vol_ratio / 2.0) * (1.0 if direction_aligned else 0.5)
    flow_confirmation = 0.35 * min(1.0, vol_ratio / 1.5)
    timing_precision = 0.30
    if 0.2 <= efficiency <= 0.3:
        timing_precision *= 1.0
    elif efficiency < 0.2 or efficiency > 0.3:
        timing_precision *= 0.6

    score = round(session_quality * (direction_evidence + flow_confirmation + timing_precision), 4)
    components = {
        "session_quality": round(session_quality, 4),
        "session_label": session_label,
        "direction_evidence": round(direction_evidence, 4),
        "flow_confirmation": round(flow_confirmation, 4),
        "timing_precision": round(timing_precision, 4),
        "efficiency_at_entry": round(efficiency, 4),
    }
    return score, components


def session_score_passes(
    primary: list[dict],
    *,
    direction: str | None,
    min_score: float,
    level_style: str = "trend",
) -> bool:
    score, _ = session_context_score(
        primary, direction=direction, level_style=level_style
    )
    return score >= float(min_score)
