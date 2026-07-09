"""Shared Engine A V3 trade-tier classification (scanner + scoring parity)."""

from __future__ import annotations

from typing import Any


def classify_engine_a_v3_signal(signal: dict[str, Any], pair: dict[str, Any]) -> tuple[str, str]:
    """Return (tier, reason) where tier is 'trade' | 'watchlist' | 'skip'."""
    decision = str(signal.get("decision") or "NO_SIGNAL").upper()
    reasons = [str(value) for value in signal.get("rejectionReasons") or [] if value]
    reason = "; ".join(dict.fromkeys(reasons))
    if decision == "NO_SIGNAL":
        return "skip", reason or "V3 specialist returned NO_SIGNAL"
    if decision == "WATCH":
        return "watchlist", reason or "V3 specialist is awaiting confirmation"
    if decision != "TRADE" or signal.get("qualified") is not True:
        return "skip", reason or "V3 signal is not trade-qualified"
    if signal.get("engineATradeEnabled") is not True:
        return "watchlist", reason or "V3 execution eligibility is disabled"
    if not pair.get("enabled", True):
        return "watchlist", reason or "Pair is disabled"
    if signal.get("exchangeClosed"):
        return "watchlist", reason or "Exchange closed"
    if signal.get("directionConflicted") is True:
        return "watchlist", reason or "V3 direction conflicted with intermarket/BTC"

    try:
        from config import CONFIG
        from scoring import (
            _signal_confidence_for_gate,
            get_min_confidence_threshold,
            get_pair_score_group,
        )

        if bool(CONFIG.get("ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", False)):
            min_conf = get_min_confidence_threshold(pair)
            conf = _signal_confidence_for_gate(signal)
            if conf is None:
                conf_raw = signal.get("scoreNorm")
                try:
                    conf = float(conf_raw) if conf_raw is not None else None
                except (TypeError, ValueError):
                    conf = None
            if conf is not None and conf < min_conf:
                score_group = get_pair_score_group(pair)
                return (
                    "watchlist",
                    f"Engine A confidence {conf:.2f} below {score_group} minimum {min_conf:.2f}",
                )
    except Exception:
        # Fail closed on gate evaluation errors: do not promote to trade.
        return "watchlist", reason or "V3 confidence gate unavailable"

    return "trade", "V3 specialist trade-qualified"


def retier_v3_after_score_adjust(signal: dict[str, Any]) -> dict[str, Any]:
    """Demote TRADE→WATCH when adjusted confluence falls below threshold.

    Never upgrades WATCH/NO_SIGNAL to TRADE. Mutates signal in place.
    """
    decision = str(signal.get("decision") or "").upper()
    if decision != "TRADE":
        return signal
    try:
        score = float(signal.get("confluenceScore") or signal.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        threshold = float(
            signal.get("confluenceThreshold")
            or signal.get("scanThreshold")
            or 0.0
        )
    except (TypeError, ValueError):
        threshold = 0.0
    if threshold <= 0:
        return signal
    if score + 1e-12 >= threshold:
        return signal

    signal["decision"] = "WATCH"
    signal["qualified"] = False
    signal["trade"] = False
    signal["executable"] = False
    reasons = list(signal.get("rejectionReasons") or [])
    reasons.append("intermarket_score_below_trade_threshold")
    signal["rejectionReasons"] = list(dict.fromkeys(reasons))
    return signal
