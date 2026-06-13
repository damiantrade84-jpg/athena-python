from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from engine_a_v3.contract import (
    CONTRACT_VERSION,
    DataFreshness,
    EngineASetupSignal,
)
from engine_a_v3.levels import (
    build_london_open_breakout_levels,
    build_mean_reversion_levels,
    build_structural_levels,
)
from engine_a_v3.promotion import PromotionRegistry, production_registry
from engine_a_v3.routing import route_specialist
from engine_a_v3.setups import SetupCandidate, detect_setup


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _validate_candles(candles: dict[str, list[dict]]) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    for timeframe in ("D1", "H4", "H1"):
        rows = candles.get(timeframe)
        if not isinstance(rows, list) or len(rows) < 2:
            reasons.append(f"{timeframe.lower()}_candles_missing")
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
            if min(open_, high, low, close) <= 0 or high < max(open_, close) or low > min(open_, close):
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


def evaluate_engine_a_v3(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    horizon: str,
    registry: PromotionRegistry | None = None,
    blocked_reasons: tuple[str, ...] = (),
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

    candidate = detect_setup(route, normalized_horizon, candles)
    if (
        route.family == "forex"
        and candidate.level_style == "london_open"
        and candidate.decision == "TRADE"
    ):
        from config import CONFIG

        scoring_cfg = CONFIG.get("ENGINE_A_V3_SESSION_SCORING") or {}
        if bool(scoring_cfg.get("ENABLED", False)):
            from engine_a_v3.session_scoring import session_score_passes

            min_score = float(scoring_cfg.get("MIN_SCORE", 0.35))
            if not session_score_passes(primary, direction=candidate.direction, min_score=min_score):
                candidate = SetupCandidate(
                    candidate.setup_id,
                    "NO_SIGNAL",
                    candidate.direction,
                    candidate.predicates,
                    tuple(
                        dict.fromkeys(
                            candidate.rejection_reasons + ("session_context_score_below_min",)
                        )
                    ),
                    candidate.level_style,
                )
    promotion = (registry or production_registry()).resolve(
        route,
        normalized_horizon,
        symbol=symbol,
    )
    levels = None
    if candidate.direction in {"LONG", "SHORT"}:
        if candidate.level_style == "mean_reversion":
            levels = build_mean_reversion_levels(primary, direction=candidate.direction)
        elif candidate.level_style == "london_open":
            levels = build_london_open_breakout_levels(primary, direction=candidate.direction)
        else:
            levels = build_structural_levels(primary, direction=candidate.direction)
    rejection_reasons = list(candidate.rejection_reasons)
    decision = candidate.decision
    if levels is None and decision != "NO_SIGNAL":
        decision = "NO_SIGNAL"
        rejection_reasons.append("structural_levels_invalid")
    if not promotion.qualified:
        decision = "NO_SIGNAL"
        rejection_reasons.extend(promotion.reasons)
    rejection_reasons = list(dict.fromkeys(rejection_reasons))
    qualified = decision == "TRADE" and promotion.qualified and levels is not None
    executable_levels = levels if decision in {"TRADE", "WATCH"} and promotion.qualified else None
    signal_identity = {
        "contractVersion": CONTRACT_VERSION,
        "pair": display,
        "symbol": symbol,
        "family": route.family,
        "subclass": route.subclass,
        "horizon": normalized_horizon,
        "setupId": candidate.setup_id,
        "decision": decision,
        "direction": candidate.direction,
        "lastConfirmedCandleTs": last_ts,
        "artifactId": promotion.artifact.artifactId if promotion.artifact else None,
        "entry": executable_levels.price if executable_levels else None,
        "sl": executable_levels.invalidation if executable_levels else None,
        "targets": [target.price for target in executable_levels.targets] if executable_levels else [],
    }
    targets = executable_levels.targets if executable_levels else ()
    validation_status = (
        "UNVALIDATED"
        if promotion.artifact and promotion.artifact.status == "DEMO_UNVALIDATED"
        else "PROMOTED"
        if promotion.artifact and promotion.artifact.status == "PROMOTED"
        else "UNAVAILABLE"
    )
    execution_scope = "DEMO_ONLY" if promotion.qualified else "NONE"
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
        setupId=candidate.setup_id,
        decision=decision,
        qualified=qualified,
        direction=candidate.direction,
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
        predicates=candidate.predicates,
        rejectionReasons=tuple(rejection_reasons),
        dataFreshness=DataFreshness(
            allowed=True,
            policy="confirmed_only",
            lastConfirmedCandleTs=last_ts,
            reason="confirmed_candles_valid",
        ),
        validationArtifact=promotion.artifact,
        validationStatus=validation_status,
        executionScope=execution_scope,
        engineATradeEnabled=qualified,
    )
