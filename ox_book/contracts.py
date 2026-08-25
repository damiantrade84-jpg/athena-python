"""OX Book contracts (pure data, no I/O)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OxParams:
    """Canonical OX configuration. One instance per engine — never per-market tuned."""

    fast: int = 15
    slow: int = 60
    atr_n: int = 14
    atr_mult: float = 3.0
    long_only: bool = True
    cost_per_side: float = 0.0002

    @property
    def key(self) -> str:
        return (
            f"ema{self.fast}/{self.slow}_atr{self.atr_n}x{self.atr_mult:g}"
            f"_{'L' if self.long_only else 'LS'}_c{self.cost_per_side:g}"
        )


@dataclass(frozen=True)
class OxTrade:
    entry_time: Any
    exit_time: Any
    direction: int
    entry: float
    exit: float
    risk: float
    R: float
    bars: int
    reason: str


@dataclass(frozen=True)
class OxMetrics:
    n: int = 0
    exp_r: float | None = None
    std_r: float | None = None
    sqn100: float | None = None
    t_stat: float | None = None
    win_rate: float | None = None
    profit_factor: float | None = None
    max_dd_r: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketEvaluation:
    """Evidence-gate result for one market under the canonical config."""

    symbol: str
    qualifies: bool
    reasons: list[str] = field(default_factory=list)
    edge_quality: float | None = None
    metrics_full: OxMetrics = field(default_factory=OxMetrics)
    metrics_oos: OxMetrics = field(default_factory=OxMetrics)
    plateau_pass_frac: float | None = None
    stressed_sqn100: float | None = None
    era_expectancies: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "qualifies": self.qualifies,
            "reasons": list(self.reasons),
            "edgeQuality": self.edge_quality,
            "metricsFull": self.metrics_full.to_dict(),
            "metricsOos": self.metrics_oos.to_dict(),
            "plateauPassFrac": self.plateau_pass_frac,
            "stressedSqn100": self.stressed_sqn100,
            "eraExpectancies": list(self.era_expectancies),
        }


@dataclass(frozen=True)
class BookVerdict:
    """Advisory book composition output. Never executable on its own."""

    members: list[MarketEvaluation]
    rejected: list[MarketEvaluation]
    canonical_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonicalKey": self.canonical_key,
            "members": [m.to_dict() for m in self.members],
            "rejected": [r.to_dict() for r in self.rejected],
        }
