from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


CONTRACT_VERSION = "3.1.0"


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
    profileSha256: str | None = None


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
    # Continuous quality fields (repopulated by the quant scorer; V3 left these None).
    confluenceScore: float | None = None
    maxScore: float | None = None
    scoreNorm: float | None = None
    conviction: float | None = None
    confluenceThreshold: float | None = None
    factorScores: dict[str, Any] | None = None
    confidenceDetail: dict[str, Any] | None = None
    factorDiagnostics: dict[str, Any] | None = None
    scoringProfile: dict[str, Any] | None = None
    exitPolicy: str = "SINGLE_TP1"
    componentScores: dict[str, Any] | None = None
    engineATradeEnabled: bool = False
    entryTimeframe: str | None = None
    triggerConfirmed: bool | None = None
    levelGateMode: str = "enforced"
    levelAdvisories: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entryZone"] = (
            [self.entryZone.low, self.entryZone.high] if self.entryZone else None
        )
        payload["targets"] = [asdict(target) for target in self.targets]
        payload["predicates"] = [asdict(predicate) for predicate in self.predicates]
        payload["rejectionReasons"] = list(self.rejectionReasons)
        payload["levelAdvisories"] = [dict(item) for item in self.levelAdvisories]
        if self.validationArtifact is not None:
            artifact = asdict(self.validationArtifact)
            artifact["metrics"] = dict(self.validationArtifact.metrics)
            payload["validationArtifact"] = artifact
        scored_at = datetime.now(timezone.utc).isoformat()
        payload.update(
            {
                # Wall-clock scan/eval time for execution freshness gates (not candle time).
                "timestamp": scored_at,
                "scoredAt": scored_at,
                "display": self.pair,
                "style": self.horizon,
                "entry": self.price,
                "tp": self.tp1,
                # Headline rr must describe the headline tp (tp1) — the level the
                # default SINGLE_TP1 policy executes. rr2 stays available for the
                # TP2 runner under SPLIT_50_50.
                "rr": self.rr1 if self.rr1 is not None else self.rr2,
                "trade": self.decision == "TRADE" and self.qualified,
                "executable": self.decision == "TRADE" and self.qualified,
                "signalTier": {
                    "TRADE": "trade",
                    "WATCH": "watchlist",
                    "NO_SIGNAL": "skip",
                }.get(self.decision, "skip"),
                "signalClass": self.setupId,
                "score": self.confluenceScore,
                "threshold": self.confluenceThreshold,
                "engine_source": "ENGINE_A",
                "engine_name": "Engine A V3",
                "legacyDiagnosticsOnly": True,
            }
        )
        return payload
