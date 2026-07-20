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
    StructuralLevels,
    build_london_open_breakout_levels,
    build_mean_reversion_levels,
    build_structural_levels,
    resolve_structural_geometry,
)
from engine_a_v3.promotion import PromotionRegistry, production_registry
from engine_a_v3.quant_scorer import QuantScore, score_pair
from engine_a_v3.profile import baseline_profile
from engine_a_v3.routing import route_specialist
from engine_a_v3.session_scoring import session_score_passes
from engine_a_v3.setups import SetupCandidate, SetupSeriesCache, detect_setup
from engine_a_v3.timeframes import (
    resolve_diagnostic_v3_entry_timeframe,
    resolve_v3_entry_timeframe,
)
from style_resolver import resolve_auto_style


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


def _first_candle_validation_failure(rows: list[dict]) -> tuple[int | None, str | None]:
    previous_ts = None
    for index, candle in enumerate(rows):
        if not isinstance(candle, dict):
            return index, "candle_malformed"
        try:
            open_ = float(candle["open"])
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            return index, "candle_malformed"
        if not all(math.isfinite(value) for value in (open_, high, low, close)):
            return index, "ohlc_nonfinite"
        if (
            min(open_, high, low, close) <= 0
            or high < max(open_, close)
            or low > min(open_, close)
            or high < low
        ):
            return index, "ohlc_invalid"
        timestamp = _parse_time(candle.get("time") or candle.get("datetime"))
        if timestamp is None or (previous_ts is not None and timestamp <= previous_ts):
            return index, "timestamps_invalid"
        previous_ts = timestamp
    return None, None


class BacktestCandleValidationIndex:
    """O(1) validity lookup for immutable historical candle prefixes."""

    def __init__(self, candles: Mapping[str, list[dict]]):
        self._candles = candles
        self._failures = {
            timeframe: _first_candle_validation_failure(rows)
            for timeframe, rows in candles.items()
            if isinstance(rows, list)
        }

    def failure_for(self, timeframe: str, rows: list[dict]) -> tuple[bool, str | None]:
        full_rows = self._candles.get(timeframe)
        if not isinstance(full_rows, list) or not isinstance(rows, list):
            return False, None
        length = len(rows)
        if length > len(full_rows):
            return False, None
        if length and (rows[0] is not full_rows[0] or rows[-1] is not full_rows[length - 1]):
            return False, None
        invalid_index, reason = self._failures.get(timeframe, (None, None))
        if invalid_index is not None and invalid_index < length:
            return True, reason
        return True, None


def _validate_candles(
    candles: dict[str, list[dict]],
    validation_index: BacktestCandleValidationIndex | None = None,
) -> tuple[bool, tuple[str, ...]]:
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
        if validation_index is not None:
            supported, failure = validation_index.failure_for(timeframe, rows)
            if supported:
                if failure:
                    reasons.append(f"{timeframe.lower()}_{failure}")
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


def _minimum_normalized_indicator_bars(
    asset_type: str, periods: Mapping[str, int] | tuple[tuple[str, int], ...]
) -> int:
    """Bars required for the latest configured normalized indicator values."""
    from indicators import get_normalization_lookback

    resolved = dict(periods)
    lookback = int(get_normalization_lookback(asset_type))
    adx_period = int(resolved.get("adx", 14))
    atr_period = int(resolved.get("atr", 14))
    return max(
        50,
        int(resolved.get("ema_long", 0)),
        int(resolved.get("rsi", 0)) + 1,
        int(resolved.get("macd_slow", 0)) + int(resolved.get("macd_signal", 0)) - 1,
        lookback + atr_period,
        lookback + (2 * adx_period) + 1,
    )


def _horizon(value: str | None) -> str | None:
    """Normalize the requested style to a V3 horizon.

    V3 intentionally supports only intraday and swing. Scalp is NOT mapped here
    — scalp is owned by Engine D (separate scalp logic), so a scalp request
    reaching V3 returns None → unsupported_horizon → NO_SIGNAL. This is the
    intended fail-closed boundary between Engine A V3 and Engine D; do not map
    scalp→intraday here or V3 would silently run scalp signals that Engine D
    should handle.
    """
    normalized = str(value or "").lower()
    if normalized in {"intraday", "swing"}:
        return normalized
    return None


def _signal_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _expiry(decision_time: datetime, horizon: str, primary_tf: str = "H1") -> datetime:
    """Signal validity window anchored to the decision bar's OPEN time.

    Intraday validity must cover at least two primary bars: decision_time is the
    open of the last CONFIRMED bar, so a window shorter than 2x the primary TF
    expires at (or before) evaluation time. Default H1 groups keep the historical
    4h window; valid style-nested H4 overrides receive an 8h window.
    """
    if horizon == "intraday":
        tf_hours = {"H1": 1, "H4": 4, "D1": 24}.get(str(primary_tf or "").upper(), 1)
        return decision_time + timedelta(hours=max(4, 2 * tf_hours))
    return decision_time + timedelta(days=2)


def _demo_research_unpromoted_trade_allowed() -> bool:
    try:
        from config import CONFIG

        return (
            bool(CONFIG.get("ENGINE_A_V3_DEMO_UNVALIDATED_ENABLED", False))
            and str(CONFIG.get("EXECUTOR_MODE", "") or "").strip().lower() == "demo"
        )
    except Exception:
        return False


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


def _build_levels(
    primary: list[dict],
    *,
    direction: str,
    level_style: str,
    atr_pct: float | None = None,
    atr_period: int = 14,
    ema_period: int = 20,
    current_price: float | None = None,
    asset_type: str | None = None,
    atr_candles: list[dict] | None = None,
    swing_candles: list[dict] | None = None,
    series_cache=None,
    primary_timeframe: str | None = None,
    atr_timeframe: str | None = None,
) -> StructuralLevels | None:
    """Route to the correct level builder by style."""
    if direction not in {"LONG", "SHORT"}:
        return None
    if level_style == "mean_reversion":
        _mr_min_rr = 1.0
        try:
            from config import CONFIG
            from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled

            if tp_sl_rr_gates_disabled(CONFIG):
                _mr_min_rr = 0.0
        except Exception:
            _mr_min_rr = 1.0
        return build_mean_reversion_levels(
            primary,
            direction=direction,
            atr_period=atr_period,
            ema_period=ema_period,
            current_price=current_price,
            min_rr=_mr_min_rr,
            series_cache=series_cache,
            primary_timeframe=primary_timeframe,
        )
    if level_style == "london_open":
        return build_london_open_breakout_levels(
            primary, direction=direction, current_price=current_price
        )
    return build_structural_levels(
        primary,
        direction=direction,
        atr_pct=atr_pct,
        atr_period=atr_period,
        current_price=current_price,
        asset_type=asset_type,
        atr_candles=atr_candles,
        swing_candles=swing_candles,
        series_cache=series_cache,
        primary_timeframe=primary_timeframe,
        atr_timeframe=atr_timeframe,
    )


def evaluate_engine_a_v3(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    horizon: str,
    registry: PromotionRegistry | None = None,
    blocked_reasons: tuple[str, ...] = (),
    context: Mapping[str, Any] | None = None,
    snapshot_cache: dict | None = None,
    current_price: float | None = None,
    entry_tf_override: str | None = None,
    policy_timeframes: Mapping[str, Any] | None = None,
    entry_candle_is_forming: bool = False,
    candle_validation_index: BacktestCandleValidationIndex | None = None,
    setup_series_cache: SetupSeriesCache | None = None,
    quant_feature_cache: dict | None = None,
    setup_candidate_cache: dict | None = None,
    compact_replay: bool = False,
) -> EngineASetupSignal:
    route = route_specialist(pair)
    asset_type = str(pair.get("type") or pair.get("asset_type") or "other")
    normalized_horizon = _horizon(
        resolve_auto_style(
            horizon,
            pair,
            score_group=route.score_group,
            asset_type=asset_type,
        )
    )
    diagnostic_override = resolve_diagnostic_v3_entry_timeframe(entry_tf_override)
    policy = policy_timeframes if isinstance(policy_timeframes, Mapping) else None
    policy_entry_tf = str(
        (policy or {}).get("setup") or (policy or {}).get("setupTf") or ""
    ).upper()
    policy_trigger_tf = str(
        (policy or {}).get("trigger") or (policy or {}).get("triggerTf") or ""
    ).upper()
    if policy and policy_entry_tf:
        primary_tf = policy_entry_tf
    elif entry_tf_override is not None and diagnostic_override is None:
        primary_tf = None
    elif diagnostic_override is not None:
        primary_tf = diagnostic_override
    else:
        primary_tf = resolve_v3_entry_timeframe(
            route.score_group,
            asset_type,
            normalized_horizon or "intraday",
        )
    primary = candles.get(primary_tf, []) if normalized_horizon and primary_tf else []
    last_ts_raw = (
        (primary[-1].get("time") or primary[-1].get("datetime"))
        if primary and isinstance(primary[-1], dict)
        else None
    )
    decision_time = _parse_time(last_ts_raw) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    last_ts = decision_time.isoformat() if last_ts_raw is not None else None
    last_confirmed_ts = last_ts
    if entry_candle_is_forming and len(primary) >= 2 and isinstance(primary[-2], dict):
        _confirmed_raw = primary[-2].get("time") or primary[-2].get("datetime")
        _confirmed_dt = _parse_time(_confirmed_raw)
        last_confirmed_ts = _confirmed_dt.isoformat() if _confirmed_dt else None
    display = str(pair.get("display") or pair.get("pair") or pair.get("symbol") or "")
    symbol = str(pair.get("symbol") or display)
    valid_candles, candle_reasons = _validate_candles(
        candles,
        validation_index=candle_validation_index,
    )
    promotion = None
    profile = None
    if normalized_horizon is not None and primary_tf is not None and valid_candles:
        promotion = (registry or production_registry()).resolve(
            route,
            normalized_horizon,
            symbol=symbol,
        )
        profile = promotion.profile or baseline_profile(route.score_group, normalized_horizon)

    role_timeframes: dict[str, str] = {}
    if diagnostic_override is not None or policy_entry_tf:
        role_timeframes[primary_tf] = "entry"
    if policy_trigger_tf and policy_trigger_tf != primary_tf:
        role_timeframes[policy_trigger_tf] = "trigger"
    role_minimum = (
        _minimum_normalized_indicator_bars(asset_type, profile.indicator_periods)
        if policy and profile is not None
        else 50
    )
    for role_tf, role_name in role_timeframes.items():
        role_candles = candles.get(role_tf, [])
        if not role_candles:
            candle_reasons = candle_reasons + (f"missing_{role_name}_candles",)
            valid_candles = False
        elif len(role_candles) < role_minimum:
            candle_reasons = candle_reasons + (f"{role_name}_history_insufficient",)
            valid_candles = False
        else:
            if candle_validation_index is not None:
                supported, failure = candle_validation_index.failure_for(role_tf, role_candles)
                if supported:
                    if failure:
                        candle_reasons = candle_reasons + (f"{role_name}_candles_invalid",)
                        valid_candles = False
                    continue
            _entry_prev_ts = None
            for _entry_bar in role_candles:
                try:
                    _o = float(_entry_bar["open"])
                    _h = float(_entry_bar["high"])
                    _l = float(_entry_bar["low"])
                    _c = float(_entry_bar["close"])
                    _entry_ts = _parse_time(
                        _entry_bar.get("time") or _entry_bar.get("datetime")
                    )
                except (AttributeError, KeyError, TypeError, ValueError):
                    _entry_ts = None
                    _o = _h = _l = _c = float("nan")
                if (
                    not all(math.isfinite(v) for v in (_o, _h, _l, _c))
                    or min(_o, _h, _l, _c) <= 0
                    or _h < max(_o, _c)
                    or _l > min(_o, _c)
                    or _h < _l
                    or _entry_ts is None
                    or (_entry_prev_ts is not None and _entry_ts <= _entry_prev_ts)
                ):
                    candle_reasons = candle_reasons + (f"{role_name}_candles_invalid",)
                    valid_candles = False
                    break
                _entry_prev_ts = _entry_ts
    if normalized_horizon is None:
        candle_reasons = ("unsupported_horizon",) + candle_reasons
    if primary_tf is None:
        candle_reasons = ("invalid_entry_timeframe",) + candle_reasons
    if not valid_candles or normalized_horizon is None or primary_tf is None or blocked_reasons:
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
            signalId="" if compact_replay else _signal_id(payload),
            engine="ENGINE_A_V3",
            pair=display,
            symbol=symbol,
            type=asset_type,
            scoreGroup=route.score_group,
            family=route.family,
            subclass=route.subclass,
            horizon=normalized_horizon or str(horizon),
            entryTimeframe=primary_tf,
            setupId=None,
            decision="NO_SIGNAL",
            qualified=False,
            direction=None,
            decisionTime=decision_time.isoformat(),
            lastConfirmedCandleTs=last_confirmed_ts,
            # With no parseable last-candle timestamp, decision_time is the
            # epoch sentinel - an expiry computed from it (epoch+4h) is
            # misleading. Emit an already-expired validUntil instead.
            validUntil=(
                _expiry(
                    decision_time, normalized_horizon or "intraday", primary_tf
                ).isoformat()
                if last_ts_raw is not None
                else decision_time.isoformat()
            ),
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
                policy="active_entry_confirmed_structure" if entry_candle_is_forming else "confirmed_only",
                lastConfirmedCandleTs=last_confirmed_ts,
                reason=rejection_reasons[0] if rejection_reasons else "invalid_data",
            ),
            validationArtifact=None,
            validationStatus="UNAVAILABLE",
            executionScope="NONE",
            engineATradeEnabled=False,
        )

    # ── Continuous quant scoring (no-veto). Every valid pair gets a direction +
    # quality. Promotion governs execution eligibility only; it never hides a pair.
    assert promotion is not None and profile is not None
    _score_kwargs = {
        "context": context,
        "profile": profile,
        "snapshot_cache": snapshot_cache,
        "series_cache": setup_series_cache,
        "feature_cache": quant_feature_cache,
        "compact_result": compact_replay,
    }
    if diagnostic_override is not None:
        _score_kwargs["entry_tf_override"] = diagnostic_override
    if policy is not None:
        _score_kwargs["policy_timeframes"] = policy
    quant = score_pair(route, normalized_horizon, candles, **_score_kwargs)

    # ── Setup overlay: use already-implemented specialists for every family
    # (forex: breakout/retest/pullback/london_open/mean_reversion; crypto:
    # breakout/retest/pullback; commodity: subclass-specific breakout/pullback;
    # index/equity: opening-range-gap / relative-strength breakout & pullback).
    # This gives each family an independent, structure/session-based path to
    # TRADE when the continuous quant score alone is below the threshold. It
    # never vetoes the quant signal; it only upgrades WATCH -> TRADE when a
    # validated setup fires.
    setup: SetupCandidate | None = None
    setup_diagnostics: dict[str, Any] = {}
    try:
        _setup_kwargs = {
            "display": display,
            "indicator_periods": dict(profile.indicator_periods),
            "series_cache": setup_series_cache,
        }
        if policy_entry_tf or diagnostic_override:
            _setup_kwargs["entry_tf_override"] = policy_entry_tf or diagnostic_override
        setup_cache_key = (
            route.score_group,
            normalized_horizon,
            primary_tf,
            len(primary),
            len(candles.get("D1") or []),
            len(candles.get("H4") or []),
            len(candles.get("H1") or []),
        )
        cached_setup = (
            setup_candidate_cache.get("candidate")
            if setup_candidate_cache is not None
            and setup_candidate_cache.get("key") == setup_cache_key
            else None
        )
        if cached_setup is not None:
            setup = cached_setup
        else:
            setup = detect_setup(route, normalized_horizon, candles, **_setup_kwargs)
            if setup_candidate_cache is not None:
                setup_candidate_cache["key"] = setup_cache_key
                setup_candidate_cache["candidate"] = setup
        setup_diagnostics = {
            "setupId": setup.setup_id,
            "setupDecision": setup.decision,
            "setupDirection": setup.direction,
            "setupLevelStyle": setup.level_style,
            "setupPredicateCount": len(setup.predicates),
        }
    except Exception as setup_exc:
        setup = None
        setup_diagnostics = {
            "setupError": True,
            "setupErrorDetail": type(setup_exc).__name__,
        }

    use_setup = (
        setup is not None
        and setup.decision == "TRADE"
        and setup.direction in {"LONG", "SHORT"}
        and not setup_diagnostics.get("setupError")
    )

    # Direction-conflict guard: the setup overlay may only UPGRADE the quant
    # signal (WATCH -> TRADE), never flip its direction. When the quant model
    # has a direction and a setup fires TRADE the opposite way, the signal is
    # ambiguous — fall back to the quant path (its own threshold still applies)
    # instead of executing a direction the quality model disagrees with.
    # Config-gated (default ON); disable via ENGINE_A_V3_SETUP_QUANT_CONFLICT_GUARD.
    setup_direction_conflict = False
    if use_setup and quant.direction in {"LONG", "SHORT"} and setup.direction != quant.direction:
        guard_enabled = True
        try:
            from config import CONFIG

            guard_enabled = bool(CONFIG.get("ENGINE_A_V3_SETUP_QUANT_CONFLICT_GUARD", True))
        except Exception:
            guard_enabled = True
        if guard_enabled:
            use_setup = False
            setup_direction_conflict = True
            setup_diagnostics["directionConflictBlocked"] = True
            setup_diagnostics["quantDirection"] = quant.direction

    # Blocked trend states (legacy filter) also suppress setup upgrades.
    legacy_pre = (quant.factor_diagnostics or {}).get("legacyFilters") or {}
    if use_setup and legacy_pre.get("trendStateBlocked"):
        use_setup = False
        setup_diagnostics["trendStateBlockedUpgrade"] = True

    # Quality floor for setup upgrades: a structural setup may only promote
    # WATCH -> TRADE when quant confluence meets a minimum fraction of threshold.
    # Config-gated (default ON); disable via ENGINE_A_V3_SETUP_UPGRADE.ENABLED.
    if use_setup:
        try:
            from config import CONFIG

            upgrade_cfg = CONFIG.get("ENGINE_A_V3_SETUP_UPGRADE") or {}
            if upgrade_cfg.get("ENABLED", True):
                frac = float(upgrade_cfg.get("MIN_CONFLUENCE_FRAC", 0.75))
                floor = frac * quant.threshold
                if quant.confluence_score < floor:
                    use_setup = False
                    setup_diagnostics["qualityFloorBlocked"] = True
                    setup_diagnostics["minConfluenceFloor"] = round(floor, 4)
        except Exception:
            pass

    # Session scoring gate (config-gated, forex-only): a forex setup must also
    # pass the session context score when ENGINE_A_V3_SESSION_SCORING.ENABLED.
    # Exceptions fail closed (block upgrade) — never silently allow the upgrade.
    if use_setup and route.family == "forex":
        try:
            from config import CONFIG

            session_cfg = CONFIG.get("ENGINE_A_V3_SESSION_SCORING") or {}
            if session_cfg.get("ENABLED"):
                min_score = float(session_cfg.get("MIN_SCORE", 0.35))
                if not session_score_passes(
                    primary, direction=setup.direction, min_score=min_score
                ):
                    use_setup = False
                    setup_diagnostics["sessionGateBlocked"] = True
        except Exception as session_exc:
            use_setup = False
            setup_diagnostics["sessionGateBlocked"] = True
            setup_diagnostics["sessionGateError"] = type(session_exc).__name__

    if use_setup:
        direction = setup.direction
        level_style = setup.level_style
        setup_id = setup.setup_id or f"setup_{level_style}"
    else:
        direction = quant.direction if quant.direction in {"LONG", "SHORT"} else None
        level_style = quant.level_style
        setup_id = f"quant_{quant.level_style}"
        # NOTE: no evaluator-side mean-reversion direction flip here.
        # quant_scorer already derives `direction` from the location signal in
        # MR regimes (and flips level_style to "trend" when its opposition
        # guard blocks the fade), so re-deriving it here was a proven no-op.

    levels = None
    atr_pct = quant.factor_diagnostics.get("atrPct") if quant.factor_diagnostics else None
    period_map = dict(profile.indicator_periods)
    atr_period = int(period_map.get("atr", 14) or 14)
    ema_period = int(period_map.get("ema_trend", 20) or 20)
    atr_candles: list[dict] | None = None
    swing_candles: list[dict] | None = None
    levels_atr_tf: str | None = None
    if direction is not None and level_style not in {"mean_reversion", "london_open"}:
        geometry = resolve_structural_geometry(asset_type)
        if geometry.get("levels_atr_tf") == "structure":
            structure_tf = str(
                (policy or {}).get("structure")
                or (policy or {}).get("structureTf")
                or ""
            ).upper()
            if not structure_tf:
                # Legacy / no-policy path: structure ladder step above entry.
                structure_tf = {
                    "M15": "H1",
                    "M30": "H1",
                    "H1": "H4",
                    "H4": "D1",
                }.get(str(primary_tf or "").upper(), "H1")
            structure_rows = candles.get(structure_tf) or []
            if len(structure_rows) >= 20:
                atr_candles = structure_rows
                swing_candles = structure_rows
                levels_atr_tf = structure_tf
    if direction is not None:
        levels = _build_levels(
            primary,
            direction=direction,
            level_style=level_style,
            atr_pct=atr_pct,
            atr_period=atr_period,
            ema_period=ema_period,
            current_price=current_price,
            asset_type=asset_type,
            atr_candles=atr_candles,
            swing_candles=swing_candles,
            series_cache=setup_series_cache,
            primary_timeframe=primary_tf,
            atr_timeframe=levels_atr_tf,
        )

    # The quant model never emits NO_SIGNAL. Missing levels caps TRADE -> WATCH
    # so the pair stays visible. Promotion caps only outside demo research mode;
    # execution stays gated by executionScope / engineATradeEnabled below.
    decision = "TRADE" if use_setup else quant.decision
    quant_session_blocked = False
    if (
        not use_setup
        and decision == "TRADE"
        and route.family == "forex"
        and direction in {"LONG", "SHORT"}
    ):
        try:
            from config import CONFIG

            q_sess = CONFIG.get("ENGINE_A_V3_QUANT_SESSION_GATE") or {}
            if q_sess.get("ENABLED", True):
                min_score = float(q_sess.get("MIN_SCORE", 0.40))
                if not session_score_passes(
                    primary, direction=direction, min_score=min_score
                ):
                    decision = "WATCH"
                    quant_session_blocked = True
        except Exception:
            decision = "WATCH"
            quant_session_blocked = True
    demo_research_unpromoted_allowed = (
        not promotion.qualified and _demo_research_unpromoted_trade_allowed()
    )
    if decision == "TRADE" and levels is None:
        decision = "WATCH"
    elif decision == "TRADE" and not promotion.qualified and not demo_research_unpromoted_allowed:
        decision = "WATCH"

    promotion_execution_allowed = promotion.qualified or demo_research_unpromoted_allowed
    qualified = decision == "TRADE" and promotion_execution_allowed and levels is not None
    executable_levels = levels
    targets = executable_levels.targets if executable_levels else ()

    validation_status = (
        "UNVALIDATED"
        if promotion.artifact and promotion.artifact.status == "DEMO_UNVALIDATED"
        else "PROMOTED"
        if promotion.artifact and promotion.artifact.status == "PROMOTED"
        else "UNVALIDATED"
        if demo_research_unpromoted_allowed
        else "UNAVAILABLE"
    )
    execution_scope = "DEMO_ONLY" if promotion_execution_allowed else "NONE"

    predicates: tuple[PredicateResult, ...] = ()
    if not compact_replay:
        predicates = _quant_predicates(quant)
        if setup is not None:
            predicates = predicates + setup.predicates

    rejection_reasons: list[str] = []
    if setup_diagnostics.get("qualityFloorBlocked"):
        rejection_reasons.append("setup_upgrade_below_quant_quality_floor")
    if setup_diagnostics.get("sessionGateBlocked"):
        if setup_diagnostics.get("sessionGateError"):
            rejection_reasons.append("session_gate_error_fail_closed")
        else:
            rejection_reasons.append("session_context_score_below_min")
    if setup_diagnostics.get("setupError"):
        rejection_reasons.append("setup_detection_error")
    if setup_direction_conflict:
        rejection_reasons.append("setup_direction_conflicts_quant")
    if quant_session_blocked:
        rejection_reasons.append("quant_session_gate_blocked")
    if (quant.factor_diagnostics or {}).get("minDirectionalFailed"):
        rejection_reasons.append("min_directional_failed")
    legacy = (quant.factor_diagnostics or {}).get("legacyFilters") or {}
    if legacy.get("trendStateBlocked") and decision == "TRADE":
        decision = "WATCH"
        rejection_reasons.append("blocked_trend_state")
    elif legacy.get("trendStateBlocked"):
        rejection_reasons.append("blocked_trend_state")
    if (quant.factor_diagnostics or {}).get("equityVolumeBlocked") and decision == "TRADE":
        decision = "WATCH"
        rejection_reasons.append("equity_volume_quality_floor")
    elif (quant.factor_diagnostics or {}).get("equityVolumeBlocked"):
        rejection_reasons.append("equity_volume_quality_floor")
    if (quant.factor_diagnostics or {}).get("cryptoDerivBlocked") and decision == "TRADE":
        decision = "WATCH"
        rejection_reasons.append("crypto_derivatives_conflict")
    elif (quant.factor_diagnostics or {}).get("cryptoDerivBlocked"):
        rejection_reasons.append("crypto_derivatives_conflict")

    # A live lower-TF entry is only executable when its active bucket was
    # explicitly supplied.  Without this guard, a caller could route M15/M30
    # through the evaluator as though its last candle were closed and promote a
    # setup that is already one bar late.  Higher-TF structure remains
    # confirmed-only; this is an entry-timing gate only.
    active_entry_gate_required = diagnostic_override in {"M15", "M30"}
    active_entry_gate_ok = (
        not active_entry_gate_required or bool(entry_candle_is_forming)
    )
    if decision == "TRADE" and not active_entry_gate_ok:
        decision = "WATCH"
        rejection_reasons.append("active_entry_candle_required")
        setup_diagnostics["activeEntryGateBlocked"] = True

    # Recompute after late demotions (blocked trend / equity volume / crypto deriv).
    qualified = decision == "TRADE" and promotion_execution_allowed and levels is not None

    if compact_replay and qualified:
        # Full diagnostics are required only for emitted trade metadata. The
        # compact pass above already made the strategy decision with identical
        # components and gates; this second pass is rare and presentation-only.
        full_score_kwargs = dict(_score_kwargs)
        full_score_kwargs["compact_result"] = False
        quant = score_pair(route, normalized_horizon, candles, **full_score_kwargs)
        predicates = _quant_predicates(quant)
        if setup is not None:
            predicates = predicates + setup.predicates

    compact_unqualified = compact_replay and not qualified
    factor_diagnostics = None
    if not compact_unqualified:
        factor_diagnostics = dict(quant.factor_diagnostics or {})
        factor_diagnostics["entryUsesActiveCandle"] = bool(entry_candle_is_forming)
        factor_diagnostics["activeEntryGate"] = {
            "required": active_entry_gate_required,
            "passed": active_entry_gate_ok,
            "timeframe": diagnostic_override,
        }
        factor_diagnostics["setupOverlay"] = setup_diagnostics
        factor_diagnostics["promotion"] = {
            "qualified": bool(promotion.qualified),
            "demoResearchOverride": bool(demo_research_unpromoted_allowed),
            "reasons": list(promotion.reasons),
        }
        if quant_session_blocked:
            factor_diagnostics["quantSessionGateBlocked"] = True

    signal_id = ""
    if not compact_unqualified:
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
            "lastConfirmedCandleTs": last_confirmed_ts,
            "artifactId": promotion.artifact.artifactId if promotion.artifact else None,
            "entry": executable_levels.price if executable_levels else None,
        }
        signal_id = _signal_id(signal_identity)

    return EngineASetupSignal(
        contractVersion=CONTRACT_VERSION,
        signalId=signal_id,
        engine="ENGINE_A_V3",
        pair=display,
        symbol=symbol,
        type=asset_type,
        scoreGroup=route.score_group,
        family=route.family,
        subclass=route.subclass,
        horizon=normalized_horizon,
        entryTimeframe=primary_tf,
        setupId=setup_id,
        decision=decision,
        qualified=qualified,
        direction=direction,
        decisionTime=decision_time.isoformat(),
        lastConfirmedCandleTs=last_confirmed_ts,
        validUntil=_expiry(decision_time, normalized_horizon, primary_tf).isoformat(),
        entryZone=executable_levels.entry_zone if executable_levels else None,
        invalidation=executable_levels.invalidation if executable_levels else None,
        targets=targets,
        price=executable_levels.price if executable_levels else None,
        sl=executable_levels.invalidation if executable_levels else None,
        tp1=targets[0].price if len(targets) > 0 else None,
        tp2=targets[1].price if len(targets) > 1 else None,
        rr1=targets[0].rr if len(targets) > 0 else None,
        rr2=targets[1].rr if len(targets) > 1 else None,
        predicates=() if compact_unqualified else predicates,
        rejectionReasons=(
            tuple(rejection_reasons) + promotion.reasons
            if not promotion.qualified and not demo_research_unpromoted_allowed
            else tuple(rejection_reasons)
        ),
        dataFreshness=DataFreshness(
            allowed=active_entry_gate_ok,
            policy="active_entry_confirmed_structure" if entry_candle_is_forming else "confirmed_only",
            lastConfirmedCandleTs=last_confirmed_ts,
            reason=(
                "active_entry_candle_required"
                if not active_entry_gate_ok
                else "active_entry_and_confirmed_structure_valid"
                if entry_candle_is_forming
                else "confirmed_candles_valid"
            ),
        ),
        validationArtifact=promotion.artifact,
        validationStatus=validation_status,
        executionScope=execution_scope,
        scoringProfile=None if compact_unqualified else profile.to_dict(),
        exitPolicy=profile.exit_policy,
        componentScores=(
            None if compact_unqualified else quant.factor_diagnostics.get("components")
        ),
        confluenceScore=quant.confluence_score,
        maxScore=quant.max_score,
        scoreNorm=quant.score_norm,
        conviction=quant.conviction,
        confluenceThreshold=quant.threshold,
        factorScores=None if compact_unqualified else quant.factor_scores,
        factorDiagnostics=factor_diagnostics,
        engineATradeEnabled=qualified,
    )
