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
    decisionTimeMs: int = 0

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
        payload["confluenceScore"] = self.confluenceScore
        payload["maxScore"] = self.maxScore
        payload["scoreNorm"] = self.scoreNorm
        payload["confidence"] = self.confidence
        payload["entryZone"] = [self.entryZone[0], self.entryZone[1]]
        return payload

    def to_engine_a_dict(self, *, style: str | None = None) -> dict[str, Any]:
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


def is_ase_engine_a_signal(signal_a: dict | ASESignal | None) -> bool:
    if signal_a is None:
        return False
    if isinstance(signal_a, ASESignal):
        return True
    if signal_a.get("engine") == "ASE" or signal_a.get("aseEngine") is True:
        return True
    return bool(signal_a.get("modelFamily")) and signal_a.get("decisionStatus") in (
        "TRADE",
        "WATCH",
        "FLAT",
        "ERROR",
    )


def normalise_ase_for_engine_c(signal_a: dict) -> dict[str, Any]:
    status = str(signal_a.get("decisionStatus", "FLAT"))
    direction = signal_a.get("direction")
    norm = float(signal_a.get("scoreNorm", signal_a.get("signalStrength", 0) / 100.0))
    has_trade = status == "TRADE" and direction in ("LONG", "SHORT")
    has_watch = status == "WATCH" and direction in ("LONG", "SHORT")
    return {
        "score_norm": round(norm, 4),
        "direction": direction,
        "regime": signal_a.get("regime", "RANGING"),
        "sl": signal_a.get("sl"),
        "tp": signal_a.get("tp1"),
        "tp2": signal_a.get("tp2"),
        "rr": signal_a.get("rr1", 0),
        "raw_score": float(signal_a.get("confluenceScore", signal_a.get("signalStrength", 0))),
        "max_score": float(signal_a.get("maxScore", 100)),
        "has_signal": has_trade,
        "has_partial_signal": has_watch and not has_trade,
        "trade_enabled": True,
        "trade_gate": {"source": "ASE", "bypassed": True},
        "cot_active": False,
        "carry_active": False,
        "style": signal_a.get("style", signal_a.get("horizon", "swing")),
        "confidence": float(signal_a.get("confidence", signal_a.get("probabilityPositive", 0.5))),
        "ase": True,
        "decisionStatus": status,
    }


def error_signal(
    *,
    instrument: str,
    family: str,
    horizon: Horizon,
    reason: str,
    gate_result: dict[str, Any] | None = None,
    model_version: str = "none",
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
    )
