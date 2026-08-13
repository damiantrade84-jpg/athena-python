"""Shared Engine A V3 trade-tier classification (scanner + scoring parity)."""

from __future__ import annotations

import math
from typing import Any


def _risk_reason(payload: dict[str, Any], fallback: str) -> str:
    reason = str(payload.get("reason") or "").strip()
    if reason:
        return reason
    reasons = payload.get("reasons")
    if isinstance(reasons, (list, tuple)):
        joined = "; ".join(str(value).strip() for value in reasons if str(value).strip())
        if joined:
            return joined
    return fallback


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
    # Decision aliases are not sufficient proof of a usable score. Cached or
    # malformed V3 rows must not remain trade-tier when every canonical score
    # path is missing or non-finite.
    score_norm = signal.get("scoreNorm")
    if score_norm is None:
        score_norm = signal.get("conviction")
    try:
        if score_norm is not None:
            canonical_norm = float(score_norm)
        else:
            canonical_score = float(signal.get("confluenceScore"))
            canonical_max = float(signal.get("maxScore"))
            canonical_norm = canonical_score / canonical_max if canonical_max > 0 else float("nan")
    except (TypeError, ValueError, ZeroDivisionError):
        canonical_norm = float("nan")
    if not math.isfinite(canonical_norm):
        return "watchlist", reason or "V3 canonical score missing or invalid"
    readiness_value = signal.get("entryReadiness")
    if readiness_value is not None:
        readiness = str(readiness_value).strip().upper() or "UNAVAILABLE"
        if readiness != "READY":
            # Optional Scan Config master: when entry-timing gates are off,
            # PENDING from trigger/M15–M30 oppose must not demote trade tier.
            # Missing/stale required closed TFs and CONFIG_ERROR still demote.
            try:
                from engine_a_v3.gate_toggles import entry_timing_gates_enabled

                _timing_on = entry_timing_gates_enabled()
            except Exception:
                _timing_on = False
            readiness_reason = str(
                signal.get("entryReadinessReason")
                or "execution timing is not ready"
            ).strip()
            _timing_pending = readiness == "PENDING" and (
                "trigger" in readiness_reason.lower()
                or "opposed" in readiness_reason.lower()
                or "neutral" in readiness_reason.lower()
                or "entry_timing_gates_disabled" in readiness_reason.lower()
            )
            if _timing_on or readiness != "PENDING" or not _timing_pending:
                return (
                    "watchlist",
                    f"Engine A entry {readiness.lower()}: {readiness_reason}",
                )
    if not pair.get("enabled", True):
        return "watchlist", reason or "Pair is disabled"
    if signal.get("exchangeClosed"):
        return "watchlist", reason or "Exchange closed"
    if signal.get("directionConflicted") is True:
        return "watchlist", reason or "V3 direction conflicted with intermarket/BTC"

    event_risk = signal.get("eventRisk")
    if event_risk:
        if not isinstance(event_risk, dict):
            return "watchlist", "V3 event-risk payload invalid"
        if bool(event_risk.get("hardBlock")):
            return "watchlist", _risk_reason(event_risk, "V3 event-risk hard block")
    macro_event_risk = signal.get("macroEventRisk")
    if macro_event_risk:
        if not isinstance(macro_event_risk, dict):
            return "watchlist", "V3 macro-event-risk payload invalid"
        if bool(macro_event_risk.get("blocked")):
            return "watchlist", _risk_reason(macro_event_risk, "V3 macro-event hard block")

    try:
        from config import CONFIG
        from scoring import (
            _signal_confidence_for_gate,
            _signal_independent_confidence_supplied,
            get_min_confidence_threshold,
            get_pair_score_group,
        )

        if bool(CONFIG.get("ENGINE_A_TRADE_MIN_CONFIDENCE_ENABLED", False)):
            min_conf = get_min_confidence_threshold(pair)
            conf = _signal_confidence_for_gate(signal)
            score_group = get_pair_score_group(pair)
            if conf is None:
                # V3 conviction/scoreNorm are the already-gated confluence score,
                # not independent confidence.  Skip this optional gate when no
                # independent confidence was published, but fail closed when a
                # supplied value is malformed or non-finite.
                if _signal_independent_confidence_supplied(signal):
                    return (
                        "watchlist",
                        f"Engine A confidence invalid for {score_group} minimum {min_conf:.2f}",
                    )
            elif conf < min_conf:
                return (
                    "watchlist",
                    f"Engine A confidence {conf:.2f} below {score_group} minimum {min_conf:.2f}",
                )
    except Exception:
        # Fail closed on gate evaluation errors: do not promote to trade.
        return "watchlist", reason or "V3 confidence gate unavailable"

    return "trade", "V3 specialist trade-qualified"


def demote_v3_trade_to_watch(
    signal: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Demote a V3 trade and clear every execution-facing alias."""
    if str(signal.get("decision") or "").upper() != "TRADE":
        return signal

    signal["decision"] = "WATCH"
    signal["qualified"] = False
    signal["engineATradeEnabled"] = False
    signal["trade"] = False
    signal["executable"] = False
    signal["execution_permitted"] = False
    signal["signalTier"] = "watchlist"
    signal["executionScope"] = "NONE"
    reasons = list(signal.get("rejectionReasons") or [])
    reasons.append(reason)
    signal["rejectionReasons"] = list(dict.fromkeys(reasons))
    return signal


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
    if not math.isfinite(score) or not math.isfinite(threshold):
        return demote_v3_trade_to_watch(signal, reason="invalid_adjusted_score")
    if threshold <= 0:
        return signal
    if score + 1e-12 >= threshold:
        return signal

    return demote_v3_trade_to_watch(
        signal,
        reason="intermarket_score_below_trade_threshold",
    )
