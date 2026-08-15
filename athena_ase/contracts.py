"""Canonical ASE signal contract (v2.1 §14)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


DecisionStatus = Literal["TRADE", "WATCH", "FLAT", "ERROR"]
Direction = Literal["LONG", "SHORT", "NONE"]
Horizon = Literal["intraday", "swing"]


def _direction_label(value: int | str | None) -> Direction:
    if isinstance(value, str):
        up = value.strip().upper()
        if up in ("LONG", "SHORT", "NONE"):
            return up  # type: ignore[return-value]
    if value in (1, "1", "+1"):
        return "LONG"
    if value in (-1, "-1"):
        return "SHORT"
    return "NONE"


@dataclass(frozen=True)
class ASESignal:
    engineVersion: str
    modelFamily: str
    modelVersion: str
    horizon: Horizon
    decisionStatus: DecisionStatus
    direction: Direction
    expectedNetR: float
    expectedNetBps: float
    probabilityPositive: float
    decisionMargin: float
    signalStrength: int
    returnQ: dict[str, float]
    maeQ: dict[str, float]
    mfeQ: dict[str, float]
    holdQ: dict[str, float]
    entryReference: float
    entryZone: tuple[float, float]
    sl: float
    tp1: float
    tp2: float
    maxHoldBars: int
    primarySignals: list[dict[str, Any]]
    predictionDiagnostics: dict[str, Any]
    dataQuality: dict[str, Any]
    modelHealth: dict[str, Any]
    instrument: str = ""
    display: str = ""
    decisionTimeMs: int = 0
    # WO Phases 1-2: diagnostic-only shadow context. Optional so old journal
    # rows still parse; must never influence decisionStatus/direction/sizing.
    fxContext: dict[str, Any] | None = None
    triangular: dict[str, Any] | None = None

    @property
    def confluenceScore(self) -> int:
        return self.signalStrength

    @property
    def maxScore(self) -> int:
        return 100

    @property
    def scoreNorm(self) -> float:
        return self.signalStrength / 100.0

    @property
    def confidence(self) -> float:
        return self.probabilityPositive

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["display"] = self.display or resolve_instrument_display(self.instrument)
        payload["confluenceScore"] = self.confluenceScore
        payload["maxScore"] = self.maxScore
        payload["scoreNorm"] = self.scoreNorm
        payload["confidence"] = self.confidence
        payload["entryZone"] = [self.entryZone[0], self.entryZone[1]]
        return payload

    def to_execution_dict(self, *, style: str | None = None) -> dict[str, Any]:
        """Execution-bridge payload only — not an Engine A scan slot."""
        payload = self.to_dict()
        payload.update(
            {
                "engine": "ASE",
                "aseEngine": True,
                "style": style or self.horizon,
                "tradeStyle": style or self.horizon,
                "tp1": self.tp1,
                "rr1": abs(self.tp1 - self.entryReference)
                / max(abs(self.entryReference - self.sl), 1e-9),
                "price": self.entryReference,
            }
        )
        return payload


def resolve_instrument_display(instrument: str, display: str | None = None) -> str:
    """Human/broker name (XAU/USD) for an ASE catalog id (GC=F)."""
    text = str(display or "").strip()
    if text:
        return text
    key = str(instrument or "").strip()
    if not key:
        return ""
    try:
        from athena_ase.instruments import instrument_by_symbol
    except Exception:
        return key
    inst = instrument_by_symbol(key)
    if inst is None:
        return key
    return str(inst.display or inst.symbol or key)


def error_signal(
    *,
    instrument: str,
    family: str,
    horizon: Horizon,
    reason: str,
    gate_result: dict[str, Any] | None = None,
    model_version: str = "none",
    display: str | None = None,
) -> ASESignal:
    health: dict[str, Any] = {
        "artifactHash": "",
        "trainedAt": "",
        "brier": None,
        "driftScore": 0.0,
        "gateResult": gate_result or {"ok": False, "reason": reason},
        "errorReason": reason,
    }
    return ASESignal(
        engineVersion="2.1.0",
        modelFamily=family,
        modelVersion=model_version,
        horizon=horizon,
        decisionStatus="ERROR",
        direction="NONE",
        expectedNetR=0.0,
        expectedNetBps=0.0,
        probabilityPositive=0.0,
        decisionMargin=0.0,
        signalStrength=0,
        returnQ={},
        maeQ={},
        mfeQ={},
        holdQ={},
        entryReference=0.0,
        entryZone=(0.0, 0.0),
        sl=0.0,
        tp1=0.0,
        tp2=0.0,
        maxHoldBars=0,
        primarySignals=[],
        predictionDiagnostics={"error": reason},
        dataQuality={"coreOk": False, "route": "none", "missingFeeds": []},
        modelHealth=health,
        instrument=instrument,
        display=resolve_instrument_display(instrument, display),
    )


def flat_signal(
    *,
    instrument: str,
    family: str,
    horizon: Horizon,
    model_version: str,
    gate_result: dict[str, Any],
    data_quality: dict[str, Any] | None = None,
    model_health: dict[str, Any] | None = None,
    primary_signals: list[dict[str, Any]] | None = None,
    display: str | None = None,
) -> ASESignal:
    return ASESignal(
        engineVersion="2.1.0",
        modelFamily=family,
        modelVersion=model_version,
        horizon=horizon,
        decisionStatus="FLAT",
        direction="NONE",
        expectedNetR=0.0,
        expectedNetBps=0.0,
        probabilityPositive=0.0,
        decisionMargin=0.0,
        signalStrength=0,
        returnQ={},
        maeQ={},
        mfeQ={},
        holdQ={},
        entryReference=0.0,
        entryZone=(0.0, 0.0),
        sl=0.0,
        tp1=0.0,
        tp2=0.0,
        maxHoldBars=0,
        primarySignals=primary_signals or [],
        predictionDiagnostics={},
        dataQuality=data_quality or {"coreOk": True, "route": "core", "missingFeeds": []},
        modelHealth=model_health
        or {"artifactHash": "", "trainedAt": "", "brier": None, "driftScore": 0.0, "gateResult": gate_result},
        instrument=instrument,
        display=resolve_instrument_display(instrument, display),
    )
