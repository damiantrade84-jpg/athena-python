from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from engine_a_v3.contract import (
    CONTRACT_VERSION,
    DataFreshness,
    EngineASetupSignal,
    PredicateResult,
)
from engine_a_v3.levels import (
    build_mean_reversion_levels,
    build_structural_levels,
)
from engine_a_v3.promotion import PromotionRegistry, production_registry
from engine_a_v3.quant_scorer import QuantScore, score_pair
from engine_a_v3.profile import baseline_profile
from engine_a_v3.routing import route_specialist


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        from config import CONFIG
        offset = int(CONFIG.get("SERVER_TZ_OFFSET_HOURS", 2))
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=offset))).astimezone(timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _validate_candles(candles: dict[str, list[dict]]) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    from config import CONFIG
    try:
        minimums = {
            "D1": int(CONFIG["ENGINE_A_MIN_D1_BARS"]),
            "H4": int(CONFIG["ENGINE_A_MIN_H4_BARS"]),
            "H1": int(CONFIG["ENGINE_A_MIN_H1_BARS"]),
        }
    except (KeyError, TypeError, ValueError):
        return False, ("engine_a_v3_history_config_invalid",)
    if minimums["D1"] < 220 or minimums["H4"] < 50 or minimums["H1"] < 50:
        return False, ("engine_a_v3_history_config_unsafe",)
    for timeframe in ("D1", "H4", "H1"):
        rows = candles.get(timeframe)
        if not isinstance(rows, list):
            reasons.append(f"{timeframe.lower()}_candles_missing")
            continue
        if len(rows) < minimums[timeframe]:
            reasons.append(f"{timeframe.lower()}_history_insufficient")
            continue
        previous_ts = None
        for candle in rows:
            if not isinstance(candle, dict):
                reasons.append(f"{timeframe.lower()}_candle_malformed")
                break
            try:
                open_ = float(candle["open"])
                high = float(candle["high"])
                low = float(candle["low"])
                close = float(candle["close"])
            except (KeyError, TypeError, ValueError):
                reasons.append(f"{timeframe.lower()}_candle_malformed")
                break
            if not all(math.isfinite(value) for value in (open_, high, low, close)):
                reasons.append(f"{timeframe.lower()}_ohlc_nonfinite")
                break
            if min(open_, high, low, close) <= 0 or high < max(open_, close) or low > min(open_, close) or high < low:
                reasons.append(f"{timeframe.lower()}_ohlc_invalid")
                break
            timestamp = _parse_time(candle.get("time") or candle.get("datetime"))
            if timestamp is None or (previous_ts is not None and timestamp <= previous_ts):
                reasons.append(f"{timeframe.lower()}_timestamps_invalid")
                break
            previous_ts = timestamp
    return not reasons, tuple(dict.fromkeys(reasons))


def _horizon(value: str | None) -> str | None:
    normalized = str(value or "").lower()
    if normalized in {"intraday", "swing"}:
        return normalized
    if normalized == "auto":
        return "swing"
    return None


def _signal_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _expiry(decision_time: datetime, horizon: str) -> datetime:
    return decision_time + (timedelta(hours=4) if horizon == "intraday" else timedelta(days=2))


def _quant_predicates(quant: QuantScore) -> tuple[PredicateResult, ...]:
    """Transparency view of the price-based components (advisory; the full
    breakdown lives in factorScores). Never used to veto."""
    target = quant.direction
    out: list[PredicateResult] = []
    for name in ("trend", "momentum", "location", "volume"):
        comp = quant.components.get(name)
        if comp is None:
            continue
        aligned = (
            target in {"LONG", "SHORT"}
            and ((comp.signal > 0) == (target == "LONG"))
            and comp.quality > 0.1
        )
        out.append(
            PredicateResult(
                name=f"{name}_supports_direction",
                passed=bool(aligned and comp.signal != 0.0),
                actual=round(comp.signal, 3),
                expected=f"aligned with {target or 'a direction'}",
            )
        )
    return tuple(out)


def evaluate_engine_a_v3(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    horizon: str,
    registry: PromotionRegistry | None = None,
    blocked_reasons: tuple[str, ...] = (),
    context: Mapping[str, Any] | None = None,
) -> EngineASetupSignal:
    route = route_specialist(pair)
    normalized_horizon = _horizon(horizon)
    primary_tf = "H1" if normalized_horizon == "intraday" else "H4"
    primary = candles.get(primary_tf, []) if normalized_horizon else []
    last_ts_raw = (
        (primary[-1].get("time") or primary[-1].get("datetime"))
        if primary and isinstance(primary[-1], dict)
        else None
    )
    decision_time = _parse_time(last_ts_raw) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    last_ts = decision_time.isoformat() if last_ts_raw is not None else None
    display = str(pair.get("display") or pair.get("pair") or pair.get("symbol") or "")
    symbol = str(pair.get("symbol") or display)
    asset_type = str(pair.get("type") or pair.get("asset_type") or "other")

    valid_candles, candle_reasons = _validate_candles(candles)
    if normalized_horizon is None:
        candle_reasons = ("unsupported_horizon",) + candle_reasons
    if not valid_candles or normalized_horizon is None or blocked_reasons:
        rejection_reasons = tuple(
            dict.fromkeys(
                tuple(blocked_reasons)
                + (("unsupported_horizon",) if normalized_horizon is None else ())
                + candle_reasons
            )
        )
        payload = {
            "pair": display,
            "symbol": symbol,
            "route": route.score_group,
            "horizon": horizon,
            "decisionTime": decision_time.isoformat(),
            "reasons": rejection_reasons,
        }
        return EngineASetupSignal(
            contractVersion=CONTRACT_VERSION,
            signalId=_signal_id(payload),
            engine="ENGINE_A_V3",
            pair=display,
            symbol=symbol,
            type=asset_type,
            scoreGroup=route.score_group,
            family=route.family,
            subclass=route.subclass,
            horizon=normalized_horizon or str(horizon),
            setupId=None,
            decision="NO_SIGNAL",
            qualified=False,
            direction=None,
            decisionTime=decision_time.isoformat(),
            lastConfirmedCandleTs=last_ts,
            validUntil=_expiry(decision_time, normalized_horizon or "intraday").isoformat(),
            entryZone=None,
            invalidation=None,
            targets=(),
            price=None,
            sl=None,
            tp1=None,
            tp2=None,
            rr1=None,
            rr2=None,
            predicates=(),
            rejectionReasons=rejection_reasons,
            dataFreshness=DataFreshness(
                allowed=False,
                policy="confirmed_only",
                lastConfirmedCandleTs=last_ts,
                reason=rejection_reasons[0] if rejection_reasons else "invalid_data",
            ),
            validationArtifact=None,
            validationStatus="UNAVAILABLE",
            executionScope="NONE",
            engineATradeEnabled=False,
        )

    # ── Continuous quant scoring (no-veto). Every valid pair gets a direction +
    # quality. Promotion governs execution eligibility only; it never hides a pair.
    promotion = (registry or production_registry()).resolve(
        route,
        normalized_horizon,
        symbol=symbol,
    )
    profile = promotion.profile or baseline_profile(route.score_group, normalized_horizon)
    quant = score_pair(route, normalized_horizon, candles, context=context, profile=profile)

    direction = quant.direction if quant.direction in {"LONG", "SHORT"} else None
    levels = None
    if direction is not None:
        if quant.level_style == "mean_reversion":
            levels = build_mean_reversion_levels(primary, direction=direction)
        else:
            levels = build_structural_levels(primary, direction=direction)

    # The quant model never emits NO_SIGNAL. Missing levels or ineligible promotion
    # only cap TRADE -> WATCH so the pair stays visible; execution stays gated by
    # executionScope / engineATradeEnabled below.
    decision = quant.decision
    if decision == "TRADE" and (levels is None or not promotion.qualified):
        decision = "WATCH"

    qualified = decision == "TRADE" and promotion.qualified and levels is not None
    executable_levels = levels
    targets = executable_levels.targets if executable_levels else ()
    setup_id = f"quant_{quant.level_style}"

    validation_status = (
        "UNVALIDATED"
        if promotion.artifact and promotion.artifact.status == "DEMO_UNVALIDATED"
        else "PROMOTED"
        if promotion.artifact and promotion.artifact.status == "PROMOTED"
        else "UNAVAILABLE"
    )
    execution_scope = "DEMO_ONLY" if promotion.qualified else "NONE"

    signal_identity = {
        "contractVersion": CONTRACT_VERSION,
        "pair": display,
        "symbol": symbol,
        "family": route.family,
        "subclass": route.subclass,
        "horizon": normalized_horizon,
        "setupId": setup_id,
        "decision": decision,
        "direction": direction,
        "score": quant.confluence_score,
        "lastConfirmedCandleTs": last_ts,
        "artifactId": promotion.artifact.artifactId if promotion.artifact else None,
        "entry": executable_levels.price if executable_levels else None,
    }

    return EngineASetupSignal(
        contractVersion=CONTRACT_VERSION,
        signalId=_signal_id(signal_identity),
        engine="ENGINE_A_V3",
        pair=display,
        symbol=symbol,
        type=asset_type,
        scoreGroup=route.score_group,
        family=route.family,
        subclass=route.subclass,
        horizon=normalized_horizon,
        setupId=setup_id,
        decision=decision,
        qualified=qualified,
        direction=direction,
        decisionTime=decision_time.isoformat(),
        lastConfirmedCandleTs=last_ts,
        validUntil=_expiry(decision_time, normalized_horizon).isoformat(),
        entryZone=executable_levels.entry_zone if executable_levels else None,
        invalidation=executable_levels.invalidation if executable_levels else None,
        targets=targets,
        price=executable_levels.price if executable_levels else None,
        sl=executable_levels.invalidation if executable_levels else None,
        tp1=targets[0].price if len(targets) > 0 else None,
        tp2=targets[1].price if len(targets) > 1 else None,
        rr1=targets[0].rr if len(targets) > 0 else None,
        rr2=targets[1].rr if len(targets) > 1 else None,
        predicates=_quant_predicates(quant),
        rejectionReasons=promotion.reasons if not promotion.qualified else (),
        dataFreshness=DataFreshness(
            allowed=True,
            policy="confirmed_only",
            lastConfirmedCandleTs=last_ts,
            reason="confirmed_candles_valid",
        ),
        validationArtifact=promotion.artifact,
        validationStatus=validation_status,
        executionScope=execution_scope,
        scoringProfile=profile.to_dict(),
        exitPolicy=profile.exit_policy,
        componentScores=quant.factor_diagnostics.get("components"),
        confluenceScore=quant.confluence_score,
        maxScore=quant.max_score,
        scoreNorm=quant.score_norm,
        conviction=quant.conviction,
        confluenceThreshold=quant.threshold,
        factorScores=quant.factor_scores,
        factorDiagnostics=quant.factor_diagnostics,
        engineATradeEnabled=qualified,
    )
