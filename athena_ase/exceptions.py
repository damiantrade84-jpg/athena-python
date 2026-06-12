"""ASE-specific exceptions."""

from __future__ import annotations


class LegacyEngineBypassed(Exception):
    """Legacy Engine A scoring is disabled for an ASE-promoted model family."""

    def __init__(self, family: str, *, symbol: str = "", entry_point: str = ""):
        self.family = family
        self.symbol = symbol
        self.entry_point = entry_point
        detail = f"legacy Engine A bypassed for promoted family={family}"
        if symbol:
            detail += f" symbol={symbol}"
        if entry_point:
            detail += f" entry={entry_point}"
        super().__init__(detail)


class HoldoutAlreadyEvaluated(Exception):
    """Holdout may be evaluated exactly once per family-horizon."""

    def __init__(self, family: str, horizon: str):
        self.family = family
        self.horizon = horizon
        super().__init__(f"holdout already evaluated for {family}/{horizon}")
