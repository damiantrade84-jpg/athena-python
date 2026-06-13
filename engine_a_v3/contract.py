from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CONTRACT_VERSION = "3.0.0"


@dataclass(frozen=True)
class PriceZone:
    low: float
    high: float


@dataclass(frozen=True)
class Target:
    label: str
    price: float
    rr: float


@dataclass(frozen=True)
class PredicateResult:
    name: str
    passed: bool
    actual: Any
    expected: str


@dataclass(frozen=True)
class DataFreshness:
    allowed: bool
    policy: str
    lastConfirmedCandleTs: str | None
    reason: str


@dataclass(frozen=True)
class ValidationArtifact:
    artifactId: str
    family: str
    subclass: str
    horizon: str
    status: str
    metrics: tuple[tuple[str, Any], ...]
    provenanceSha256: str


@dataclass(frozen=True)
class EngineASetupSignal:
    contractVersion: str
    signalId: str
    engine: str
    pair: str
    symbol: str
    type: str
    scoreGroup: str
    family: str
    subclass: str
    horizon: str
    setupId: str | None
    decision: str
    qualified: bool
    direction: str | None
    decisionTime: str
    lastConfirmedCandleTs: str | None
    validUntil: str
    entryZone: PriceZone | None
    invalidation: float | None
    targets: tuple[Target, ...]
    price: float | None
    sl: float | None
    tp1: float | None
    tp2: float | None
    rr1: float | None
    rr2: float | None
    predicates: tuple[PredicateResult, ...]
    rejectionReasons: tuple[str, ...]
    dataFreshness: DataFreshness
    validationArtifact: ValidationArtifact | None
    validationStatus: str = "UNAVAILABLE"
    executionScope: str = "NONE"
    confluenceScore: None = None
    maxScore: None = None
    scoreNorm: None = None
    confidenceDetail: None = None
    factorDiagnostics: None = None
    engineATradeEnabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entryZone"] = (
            [self.entryZone.low, self.entryZone.high] if self.entryZone else None
        )
        payload["targets"] = [asdict(target) for target in self.targets]
        payload["predicates"] = [asdict(predicate) for predicate in self.predicates]
        payload["rejectionReasons"] = list(self.rejectionReasons)
        if self.validationArtifact is not None:
            artifact = asdict(self.validationArtifact)
            artifact["metrics"] = dict(self.validationArtifact.metrics)
            payload["validationArtifact"] = artifact
        payload.update(
            {
                "timestamp": self.decisionTime,
                "display": self.pair,
                "style": self.horizon,
                "entry": self.price,
                "tp": self.tp1,
                "rr": self.rr2,
                "trade": self.decision == "TRADE" and self.qualified,
                "executable": self.decision == "TRADE" and self.qualified,
                "signalTier": {
                    "TRADE": "trade",
                    "WATCH": "watchlist",
                    "NO_SIGNAL": "skip",
                }[self.decision],
                "signalClass": self.setupId,
                "engine_source": "ENGINE_A",
                "engine_name": "Engine A V3",
                "legacyDiagnosticsOnly": True,
            }
        )
        return payload
