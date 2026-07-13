"""Shared H1 vs lower-TF entry improvement metrics (diagnostic experiments)."""

from __future__ import annotations

from typing import Any


# Minimum absolute score gain to count as an improvement without a gate upgrade.
SCORE_DELTA_THRESHOLD = 0.05


def side_improved(
    *,
    score_delta: float | None,
    gate_upgraded: bool,
    score_delta_threshold: float = SCORE_DELTA_THRESHOLD,
) -> bool:
    """Per-side improvement vs H1 baseline.

    True when:
    - score_delta > threshold, or
    - gate/trigger upgraded and score_delta is non-negative (not a score regression).
    """
    if score_delta is not None and score_delta > score_delta_threshold:
        return True
    if gate_upgraded and score_delta is not None and score_delta >= 0:
        return True
    if gate_upgraded and score_delta is None:
        # Gate upgrade with no score available still counts as side improvement.
        return True
    return False


def pair_improved_overall(
    side_rows: list[dict[str, Any]],
    *,
    score_delta_threshold: float = SCORE_DELTA_THRESHOLD,
) -> dict[str, Any]:
    """Pair-level rollup: any side improves AND sum of available deltas >= 0.

    Each ``side_rows`` item may include:
      - score_delta (float | None)
      - gate_upgraded (bool)
      - improved (optional precomputed bool)
    """
    improved_sides = 0
    deltas: list[float] = []
    for row in side_rows:
        delta = row.get("score_delta")
        if delta is not None:
            try:
                deltas.append(float(delta))
            except (TypeError, ValueError):
                pass
        if "improved" in row:
            improved = bool(row["improved"])
        else:
            improved = side_improved(
                score_delta=delta if delta is None else float(delta),
                gate_upgraded=bool(row.get("gate_upgraded")),
                score_delta_threshold=score_delta_threshold,
            )
        if improved:
            improved_sides += 1
    delta_sum = sum(deltas) if deltas else None
    overall = bool(improved_sides > 0 and (delta_sum is None or delta_sum >= 0))
    return {
        "any_side_improved": improved_sides > 0,
        "improved_side_count": improved_sides,
        "score_delta_sum": delta_sum,
        "overall_improved": overall,
    }
