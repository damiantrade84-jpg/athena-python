"""OX Book significance discipline.

Multiple-testing rules from the evidence base:
  - Bailey/Borwein/Lopez de Prado/Zhu: a backtest without a count of trials attempted
    is uninterpretable. The TrialRegistry appends every evaluation to a JSONL file so
    the trial count N is always known and auditable.
  - Harvey/Liu/Zhu: with hundreds of factors tried across academia, a new claim needs
    t-stat > 3.0, not 2.0. OX promotion claims must clear settings.t_stat_hurdle().
  - McLean/Pontiff: documented edges lose ~58% of return post-publication. Sizing
    helpers therefore expect only (1 - decay_haircut) of backtest expectancy.

Simplification (labelled assumption): full Deflated Sharpe needs the variance of SR
across trials; v0 gates on the raw t-stat of mean R plus mandatory trial logging
instead. Upgrade path: compute DSR from registry variance once enough trials exist.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ox_book import settings

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TrialRegistry:
    """Append-only JSONL log of every backtest evaluation performed."""

    def __init__(self, path: str | None = None) -> None:
        raw = path or settings.trial_log_path()
        self.path = raw if os.path.isabs(raw) else os.path.join(_REPO_ROOT, raw)

    def record(self, payload: dict[str, Any]) -> None:
        import datetime as _dt

        entry = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(), **payload}
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def count(self) -> int:
        if not os.path.exists(self.path):
            return 0
        total = 0
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    total += 1
        return total


def clears_promotion_bar(t_stat: float | None) -> bool:
    """True only when the measured t-stat clears the multiple-testing hurdle."""
    if t_stat is None:
        return False
    return t_stat > settings.t_stat_hurdle()


def haircut_expectancy(exp_r: float | None) -> float | None:
    """Backtest expectancy scaled by (1 - DECAY_HAIRCUT); sizing input, never display edge."""
    if exp_r is None:
        return None
    return exp_r * (1.0 - settings.decay_haircut())
