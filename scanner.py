"""Full-scan orchestration and scan-time signal annotation."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from athena_runtime import rt
from athena_app.services.crypto_signal_feed import (
    fetch_crypto_signal_candles,
    resolve_crypto_signal_feed,
)
from candles_cache import get_candle_fetch_meta
from config import CONFIG, scan_candle_limits
from data_feeds import http_requests
from indicators import calc_atr, calc_indicators, calc_indicators_with_normalized
from intermarket import build_scan_snapshot
from scoring import (
    _build_event_risk,
    _classify_signal,
    _pair_exchange_closed,
    apply_correlation_cap,
    get_score_threshold,
    get_pair_score_group,
)
from engine_c import ENGINE_C_AB_WEIGHTS
from market_structure import (
    NakedEngine,
    engine_b_confidence_passes,
    engine_b_forex_asian_session_blocks_bar,
    engine_b_min_score_threshold,
)
from threshold_audit import (
    audit_enabled as threshold_audit_enabled,
    build_signal_funnel_row,
    write_signal_funnel_rows,
)
from factor_scoring import make_regime_smoothing_context

log = logging.getLogger("sentinel")


def _engine_b_scan_confirmation_gate_enabled(config: dict | None = None) -> bool:
    """Return True when Engine A *trade* tier should require Engine B confirmation.

    Default False: full-scan trade lists use Engine A thresholds only; Engine B
    fields remain on the payload for Engine C / dashboards. When True (legacy),
    a trade-tier row is demoted to watchlist unless ``enginesAligned``.

    Autopilot and manual execution still apply their own conviction / risk gates
    (see ``auto_trader.AutoTrader._can_execute``); they do not follow this flag.
    """
    cfg = config or CONFIG
    return bool(cfg.get("ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED", False))


def _select_engine_b_tf_candles(tf: str | None, tf_map: dict[str, list]) -> list:
    key = str(tf or "H4").upper()
    return list(tf_map.get(key) or [])


def _last_atr_from_candles(candles: list, period: int = 14) -> float:
    if not candles or len(candles) < period + 1:
        return 0.0
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    atr_series = calc_atr(highs, lows, closes, period)
    return float(atr_series[-1]) if atr_series else 0.0


def _fetch_ab_crypto_signal_candles(runtime, pair: dict, tf: str, limit: int):
    """Fetch shared Engine A/B scan candles with optional crypto Bybit experiment."""
    if (
        str((pair or {}).get("type") or "").lower() != "crypto"
        or resolve_crypto_signal_feed("AB", CONFIG) == "binance"
    ):
        return runtime.fetch_candles(pair, tf, limit), None

    result = fetch_crypto_signal_candles(
        pair,
        tf,
        limit,
        engine="AB",
        config=CONFIG,
        default_fetch=lambda pair_arg, tf_arg, limit_arg: runtime.fetch_candles(
            pair_arg, tf_arg, limit_arg
        ),
        bybit_fetch=getattr(runtime, "fetch_bybit_klines", None),
        bybit_paginated_fetch=getattr(runtime, "fetch_bybit_klines_paginated", None),
    )
    return result.candles, result.meta


def _engine_b_level_pair(conf_b: dict | None, res_b: dict | None) -> tuple[float | None, float | None]:
    conf_b = conf_b or {}
    res_b = res_b or {}
    sl = conf_b.get("execution_sl")
    if sl is None:
        sl = res_b.get("execution_sl")
    if sl is None:
        sl = res_b.get("recommended_stop_loss")
    tp = conf_b.get("execution_tp")
    if tp is None:
        tp = res_b.get("execution_tp")
    if tp is None:
        tp = res_b.get("recommended_take_profit")
    try:
        return float(sl), float(tp)
    except (TypeError, ValueError):
        return None, None


def _apply_engine_b_scan_levels(signal: dict, conf_b: dict | None, res_b: dict | None) -> None:
    sl, tp = _engine_b_level_pair(conf_b, res_b)
    if sl is None or tp is None:
        return
    signal["engine_b_execution_sl"] = sl
    signal["engine_b_execution_tp"] = tp
    signal["engine_b_rr_used_for_gate"] = (conf_b or {}).get("rr_used_for_gate")
    if not bool(CONFIG.get("ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True)):
        return
    signal["sl"] = sl
    signal["tp1"] = tp
    signal["tp2"] = tp
    signal["levelSource"] = "engine_b_execution"
    signal["level_source"] = "engine_b_execution"


def _apply_engine_b_scan_confidence_gate(
    signal: dict,
    conf_b: dict | None,
    style_profile: dict | None,
    regime_label: str | None,
    asset_type: str = "",
) -> tuple[bool, float]:
    """Apply Engine B's final style/regime score floor to scan alignment."""
    gate_ok, scaled_min = engine_b_confidence_passes(
        conf_b,
        style_profile,
        regime_label,
        asset_type,
    )
    signal["enginesAligned"] = bool(gate_ok)
    signal["engine_b_min_score_scaled"] = scaled_min
    if isinstance(conf_b, dict):
        conf_b["passed"] = bool(gate_ok)
        conf_b["checklist_passed"] = bool(gate_ok)
        conf_b["min_score_scaled"] = scaled_min
    return bool(gate_ok), scaled_min


def _engine_b_scan_combined_conviction(
    a_norm: float,
    b_norm: float,
    weights: dict | None,
    *,
    direction_aligned: bool,
) -> float:
    """Blend Engine B only when its independent direction agrees with Engine A."""
    try:
        a_val = max(0.0, min(1.0, float(a_norm)))
    except (TypeError, ValueError):
        a_val = 0.0
    try:
        b_val = max(0.0, min(1.0, float(b_norm)))
    except (TypeError, ValueError):
        b_val = 0.0

    if not direction_aligned:
        return round(a_val * 0.60, 4)

    w = weights or ENGINE_C_AB_WEIGHTS.get("TRENDING", {"A": 0.40, "B": 0.60})
    try:
        w_a = float(w.get("A", 0.40))
    except (TypeError, ValueError, AttributeError):
        w_a = 0.40
    try:
        w_b = float(w.get("B", 0.60))
    except (TypeError, ValueError, AttributeError):
        w_b = 0.60
    return round((a_val * w_a) + (b_val * w_b), 4)


def _engine_b_structure_ready_watchlist_config(config: dict | None = None) -> dict:
    cfg = config or CONFIG
    raw = cfg.get("ENGINE_B_STRUCTURE_READY_WATCHLIST", {}) or {}
    return raw if isinstance(raw, dict) else {}


def _engine_b_structure_ready_watchlist_detail(
    conf_b: dict | None,
    res_b: dict | None,
    *,
    config: dict | None = None,
) -> dict | None:
    """Return safe scan-only diagnostics for strong B structures awaiting trigger.

    This intentionally does not alter Engine B's pass state. It only lets full
    scan expose candidates that have meaningful structure but are blocked by the
    final price-action trigger, so they can be monitored without execution.
    """
    cfg = _engine_b_structure_ready_watchlist_config(config)
    if not bool(cfg.get("ENABLED", False)):
        return None
    if not isinstance(conf_b, dict) or not isinstance(res_b, dict):
        return None
    if bool(conf_b.get("passed", False)):
        return None

    try:
        score = float(conf_b.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        min_score = float(conf_b.get("min_score_scaled", 0.0) or 0.0)
    except (TypeError, ValueError):
        min_score = 0.0
    try:
        max_possible = float(conf_b.get("max_possible", 0.0) or 0.0)
    except (TypeError, ValueError):
        max_possible = 0.0
    try:
        min_ratio = float(cfg.get("MIN_SCORE_RATIO", 0.85))
    except (TypeError, ValueError):
        min_ratio = 0.85
    min_ratio = max(0.0, min(1.0, min_ratio))

    if min_score > 0:
        score_ok = score >= (min_score * min_ratio)
    elif max_possible > 0:
        score_ok = (score / max_possible) >= min_ratio
    else:
        score_ok = False
    if not score_ok:
        return None

    asset_type = str(res_b.get("asset_type") or conf_b.get("asset_type") or "").lower()
    forex_gate_near_miss = asset_type == "forex" and bool(
        cfg.get("FOREX_GATE_NEAR_MISS_ENABLED", False)
    )
    asset_gate_near_miss = (
        asset_type in {"forex", "stock", "index", "commodity", "etf", "etf_bond"}
        and bool(cfg.get("ASSET_GATE_NEAR_MISS_ENABLED", False))
    )
    gate_near_miss = forex_gate_near_miss or asset_gate_near_miss
    trigger_ok = bool(conf_b.get("trigger_ok", False))
    entry_ok = bool(conf_b.get("entry_ok", False))
    if (
        bool(cfg.get("REQUIRE_TRIGGER_MISSING", True))
        and not gate_near_miss
        and (trigger_ok or entry_ok)
    ):
        return None

    if (
        bool(cfg.get("REQUIRE_STRUCTURE_OK", True))
        and not gate_near_miss
        and not bool(conf_b.get("structure_ok", False))
    ):
        return None

    structure_evidence = {
        "bos_confirmed": bool(res_b.get("bos_confirmed", False)),
        "choch_confirmed": bool(res_b.get("choch_confirmed", False)),
        "liquidity_sweep": bool(res_b.get("liquidity_sweep", False)),
        "ob_at_zone": bool(res_b.get("ob_at_zone", False)),
        "fvg_overlap": bool(res_b.get("fvg_overlap", False)),
        "breaker_block": bool(res_b.get("breaker_block", False)),
    }
    if not any(structure_evidence.values()):
        return None

    location_context = bool(
        conf_b.get("location_ok", False)
        or conf_b.get("zone_ok", False)
        or res_b.get("zone_touched", False)
        or res_b.get("near_active_zone", False)
        or res_b.get("ob_at_zone", False)
        or res_b.get("fvg_overlap", False)
    )
    if bool(cfg.get("REQUIRE_LOCATION_CONTEXT", True)) and not location_context:
        if not gate_near_miss:
            return None

    if gate_near_miss:
        try:
            min_confirmations = int(cfg.get("FOREX_MIN_GATE_CONFIRMATIONS", 3))
        except (TypeError, ValueError):
            min_confirmations = 3
        min_confirmations = max(1, min(5, min_confirmations))
        space_gate_ok = bool(conf_b.get("space_gate_ok", conf_b.get("room_ok", False)))
        gate_confirmations = [
            bool(conf_b.get("structure_ok", False)),
            bool(conf_b.get("location_ok", False)),
            entry_ok,
            space_gate_ok,
            bool(conf_b.get("rr_ok", False)),
        ]
        if sum(1 for item in gate_confirmations if item) < min_confirmations:
            return None

    failed_gates = list(conf_b.get("failed_gate_names") or [])
    reason = "Engine B structure ready; awaiting price-action trigger"
    execution_block_reason = "awaiting_price_action_trigger"
    if gate_near_miss:
        blocked = ", ".join(failed_gates) if failed_gates else "gate_near_miss"
        label = "forex" if forex_gate_near_miss else asset_type or "asset"
        reason = f"Engine B {label} near miss; blocked by {blocked}"
        execution_block_reason = "engine_b_gate_near_miss"
    return {
        "reason": reason,
        "execution_allowed": False,
        "execution_block_reason": execution_block_reason,
        "asset_type": asset_type or None,
        "forex_gate_near_miss": forex_gate_near_miss,
        "asset_gate_near_miss": asset_gate_near_miss,
        "gate_near_miss": gate_near_miss,
        "score": round(score, 4),
        "min_score_scaled": round(min_score, 4),
        "min_score_ratio": min_ratio,
        "trigger_ok": trigger_ok,
        "entry_ok": entry_ok,
        "structure_ok": bool(conf_b.get("structure_ok", False)),
        "location_context": location_context,
        "failed_gates": failed_gates,
        "structure_evidence": structure_evidence,
    }


def _mark_engine_b_structure_ready_watchlist(
    signal: dict,
    conf_b: dict | None,
    res_b: dict | None,
    *,
    config: dict | None = None,
) -> dict | None:
    detail = _engine_b_structure_ready_watchlist_detail(conf_b, res_b, config=config)
    if not detail:
        return None
    signal["engine_b_structure_ready_watchlist"] = True
    signal["engine_b_structure_ready_detail"] = detail
    signal["engine_b_execution_blocked"] = True
    signal["engine_b_execution_block_reason"] = detail["execution_block_reason"]
    return detail


def _apply_engine_b_scan_gate(signal: dict, tier: str, reason: str) -> tuple[str, str]:
    """Demote Engine A trade tier when B confirmation is required and missing.

    Only runs when ``ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED`` is True.
    """
    if not _engine_b_scan_confirmation_gate_enabled():
        return tier, reason
    if tier != "trade":
        return tier, reason
    if bool(signal.get("enginesAligned", False)):
        return tier, reason
    detail = signal.get("engine_b_error") or signal.get("engine_b_verdict") or "not_confirmed"
    return "watchlist", f"Engine B confirmation failed ({detail})"


def _apply_engine_b_only_watchlist_scan_tier(
    signal: dict,
    tier: str,
    reason: str,
    *,
    config: dict | None = None,
) -> tuple[str, str]:
    """Surface scan-only Engine B passes that Engine A tiering would hide.

    This never promotes to trade. It only preserves a passed naked-structure
    setup for operator review when Engine A is below its scan floor.
    """
    cfg = config or CONFIG
    if not bool(cfg.get("ENGINE_B_SCAN_B_ONLY_WATCHLIST_ENABLED", False)):
        return tier, reason
    if tier == "trade":
        return tier, reason
    if not bool(signal.get("engine_b_confidence_passed", False)):
        return tier, reason

    diagnostics = signal.setdefault("scanDiagnostics", [])
    safety_block_codes = {
        "closed_exchange",
        "event_risk",
        "macro_event_risk",
        "inactive_pair",
        "engine_b_error",
    }
    codes = {d.get("code") for d in diagnostics if isinstance(d, dict)}
    if codes.intersection(safety_block_codes):
        return tier, reason

    b_dir = signal.get("engine_b_direction")
    a_dir = signal.get("direction")
    aligned = bool(signal.get("enginesAligned", False))
    if b_dir in ("LONG", "SHORT") and a_dir in ("LONG", "SHORT") and b_dir != a_dir:
        detail = f"Engine B-only watchlist: B {b_dir}, A {a_dir}"
    elif aligned:
        detail = "Engine B passed; Engine A below scan floor"
    else:
        detail = "Engine B-only watchlist"

    diagnostics.append({"code": "engine_b_only_watchlist", "detail": detail})
    signal["engine_b_execution_blocked"] = True
    signal["engine_b_execution_block_reason"] = "engine_b_only_scan_watchlist"
    return "watchlist", detail


def _apply_engine_b_structure_ready_scan_tier(
    signal: dict,
    tier: str,
    reason: str,
    *,
    config: dict | None = None,
) -> tuple[str, str]:
    """Promote scan-only Engine B near-ready rows to watchlist, never trade."""
    cfg = _engine_b_structure_ready_watchlist_config(config)
    if not bool(cfg.get("ENABLED", False)):
        return tier, reason
    if tier == "trade":
        return tier, reason
    detail = signal.get("engine_b_structure_ready_detail")
    if not isinstance(detail, dict):
        return tier, reason
    if bool(detail.get("execution_allowed", False)):
        return tier, reason

    safety_block_codes = {
        "closed_exchange",
        "event_risk",
        "inactive_pair",
        "engine_b_error",
    }
    codes = {d.get("code") for d in signal.get("scanDiagnostics", []) if isinstance(d, dict)}
    if codes.intersection(safety_block_codes):
        return tier, reason

    diagnostics = signal.setdefault("scanDiagnostics", [])
    diagnostics.append(
        {
            "code": "engine_b_structure_ready_watchlist",
            "detail": detail.get("reason", "Engine B structure ready; awaiting trigger"),
        }
    )
    return "watchlist", str(detail.get("reason") or "Engine B structure ready; awaiting trigger")


# --- Engine B independent scan helpers --------------------------------------
#
# These helpers exist so the full-scan path can produce Engine B signals or
# Engine B rejection/funnel rows independently of Engine A. They keep Engine A
# scoring, Engine B thresholds, risk/kill-switch and live execution untouched.

# Status sentinel that downstream consumers (UI, audit, tests) use to recognise
# a row whose source engine is Engine B (not an Engine A signal with a B
# overlay).
ENGINE_B_SOURCE = "ENGINE_B"
ENGINE_A_SOURCE = "ENGINE_A"


def _make_engine_b_only_signal_stub(pair: dict) -> dict:
    """Build a minimal sig stub when Engine A produced no signal.

    The stub keeps the downstream pipeline able to attach Engine B overlay
    fields (verdict, score, SL/TP, RR, funnel) and route the row as an Engine
    B-only result. Engine A scoring fields are zeroed/absent — the row is
    never auto-traded and is classified by ``_classify_engine_b_only_signal``.
    """
    return {
        "engine_source": ENGINE_B_SOURCE,
        "engine": "B",
        "engine_name": "Engine B",
        "symbol": pair.get("symbol"),
        "display": pair.get("display"),
        "type": pair.get("type"),
        "asset_type": pair.get("type"),
        "direction": None,
        "confluenceScore": 0.0,
        "scoreNorm": 0.0,
        "maxScore": 0.0,
        "combinedConviction": 0.0,
        "enginesAligned": False,
        "engine_a_present": False,
        "scanDiagnostics": [],
        "warnings": [],
    }


def _engine_b_independent_direction_probe(
    pair: dict,
    *,
    engine,
    d1_candles: list,
    zone_candles: list,
    entry_candles: list,
    current_price: float,
    atr: float,
    regime_label: str | None,
    style_profile: dict,
    resolved_style: str,
    asset_type: str,
    d1_snap: dict | None,
    h4_snap: dict | None,
) -> tuple[str | None, dict | None, dict | None]:
    """Pick best Engine B direction independently of Engine A.

    Runs ``analyze_structure`` for both LONG and SHORT, and for any CLEAR
    verdict computes confidence and tests the style/regime gate. Returns the
    best ``(direction, res_b, conf_b)`` tuple — preferring gate-passed over
    not-passed, then higher confidence score. Returns ``(None, None, None)``
    when neither direction has a CLEAR structural verdict.
    """
    best: tuple[bool, float, str, dict, dict] | None = None
    for try_direction in ("LONG", "SHORT"):
        res_b = engine.set_registry_context(
            pair.get("symbol") or pair.get("display")
        ).analyze_structure(
            d1_candles or [],
            zone_candles,
            entry_candles,
            current_price,
            try_direction,
            atr,
            regime_label,
            fallback_rr=style_profile.get("fallback_rr", 2.0),
            asset_type=asset_type,
            d1_snap=d1_snap or {},
            h4_snap=h4_snap or {},
            style=resolved_style,
            pair=pair,
        )
        if res_b.get("structural_verdict") != "CLEAR":
            continue
        conf_b = engine.calculate_confidence(
            res_b,
            current_price,
            try_direction,
            entry_candles=entry_candles or zone_candles,
            style_profile=style_profile,
        )
        gate_ok, _ = engine_b_confidence_passes(
            conf_b, style_profile, regime_label, asset_type,
        )
        try:
            score = float(conf_b.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        candidate = (bool(gate_ok), score, try_direction, res_b, conf_b)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        return None, None, None
    _, _, direction, res_b, conf_b = best
    return direction, res_b, conf_b


def _annotate_engine_b_only_signal_for_scan(
    signal: dict,
    pair: dict,
    ds_ctx: dict,
    earnings_ctx: dict,
    closed_exchanges: set,
    news_ctx: dict,
) -> dict:
    """Minimal scan annotation for Engine B-only rows.

    Mirrors the safety-block portion of ``annotate_signal_for_scan`` (exchange
    closed / event risk / inactive pair) but does not inject Engine A
    threshold or low-confluence diagnostics, which do not apply to an Engine B
    independent row.
    """
    signal["isEnabled"] = pair.get("enabled", True)
    signal["exchangeClosed"] = _pair_exchange_closed(pair, closed_exchanges)
    signal["eventRisk"] = _build_event_risk(
        pair, ds_ctx, earnings_ctx, closed_exchanges
    )
    signal["newsCtx"] = news_ctx
    if CONFIG.get("EVENT_RISK_ENABLED", True):
        try:
            from event_risk import check_event_risk
            ev_risk = check_event_risk(
                pair.get("display", ""),
                pair.get("type", ""),
                lookahead_hours=CONFIG.get("EVENT_RISK_HOURS", 4),
            )
            signal["macroEventRisk"] = {
                "blocked": not ev_risk.get("allowed", True),
                "reason": ev_risk.get("reason", ""),
                "events": ev_risk.get("events", []),
            }
        except Exception as e:
            log.warning(f"Error checking macro event risk for B-only scan: {e}")
            signal["macroEventRisk"] = {
                "blocked": False,
                "reason": "Error checking macro events",
                "events": [],
            }
    diagnostics = []
    if signal.get("exchangeClosed"):
        diagnostics.append({"code": "closed_exchange", "detail": "Exchange closed"})
    if signal["eventRisk"].get("hardBlock"):
        diagnostics.append(
            {"code": "event_risk", "detail": ", ".join(signal["eventRisk"].get("reasons", []))}
        )
    if signal.get("macroEventRisk", {}).get("blocked"):
        diagnostics.append(
            {"code": "macro_event_risk", "detail": signal["macroEventRisk"].get("reason", "")}
        )
    if not pair.get("enabled", True):
        diagnostics.append({"code": "inactive_pair", "detail": "Pair not auto-enabled"})
    if signal.get("engine_b_error"):
        diagnostics.append(
            {"code": "engine_b_error", "detail": str(signal.get("engine_b_error"))}
        )
    signal["scanDiagnostics"] = diagnostics
    return signal


def _classify_engine_b_only_signal(signal: dict, pair: dict) -> tuple[str, str]:
    """Classifier for Engine B-only rows.

    Uses Engine B gates only — never Engine A threshold or combinedConviction.
    Result is always either ``"watchlist"`` (when Engine B confidence passed
    and no safety blocks) or ``"skip"``. Engine B-only rows are never tier
    ``"trade"``: live auto-trade requires the full A-driven path.
    """
    diagnostics_iter = (
        d for d in signal.get("scanDiagnostics", []) if isinstance(d, dict)
    )
    codes: set[str] = {str(d.get("code")) for d in diagnostics_iter if d.get("code") is not None}
    safety_block_codes = {
        "closed_exchange",
        "event_risk",
        "macro_event_risk",
        "inactive_pair",
    }
    blocked = codes & safety_block_codes
    if blocked:
        return "skip", f"Engine B blocked by safety codes: {','.join(sorted(blocked))}"
    if not pair.get("enabled", True):
        return "skip", "Pair not auto-enabled"
    if signal.get("engine_b_error"):
        return "skip", f"Engine B error: {signal.get('engine_b_error')}"
    funnel = signal.get("engine_b_scan_gate_funnel")
    if not isinstance(funnel, dict):
        funnel = {}
    verdict = signal.get("engine_b_verdict") or funnel.get("structure_verdict")
    if verdict != "CLEAR":
        skip_stage = funnel.get("engine_b_skip_stage")
        if skip_stage:
            return "skip", f"Engine B no signal: {skip_stage}"
        return "skip", f"Engine B structural verdict {verdict or 'NONE'}"
    if not bool(signal.get("engine_b_confidence_passed", False)):
        failed = (
            signal.get("engine_b_failed_gate_names")
            or funnel.get("failed_gate_names")
            or []
        )
        if failed:
            failed_str = ",".join(str(g) for g in failed)
            if any(
                str(g).startswith("rr=") or str(g).lower() == "rr_gate"
                for g in failed
            ):
                return "skip", f"Engine B RR gate failed: {failed_str}"
            return "skip", f"Engine B gates failed: {failed_str}"
        return "skip", "Engine B confidence gate not passed"
    detail = "Engine B-only watchlist"
    if signal.get("engine_b_direction"):
        detail = f"Engine B-only watchlist ({signal.get('engine_b_direction')})"
    return "watchlist", detail


def _a_only_auto_weight(pair: dict | None, config: dict | None = None) -> float:
    """Return the config-gated A-only auto conviction weight."""
    cfg = config or CONFIG
    weight_cfg = cfg.get("AUTO_TRADE_A_ONLY_WEIGHT", {}) or {}
    asset_type = ""
    try:
        asset_type = str(pair.get("type", "")).lower() if isinstance(pair, dict) else ""
    except Exception:
        asset_type = ""

    try:
        if isinstance(weight_cfg, dict):
            weight = float(weight_cfg.get(asset_type, weight_cfg.get("default", 0.60)))
        else:
            weight = float(weight_cfg)
    except Exception:
        weight = 0.60
    return max(0.0, min(1.0, weight))


def _linear_percentile(values: list[float], p: float) -> float | None:
    """Return the p-th percentile (0–100) with linear interpolation. ``values`` may be unsorted."""
    if not values:
        return None
    xs = sorted(float(x) for x in values)
    if len(xs) == 1:
        return xs[0]
    p = max(0.0, min(100.0, p))
    k = (len(xs) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (k - f) * (xs[c] - xs[f])


def _scalar_float_gate(x: Any) -> float | None:
    """JSON-safe finite float helper for funnel diagnostics."""
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return round(v, 8)
    except (TypeError, ValueError):
        return None


def _attach_engine_b_scan_gate_funnel(
    *,
    sig: dict,
    pair: dict,
    score_group: str | None,
    resolved_style: str | None,
    style_profile_b: dict | None,
    conf_b: dict | None,
    res_b: dict | None,
    extras: dict[str, Any],
) -> None:
    """Populate report-only funnel dict on sig; never changes pass/fail or execution."""
    if not bool(CONFIG.get("ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", True)):
        return
    sp = style_profile_b if isinstance(style_profile_b, dict) else {}
    cnf = conf_b if isinstance(conf_b, dict) else {}
    rb = res_b if isinstance(res_b, dict) else {}
    ptype = str(pair.get("type") or "").lower()
    atr_val = extras.get("atr_value")
    skip_stage = extras.get("engine_b_skip_stage")
    if isinstance(skip_stage, str) and not skip_stage:
        skip_stage = None
    funnel: dict[str, Any] = {
        "symbol": pair.get("display") or pair.get("symbol"),
        "asset_type": pair.get("type"),
        "score_group": score_group,
        "style": resolved_style,
        "sig_a_present": True,
        "engine_b_evaluated": bool(sig.get("engine_b_evaluated")),
        "engine_b_skip_stage": skip_stage,
        "engine_b_error": sig.get("engine_b_error"),
        "candles_ok": bool(extras.get("candles_tf_ok")),
        "atr": _scalar_float_gate(atr_val),
        "atr_source": extras.get("atr_source"),
        "bybit_atr_available": extras.get("bybit_atr_available"),
        "fallback_allowed": (
            bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False))
            if ptype == "crypto"
            else None
        ),
        "structure_verdict": rb.get("structural_verdict"),
        "direction_a": sig.get("direction"),
        "direction_b": sig.get("engine_b_direction"),
        "structure_ok": cnf.get("structure_ok"),
        "location_ok": cnf.get("location_ok"),
        "entry_ok": cnf.get("entry_ok"),
        "room_ok": cnf.get("room_ok"),
        "space_gate_ok": cnf.get("space_gate_ok"),
        "rr_ok": cnf.get("rr_ok"),
        "score": _scalar_float_gate(cnf.get("score")),
        "min_score_scaled": _scalar_float_gate(sig.get("engine_b_min_score_scaled")),
        "min_rr": _scalar_float_gate(sp.get("min_rr")),
        "entry": _scalar_float_gate(extras.get("entry_price")),
        "sl": sig.get("sl"),
        "tp": sig.get("tp1"),
        "structural_sl": cnf.get("structural_sl"),
        "structural_tp": cnf.get("structural_tp"),
        "execution_sl": cnf.get("execution_sl"),
        "execution_tp": cnf.get("execution_tp"),
        "rr": _scalar_float_gate(cnf.get("rr")),
        "rr_source": cnf.get("rr_source"),
        "execution_level_reject_reason": cnf.get("execution_level_reject_reason"),
        "tp_structural_limited": rb.get("tp_structural_limited"),
        "failed_gate_names": list(cnf.get("failed_gate_names") or []),
        "final_tier": None,
        "final_reason": None,
        "engine_b_confidence_passed": sig.get("engine_b_confidence_passed"),
        "synthetic_fallback_rr_tp_enabled": bool(
            CONFIG.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", False)
        ),
        "rr_can_satisfy_space_gate_crypto_config": False,
        "rr_space_gate_enabled_overlay": cnf.get("rr_space_gate_enabled"),
    }
    # Config truth for crypto RR↔space (not re-evaluating logic).
    _rr_gate_cfg = CONFIG.get("ENGINE_B_RR_CAN_SATISFY_SPACE_GATE", False)
    if isinstance(_rr_gate_cfg, dict):
        funnel["rr_can_satisfy_space_gate_crypto_config"] = bool(_rr_gate_cfg.get("crypto", False))
    funnel["structure_executed"] = bool(extras.get("structure_executed"))
    sig["engine_b_scan_gate_funnel"] = funnel


def _patch_engine_b_funnel_final_tier(sig: dict, tier: str, tier_reason: str) -> None:
    if not bool(CONFIG.get("ENGINE_B_SCAN_GATE_FUNNEL_ENABLED", True)):
        return
    fd = sig.get("engine_b_scan_gate_funnel")
    if isinstance(fd, dict):
        fd["final_tier"] = tier
        fd["final_reason"] = tier_reason


def compute_scan_quantile_floors(
    scores_by_type: dict[str, list[float]],
    *,
    enabled: bool,
    min_samples: int,
    top_fraction_cfg: dict,
) -> dict[str, float | None]:
    """Per asset class, score floor such that ~top ``top_fraction`` of *this scan* sit above it.

    ``top_fraction`` 0.2 → use the (1 - 0.2) × 100 = 80th percentile of cross-sectional scores.
    Returns ``None`` for a class when disabled, too few samples, or missing/invalid fraction.
    """
    out: dict[str, float | None] = {}
    if not enabled:
        for k in scores_by_type:
            out[k] = None
        return out

    default_frac = top_fraction_cfg.get("default")

    for ptype, scores in scores_by_type.items():
        if len(scores) < min_samples:
            out[ptype] = None
            continue
        raw = top_fraction_cfg.get(ptype, default_frac)
        if raw is None:
            out[ptype] = None
            continue
        try:
            frac = float(raw)
        except (TypeError, ValueError):
            out[ptype] = None
            continue
        if frac <= 0.0 or frac >= 1.0:
            out[ptype] = None
            continue
        pct = 100.0 * (1.0 - frac)
        cut = _linear_percentile(scores, pct)
        out[ptype] = cut

    return out


def _normalize_style(style: str | None) -> str:
    s = (style or "auto").lower()
    return s if s in ("auto", "swing", "intraday", "scalp") else "auto"


def _humanize_forex_zero_reason(code: str) -> str:
    labels = {
        "hurst_veto_trend": "Hurst veto blocked the trend path",
        "trend_path_inactive": "Trend path produced no score",
        "breakout_inactive": "London breakout path inactive",
        "session_inactive": "Forex session inactive",
        "zero_base_score": "Forex base score stayed at zero",
        "trend_gate_adx_below_min": "Trend gate blocked: ADX below minimum",
        "trend_gate_mixed_ema_alignment": "Trend gate blocked: D1/H4 EMA alignment mixed",
        "trend_gate_missing_ema_inputs": "Trend gate blocked: EMA inputs missing",
        "trend_gate_long_margin_or_slope_failed": "Trend gate blocked: long margin/slope failed",
        "trend_gate_short_margin_or_slope_failed": "Trend gate blocked: short margin/slope failed",
        "trend_gate_blocked": "Trend gate blocked",
    }
    key = str(code or "").strip()
    if key in labels:
        return labels[key]
    return key.replace("_", " ")


def annotate_signal_for_scan(
    signal: dict,
    pair: dict,
    threshold: float,
    ds_ctx: dict,
    earnings_ctx: dict,
    closed_exchanges: set,
    news_ctx: dict,
) -> dict:
    signal["scanThreshold"] = threshold
    signal["isEnabled"] = pair.get("enabled", True)
    signal["exchangeClosed"] = _pair_exchange_closed(pair, closed_exchanges)
    signal["eventRisk"] = _build_event_risk(
        pair, ds_ctx, earnings_ctx, closed_exchanges
    )
    signal["newsCtx"] = news_ctx

    if CONFIG.get("EVENT_RISK_ENABLED", True):
        try:
            from event_risk import check_event_risk
            ev_risk = check_event_risk(pair.get("display", ""), pair.get("type", ""), lookahead_hours=CONFIG.get("EVENT_RISK_HOURS", 4))
            signal["macroEventRisk"] = {
                "blocked": not ev_risk.get("allowed", True),
                "reason": ev_risk.get("reason", ""),
                "events": ev_risk.get("events", [])
            }
        except Exception as e:
            log.warning(f"Error checking macro event risk for scan: {e}")
            signal["macroEventRisk"] = {"blocked": False, "reason": "Error checking macro events", "events": []}

    diagnostics = []

    if signal["confluenceScore"] < threshold:
        diagnostics.append(
            {
                "code": "low_confluence",
                "detail": f"Score {signal['confluenceScore']}/{threshold}",
            }
        )

    if signal.get("trendState") == "RANGING":
        diagnostics.append({"code": "ranging", "detail": "Ranging regime"})

    if signal.get("trendState") == "DEAD RANGING":
        diagnostics.append({"code": "dead_ranging", "detail": "Dead ranging regime"})

    if any("COUNTER-TREND" in w for w in signal.get("warnings", [])):
        diagnostics.append(
            {"code": "counter_trend", "detail": "Counter-trend warning active"}
        )

    if signal.get("exchangeClosed"):
        diagnostics.append({"code": "closed_exchange", "detail": "Exchange closed"})

    if signal["eventRisk"].get("hardBlock"):
        diagnostics.append(
            {
                "code": "event_risk",
                "detail": ", ".join(signal["eventRisk"].get("reasons", [])),
            }
        )

    if signal.get("macroEventRisk", {}).get("blocked"):
        diagnostics.append(
            {
                "code": "macro_event_risk",
                "detail": signal["macroEventRisk"].get("reason", "Blocked by macro event"),
            }
        )

    if signal.get("engine_b_error"):
        diagnostics.append(
            {
                "code": "engine_b_error",
                "detail": str(signal.get("engine_b_error")),
            }
        )

    if not pair.get("enabled", True):
        diagnostics.append(
            {
                "code": "inactive_pair",
                "detail": "Pair not auto-enabled for live trading",
            }
        )

    if pair.get("type") == "forex":
        factor_scores = signal.get("factorScores") or {}
        zero_reasons = factor_scores.get("zero_score_reasons") or []
        if (
            isinstance(zero_reasons, list)
            and zero_reasons
            and float(signal.get("confluenceScore", 0) or 0) <= 0
        ):
            for reason_code in zero_reasons:
                diagnostics.append(
                    {
                        "code": f"forex_{reason_code}",
                        "detail": _humanize_forex_zero_reason(str(reason_code)),
                    }
                )

    signal["scanDiagnostics"] = diagnostics

    warn_list = signal.setdefault("warnings", [])
    for reason in signal["eventRisk"].get("reasons", []):
        warn = f"EVENT RISK: {reason}"

        if warn not in warn_list:
            warn_list.append(warn)

    return signal


def run_full_scan(style: str = "auto", asset_class: str | None = None) -> dict[str, Any]:
    """Parallel scan of tracked pairs. Optional asset_class filter."""
    r = rt()

    # REGRESSION CHECK: Print config state at scan startup
    print("\n" + "="*80)
    print("REGRESSION CHECK - CONFIG STATE")
    print("="*80)
    print(f"ENGINE_B_CRYPTO_PROFILE_ENABLED: {CONFIG.get('ENGINE_B_CRYPTO_PROFILE_ENABLED')}")
    print(f"ENGINE_B_CRYPTO_TARGET_V2_ENABLED: {CONFIG.get('ENGINE_B_CRYPTO_TARGET_V2_ENABLED')}")
    print(f"ENGINE_B_CRYPTO_TRIGGER_PROFILE_ENABLED: {CONFIG.get('ENGINE_B_CRYPTO_TRIGGER_PROFILE_ENABLED')}")
    print(f"ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS: {CONFIG.get('ENGINE_B_CRYPTO_ALLOW_FALLBACK_TARGET_FOR_PASS')}")
    print(f"ENGINE_B_CRYPTO_REQUIRE_STRUCTURAL_TARGET_FOR_PASS: {CONFIG.get('ENGINE_B_CRYPTO_REQUIRE_STRUCTURAL_TARGET_FOR_PASS')}")
    print(f"Requested asset_class: {asset_class}")
    print(f"Requested style: {style}")
    print("="*80 + "\n")

    _requested_style = _normalize_style(style)

    _valid_classes = {"crypto", "forex", "stock", "commodity", "index"}

    _ac = asset_class.lower().strip() if asset_class else None

    if _ac and _ac not in _valid_classes:
        return {
            "success": False,
            "error": f"Invalid asset_class '{asset_class}'. Valid: {sorted(_valid_classes)}",
            "signals": [],
            "tradeSignals": [],
            "watchlist": [],
            "errors": [],
            "skipped": [],
            "btcBias": "neutral",
            "totalPairs": 0,
            "activePairs": 0,
            "scannedAt": datetime.now(timezone.utc).isoformat(),
        }

    if r.kill_switch():
        return {
            "success": False,
            "error": "Kill-switch active — system paused",
            "signals": [],
            "tradeSignals": [],
            "watchlist": [],
            "errors": [],
            "skipped": [],
            "btcBias": "neutral",
            "totalPairs": 0,
            "activePairs": 0,
            "scannedAt": datetime.now(timezone.utc).isoformat(),
        }

    if not r.scan_lock.acquire(blocking=False):
        return {
            "success": False,
            "error": "Scan already in progress",
            "signals": [],
            "tradeSignals": [],
            "watchlist": [],
            "errors": [],
            "skipped": [],
            "btcBias": "neutral",
            "totalPairs": 0,
            "activePairs": 0,
            "scannedAt": datetime.now(timezone.utc).isoformat(),
        }

    try:
        _disabled_jse_symbols = {
            p.get("symbol")
            for p in getattr(r, "JSE_PAIRS", [])
            if not p.get("enabled", True)
        }
        candidate_pairs = [
            p
            for p in r.ALL_PAIRS
            if p["display"] not in r.disabled_pairs
            and p.get("symbol") not in _disabled_jse_symbols
        ]

        if _ac:
            candidate_pairs = [p for p in candidate_pairs if p.get("type") == _ac]

        active_pairs = [p for p in candidate_pairs if p.get("enabled", True)]

        results, watchlist, errors, skipped = [], [], [], []
        threshold_audit_rows: list[dict[str, Any]] = []
        _threshold_audit_on = threshold_audit_enabled()

        scan_funnel = {
            "total": len(candidate_pairs),
            "active": len(active_pairs),
            "no_data": 0,
            "low_score": 0,
            "passed": 0,
            "watchlist": 0,
            "errors": 0,
            "closed_exchange": 0,
            "event_block": 0,
            "inactive_pair": 0,
            "counter_trend": 0,
            "dead_ranging": 0,
        }

        btc_bias = "neutral"

        _closed_exchanges = set()

        ds_ctx = {}

        earnings_ctx = {}

        try:
            _eodhd_key = os.environ.get("EODHD_KEY", "")

            if _eodhd_key:
                for exch_code in ["JSE", "US"]:
                    try:
                        req = http_requests.get(
                            f"https://eodhd.com/api/exchange-details/{exch_code}?api_token={_eodhd_key}&fmt=json",
                            timeout=8,
                        )

                        if req.status_code == 200:
                            edata = req.json()

                            if not edata.get("isOpen", True):
                                _closed_exchanges.add(exch_code)

                                log.info(f"[EXCH] {exch_code}: CLOSED")

                            else:
                                log.info(f"[EXCH] {exch_code}: OPEN")

                    except Exception as _e:
                        log.debug(f"[EXCH] {exch_code} check error: {_e}")

        except Exception as e:
            log.warning(f"[EXCH] Exchange check failed: {e}")

        log.info("Fetching market context...")

        news_ctx = r.fetch_news_context(candidate_pairs, allow_refresh=False)

        try:
            yield_ctx = r.fetch_yield_curve()

            if yield_ctx:
                news_ctx["yieldCurve"] = yield_ctx

        except Exception as e:
            log.warning(f"[YIELD] scan fetch err: {e}")

        try:
            ds_ctx = r.fetch_div_split_context()

            if ds_ctx:
                news_ctx["divSplit"] = ds_ctx

        except Exception as e:
            log.warning(f"[DIVS] scan fetch err: {e}")

        try:
            earnings_ctx = r.fetch_upcoming_earnings_context(candidate_pairs)

            if earnings_ctx:
                news_ctx["upcomingEarnings"] = earnings_ctx

        except Exception as e:
            log.warning(f"[EARN] scan fetch err: {e}")

        try:
            btc = r.fetch_candles(
                {"symbol": "BTCUSDT", "source": "binance"}, "D1", CONFIG["D1_CANDLES"]
            )

            if btc and len(btc) >= 200:
                s = calc_indicators(btc)["snap"]

                if s["ema21"] and s["ema50"] and s["ema200"]:
                    if s["ema21"] > s["ema50"] > s["ema200"]:
                        btc_bias = "bullish"

                    elif s["ema21"] < s["ema50"] < s["ema200"]:
                        btc_bias = "bearish"

            log.info(f"BTC bias: {btc_bias}")

        except Exception as e:
            log.error(f"BTC err: {e}")

        _scan_limits = scan_candle_limits()
        intermarket_snapshot = None
        _im_cfg = CONFIG.get("INTERMARKET_CONFIRMATION", {}) or {}
        if bool(_im_cfg.get("enabled")) and bool(_im_cfg.get("full_scan_time_matrix", True)):
            try:
                _im_h4_limit = max(int(_scan_limits["H4"]), 220)
                _im_preloaded_h4 = {}
                for _pair in active_pairs:
                    _candles = r.fetch_candles(_pair, "H4", _im_h4_limit)
                    if _candles:
                        _im_preloaded_h4[_pair["display"]] = _candles
                intermarket_snapshot = build_scan_snapshot(
                    r.ALL_PAIRS,
                    disabled_pairs=r.disabled_pairs,
                    etf_pairs=getattr(r, "ETF_PAIRS", []),
                    fetch_candles=r.fetch_candles,
                    config=CONFIG,
                    preloaded_h4_candles=_im_preloaded_h4,
                    force=True,
                )
                if intermarket_snapshot:
                    log.info(
                        "[INTERMARKET] prewarmed snapshot: %d symbols",
                        len((intermarket_snapshot.get("universe") or {}).get("pairs", [])),
                    )
            except Exception as _im_err:
                intermarket_snapshot = None
                log.warning("[INTERMARKET] scan snapshot build failed: %s", _im_err)

        _engine_b = NakedEngine()
        _regime_context = make_regime_smoothing_context()
        _max_workers = max(1, int(CONFIG.get("SCAN_MAX_WORKERS", 3) or 3))

        def _analyse(pair):
            try:
                _pair_style = r.resolve_scan_style(_requested_style, pair)
                _lim = scan_candle_limits()
                _im_h4 = (
                    ((intermarket_snapshot or {}).get("seriesStore", {}) or {})
                    .get(pair.get("display"), {})
                    .get("candles")
                )
                _crypto_bybit_signal_feed = (
                    pair.get("type") == "crypto"
                    and resolve_crypto_signal_feed("AB", CONFIG) == "bybit"
                )
                _d1_res, _d1_meta = _fetch_ab_crypto_signal_candles(
                    r, pair, "D1", _lim["D1"]
                )
                _h4_res, _h4_meta = _fetch_ab_crypto_signal_candles(
                    r, pair, "H4", _lim["H4"]
                )
                _h1_res, _h1_meta = _fetch_ab_crypto_signal_candles(
                    r, pair, "H1", _lim["H1"]
                )
                raw_candles = {
                    "D1": _d1_res,
                    "H4": (None if _crypto_bybit_signal_feed else _im_h4) or _h4_res,
                    "H1": _h1_res,
                }
                preloaded_market_state = {}
                preloaded_candles_for_a = dict(raw_candles)
                if pair.get("source") == "mt5" and pair.get("type") == "forex":
                    try:
                        from athena_app.services.market_state import (
                            candle_timestamp_epoch,
                            get_tf_market_state,
                        )

                        for _tf in ("D1", "H4", "H1"):
                            _raw = raw_candles.get(_tf) or []
                            _state = get_tf_market_state(
                                pair,
                                _tf,
                                candles=_raw,
                            )
                            preloaded_market_state[_tf] = _state
                            preloaded_candles_for_a[_tf] = list(_state.get("confirmed") or [])
                            _confirmed = list(_state.get("confirmed") or [])
                            _last_confirmed = _confirmed[-1] if _confirmed else None
                            _last_raw = (_raw or [])[-1] if (_raw or []) else None
                            log.debug(
                                "[SCAN][STATE][A] %s %s raw=%d confirmed=%d forming=%s is_live=%s last_confirmed_ts=%s last_raw_ts=%s",
                                pair.get("display", "?"),
                                _tf,
                                len(_raw),
                                len(_confirmed),
                                bool(_state.get("forming")),
                                bool(_state.get("is_live")),
                                candle_timestamp_epoch(_last_confirmed),
                                candle_timestamp_epoch(_last_raw),
                            )
                    except Exception as _state_err:
                        log.debug(
                            "[SCAN][STATE][A] %s market-state preload failed, falling back to raw preloads: %s",
                            pair.get("display", "?"),
                            _state_err,
                        )
                fetch_meta = {
                    "D1": _d1_meta or get_candle_fetch_meta(pair, "D1", _lim["D1"]),
                    "H4": _h4_meta or get_candle_fetch_meta(pair, "H4", _lim["H4"]),
                    "H1": _h1_meta or get_candle_fetch_meta(pair, "H1", _lim["H1"]),
                }
                rate_limited_tfs = [
                    tf
                    for tf, meta in fetch_meta.items()
                    if isinstance(meta, dict)
                    and (
                        meta.get("rateLimited") is True
                        or meta.get("detail") == "rate_limited"
                    )
                    and not raw_candles.get(tf)
                ]
                if rate_limited_tfs:
                    return pair, {
                        "skipReason": "Rate limited",
                        "skipCode": "rate_limited",
                        "skipDetail": f"Rate limited on {', '.join(rate_limited_tfs)}",
                    }, None
                sig_a = r.analyze_pair(
                    pair,
                    btc_bias,
                    style=_pair_style,
                    regime_context=_regime_context,
                    preloaded_candles=preloaded_candles_for_a,
                    preloaded_market_state=preloaded_market_state,
                    preloaded_fetch_meta=fetch_meta,
                    intermarket_snapshot=intermarket_snapshot,
                )

                # REGRESSION CHECK: Log per-pair Engine A details
                d1_count = len(raw_candles.get("D1", []))
                h4_count = len(raw_candles.get("H4", []))
                h1_count = len(raw_candles.get("H1", []))
                if sig_a:
                    score = sig_a.get("confluenceScore", 0)
                    max_score = sig_a.get("maxScore", 3.0)
                    direction = sig_a.get("direction", "NONE")
                    print(f"[REGRESSION-A] {pair['display']:12s} type={pair.get('type'):8s} D1={d1_count:3d} H4={h4_count:3d} H1={h1_count:3d} score={score:.2f}/{max_score:.1f} dir={direction:5s}")
                else:
                    print(f"[REGRESSION-A] {pair['display']:12s} type={pair.get('type'):8s} D1={d1_count:3d} H4={h4_count:3d} H1={h1_count:3d} NO SIGNAL")

                # Engine separation: when Engine A produces no signal we MUST
                # still run Engine B and emit either an Engine B-only signal
                # row or an Engine B rejection/funnel row. Engine B output
                # must never depend on Engine A.
                engine_b_scan_only = False
                if not sig_a:
                    sig_a = _make_engine_b_only_signal_stub(pair)
                    engine_b_scan_only = True
                else:
                    sig_a.setdefault("engine_source", ENGINE_A_SOURCE)
                    sig_a.setdefault("engine", "A")
                    sig_a.setdefault("engine_name", "Engine A")
                    sig_a["engine_a_present"] = True

                    if sig_a.get("direction") not in ("LONG", "SHORT"):
                        engine_b_scan_only = True

                ptype = pair.get("type", "")
                try:
                    _eb_snap = {"conf": None, "res": None}
                    _eb_funnel_extras: dict[str, Any] = {
                        "candles_tf_ok": False,
                        "atr_value": None,
                        "atr_source": None,
                        "bybit_atr_available": None,
                        "engine_b_skip_stage": None,
                        "regime_label": None,
                        "structure_executed": False,
                        "entry_price": None,
                    }
                    _pair_score_group = get_pair_score_group(pair)
                    resolved_style_b, style_profile_b = r.naked_scan_style_profile(
                        _pair_style,
                        score_group=_pair_score_group,
                        asset_type=ptype,
                    )

                    d1 = raw_candles.get("D1")
                    h4 = raw_candles.get("H4")
                    h1 = raw_candles.get("H1")
                    _engine_b_overlay_state_dbg = None
                    _engine_b_last_confirmed_ts = {}
                    if pair.get("source") == "mt5":
                        # Harden MT5 candle-state handling: use confirmed bars explicitly.
                        # Avoid naive last-bar chopping; match dedicated Engine B live scan discipline.
                        try:
                            from athena_app.services.engine_b_market_state import (
                                engine_b_live_market_state,
                            )

                            _engine_b_overlay_state_dbg = {}
                            for _tf, _raw in (("D1", d1), ("H4", h4), ("H1", h1)):
                                _raw_list = _raw or []
                                _st = engine_b_live_market_state(
                                    pair,
                                    _tf,
                                    len(_raw_list),
                                    candles=_raw_list,
                                )
                                _engine_b_overlay_state_dbg[_tf] = {
                                    "raw": len(_raw_list),
                                    "confirmed": len(_st.get("confirmed") or []),
                                    "forming": bool(_st.get("forming")),
                                    "is_live": bool(_st.get("is_live")),
                                }
                                _b_confirmed = list(_st.get("confirmed") or [])
                                _engine_b_last_confirmed_ts[_tf] = (
                                    _b_confirmed[-1].get("time")
                                    if _b_confirmed
                                    else None
                                )
                                log.debug(
                                    "[SCAN+B][STATE] %s %s raw=%d confirmed=%d forming=%s is_live=%s",
                                    pair.get("display", "?"),
                                    _tf,
                                    len(_raw_list),
                                    len(_st.get("confirmed") or []),
                                    bool(_st.get("forming")),
                                    bool(_st.get("is_live")),
                                )
                                if _tf == "D1":
                                    d1 = list(_st.get("confirmed") or [])
                                elif _tf == "H4":
                                    h4 = list(_st.get("confirmed") or [])
                                else:
                                    h1 = list(_st.get("confirmed") or [])
                            if pair.get("type") == "forex":
                                _a_d1_ts = (preloaded_candles_for_a.get("D1") or [{}])[-1].get("time")
                                _a_h4_ts = (preloaded_candles_for_a.get("H4") or [{}])[-1].get("time")
                                _a_h1_ts = (preloaded_candles_for_a.get("H1") or [{}])[-1].get("time")
                                log.debug(
                                    "[SCAN][STATE][A_vs_B] %s D1_same=%s H4_same=%s H1_same=%s A_ts=(%s,%s,%s) B_ts=(%s,%s,%s)",
                                    pair.get("display", "?"),
                                    _a_d1_ts == _engine_b_last_confirmed_ts.get("D1"),
                                    _a_h4_ts == _engine_b_last_confirmed_ts.get("H4"),
                                    _a_h1_ts == _engine_b_last_confirmed_ts.get("H1"),
                                    _a_d1_ts,
                                    _a_h4_ts,
                                    _a_h1_ts,
                                    _engine_b_last_confirmed_ts.get("D1"),
                                    _engine_b_last_confirmed_ts.get("H4"),
                                    _engine_b_last_confirmed_ts.get("H1"),
                                )
                        except Exception as _ms_err:
                            log.debug(
                                "[SCAN+B][STATE] %s split_market_state failed, falling back to naive chop: %s",
                                pair.get("display", "?"),
                                _ms_err,
                            )
                            if d1 and len(d1) > 1:
                                d1 = d1[:-1]
                            if h4 and len(h4) > 1:
                                h4 = h4[:-1]
                            if h1 and len(h1) > 1:
                                h1 = h1[:-1]
                    else:
                        # Non-MT5 (Binance/EODHD): check whether the last bar
                        # is likely still forming before blindly chopping.  If
                        # the bar open is in the future (i.e. still forming)
                        # drop it; otherwise the bar is confirmed — keep it.
                        _now_ts = datetime.now(timezone.utc)

                        def _should_drop_last(bars):
                            """Return True when the last bar appears to be forming."""
                            if not bars or len(bars) <= 1:
                                return False
                            _last_t = bars[-1].get("time") or bars[-1].get("datetime")
                            if not _last_t:
                                return True  # no timestamp → conservative chop
                            try:
                                _ts_str = str(_last_t).replace("Z", "+00:00")
                                _bar_dt = datetime.fromisoformat(_ts_str)
                                if _bar_dt.tzinfo is None:
                                    _bar_dt = _bar_dt.replace(tzinfo=timezone.utc)
                                return _bar_dt > _now_ts
                            except Exception:
                                return True

                        if _should_drop_last(d1):
                            d1 = d1[:-1]
                        if _should_drop_last(h4):
                            h4 = h4[:-1]
                        if _should_drop_last(h1):
                            h1 = h1[:-1]

                    if pair.get("source") != "mt5":
                        from athena_app.services.market_state import (
                            market_state_offset_hours,
                            split_market_state,
                        )

                        for _tf in ("D1", "H4", "H1"):
                            _st = split_market_state(
                                list(raw_candles.get(_tf) or []),
                                _tf,
                                pair.get("display") or pair.get("symbol") or "",
                                offset_hours=market_state_offset_hours(pair, _tf),
                            )
                            _confirmed = list(_st.get("confirmed") or [])
                            if _tf == "D1":
                                d1 = _confirmed
                            elif _tf == "H4":
                                h4 = _confirmed
                            else:
                                h1 = _confirmed

                    sig_a["engine_b_evaluated"] = True
                    _tf_map_b = {"D1": d1 or [], "H4": h4 or [], "H1": h1 or []}
                    _zone_tf_b = str(style_profile_b.get("zone_tf", "H4")).upper()
                    _entry_tf_b = str(style_profile_b.get("entry_tf", "H1")).upper()
                    _atr_tf_b = str(style_profile_b.get("atr_tf", "H4")).upper()
                    zone_candles_b = _select_engine_b_tf_candles(_zone_tf_b, _tf_map_b)
                    entry_candles_b = _select_engine_b_tf_candles(_entry_tf_b, _tf_map_b)
                    atr_candles_b = _select_engine_b_tf_candles(_atr_tf_b, _tf_map_b)

                    _eb_funnel_extras["candles_tf_ok"] = bool(
                        zone_candles_b and entry_candles_b and atr_candles_b
                    )

                    if zone_candles_b and entry_candles_b and atr_candles_b:
                        if engine_b_forex_asian_session_blocks_bar(
                            entry_candles_b, ptype
                        ):
                            _eb_funnel_extras["engine_b_skip_stage"] = (
                                "forex_asian_session_block"
                            )
                            _attach_engine_b_scan_gate_funnel(
                                sig=sig_a,
                                pair=pair,
                                score_group=_pair_score_group,
                                resolved_style=resolved_style_b,
                                style_profile_b=style_profile_b,
                                conf_b=_eb_snap["conf"],
                                res_b=_eb_snap["res"],
                                extras=dict(_eb_funnel_extras),
                            )
                            return pair, sig_a, None
                        atr_signal = float(_last_atr_from_candles(atr_candles_b, 14))
                        atr = atr_signal
                        _eb_funnel_extras["atr_value"] = atr
                        _eb_funnel_extras["atr_source"] = "candle_atr_tf"
                        _eb_funnel_extras["bybit_atr_available"] = None
                        if (
                            ptype == "crypto"
                            and str(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
                            and hasattr(r, "bybit_atr_for_levels")
                        ):
                            bybit_atr = r.bybit_atr_for_levels(pair, resolved_style_b)
                            _eb_funnel_extras["bybit_atr_available"] = bool(bybit_atr)
                            if bybit_atr:
                                atr = float(bybit_atr)
                                _eb_funnel_extras["atr_value"] = atr
                                _eb_funnel_extras["atr_source"] = "bybit_levels"
                            elif not bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                                atr = 0.0
                                sig_a["engine_b_error"] = "bybit_atr_unavailable"
                        current_price = float(entry_candles_b[-1]["close"])
                        _eb_funnel_extras["entry_price"] = current_price

                        if atr and atr > 0:
                            regime_label = r.engine_b_regime_label(zone_candles_b, ptype)
                            _eb_funnel_extras["regime_label"] = regime_label

                            # Snapshots are needed for both A-driven and Engine
                            # B-only paths.
                            _sc_d1_snap = {}
                            _sc_h4_snap = {}
                            try:
                                _sc_d1_snap = (
                                    calc_indicators_with_normalized(d1 or [], ptype) or {}
                                ).get("snap") or {}
                                _sc_h4_snap = (
                                    calc_indicators_with_normalized(zone_candles_b, ptype) or {}
                                ).get("snap") or {}
                            except Exception:
                                pass

                            # Engine B-only path: when no Engine A direction
                            # exists, probe both directions independently and
                            # pick the best. This makes Engine B output
                            # independent of Engine A.
                            _b_only_probe_res = None
                            _b_only_probe_conf = None
                            if engine_b_scan_only:
                                _b_dir, _b_only_probe_res, _b_only_probe_conf = (
                                    _engine_b_independent_direction_probe(
                                        pair,
                                        engine=_engine_b,
                                        d1_candles=d1 or [],
                                        zone_candles=zone_candles_b,
                                        entry_candles=entry_candles_b,
                                        current_price=current_price,
                                        atr=atr,
                                        regime_label=regime_label,
                                        style_profile=style_profile_b,
                                        resolved_style=resolved_style_b,
                                        asset_type=ptype,
                                        d1_snap=_sc_d1_snap,
                                        h4_snap=_sc_h4_snap,
                                    )
                                )
                                if _b_dir in ("LONG", "SHORT"):
                                    sig_a["direction"] = _b_dir
                                else:
                                    _eb_funnel_extras.setdefault(
                                        "engine_b_skip_stage",
                                        "no_clear_structural_verdict",
                                    )
                            direction = sig_a.get("direction")

                            if direction in ("LONG", "SHORT"):
                                if _b_only_probe_res is not None:
                                    # Reuse probe result so we don't re-run
                                    # analyze_structure for the picked direction.
                                    res_b = _b_only_probe_res
                                else:
                                    res_b = _engine_b.set_registry_context(
                                        pair.get("symbol") or pair.get("display")
                                    ).analyze_structure(
                                        d1 or [],
                                        zone_candles_b,
                                        entry_candles_b,
                                        current_price,
                                        direction,
                                        atr,
                                        regime_label,
                                        fallback_rr=style_profile_b.get("fallback_rr", 2.0),
                                        asset_type=ptype,
                                        d1_snap=_sc_d1_snap,
                                        h4_snap=_sc_h4_snap,
                                        style=resolved_style_b,
                                        pair=pair,
                                    )
                                _eb_funnel_extras["structure_executed"] = True

                                conf_b = None
                                if res_b.get("structural_verdict") == "CLEAR":
                                    if _b_only_probe_conf is not None:
                                        conf_b = _b_only_probe_conf
                                    else:
                                        conf_b = _engine_b.calculate_confidence(
                                            res_b,
                                            current_price,
                                            direction,
                                            entry_candles=entry_candles_b or zone_candles_b,
                                            style_profile=style_profile_b,
                                        )
                                    _engine_b_direction_used = direction
                                    # Scan-only B independence (legacy A-driven
                                    # path): if Engine A's direction makes the
                                    # naked-structure gate fail, optionally
                                    # re-check the direction inferred from
                                    # Engine B's own BOS/CHoCH/sweep evidence.
                                    # Skipped for engine_b_scan_only — the
                                    # probe above already evaluated both
                                    # directions and picked the best.
                                    if (
                                        not engine_b_scan_only
                                        and bool(CONFIG.get("ENGINE_B_SCAN_INDEPENDENT_DIRECTION_ENABLED", False))
                                    ):
                                        _initial_gate_ok, _ = engine_b_confidence_passes(
                                            conf_b,
                                            style_profile_b,
                                            regime_label,
                                            ptype,
                                        )
                                        _independent = res_b.get("engine_b_independent_direction") or {}
                                        _alt_direction = _independent.get("direction")
                                        if (
                                            not _initial_gate_ok
                                            and _alt_direction in ("LONG", "SHORT")
                                            and _alt_direction != direction
                                        ):
                                            alt_res_b = _engine_b.set_registry_context(
                                                pair.get("symbol") or pair.get("display")
                                            ).analyze_structure(
                                                d1 or [],
                                                zone_candles_b,
                                                entry_candles_b,
                                                current_price,
                                                _alt_direction,
                                                atr,
                                                regime_label,
                                                fallback_rr=style_profile_b.get("fallback_rr", 2.0),
                                                asset_type=ptype,
                                                d1_snap=_sc_d1_snap,
                                                h4_snap=_sc_h4_snap,
                                                style=resolved_style_b,
                                                pair=pair,
                                            )
                                            if alt_res_b.get("structural_verdict") == "CLEAR":
                                                alt_conf_b = _engine_b.calculate_confidence(
                                                    alt_res_b,
                                                    current_price,
                                                    _alt_direction,
                                                    entry_candles=entry_candles_b or zone_candles_b,
                                                    style_profile=style_profile_b,
                                                )
                                                alt_gate_ok, _ = engine_b_confidence_passes(
                                                    alt_conf_b,
                                                    style_profile_b,
                                                    regime_label,
                                                    ptype,
                                                )
                                                if alt_gate_ok:
                                                    res_b = alt_res_b
                                                    conf_b = alt_conf_b
                                                    _engine_b_direction_used = _alt_direction
                                                    sig_a["engine_b_independent_direction_scan_applied"] = True
                                                    sig_a["engine_b_original_direction"] = direction
                                    b_score = float(conf_b.get("score", 0))
                                    b_max = float(conf_b.get("max_possible", 5))

                                    sig_a["engine_b_score"] = round(b_score, 2)
                                    sig_a["engine_b_max"] = round(b_max, 1)
                                    sig_a["engine_b_pct"] = round(b_score / b_max * 100, 1) if b_max else 0
                                    sig_a["engine_b_direction"] = _engine_b_direction_used
                                    sig_a["engine_b_verdict"] = res_b.get("structural_verdict")
                                    sig_a["engine_b_bos"] = res_b.get("bos_confirmed", False)
                                    sig_a["engine_b_ob"] = res_b.get("ob_at_zone", False)
                                    sig_a["engine_b_sl"] = res_b.get("recommended_stop_loss")
                                    sig_a["engine_b_tp"] = res_b.get("recommended_take_profit")
                                    sig_a["engine_b_lifecycle_state"] = conf_b.get("lifecycle_state", "unknown")
                                    sig_a["engine_b_lifecycle_reason"] = conf_b.get("lifecycle_reason", "")
                                    _apply_engine_b_scan_levels(sig_a, conf_b, res_b)

                                    a_max = sig_a.get("maxScore", 3.0)
                                    a_score = sig_a.get("confluenceScore", 0)
                                    a_norm = float(sig_a.get("scoreNorm", 0))
                                    b_norm = min(b_score / b_max, 1.0) if b_max else 0

                                    # Use same regime-conditional weights as engine_c.
                                    _rl = (regime_label or "").upper()
                                    _w = ENGINE_C_AB_WEIGHTS.get(_rl, ENGINE_C_AB_WEIGHTS.get("TRENDING", {"A": 0.40, "B": 0.60}))
                                    _w_a = float(_w.get("A", 0.40))
                                    _w_b = float(_w.get("B", 0.60))

                                    _engine_b_direction_aligned = (
                                        _engine_b_direction_used == direction
                                    )
                                    combined_conviction = _engine_b_scan_combined_conviction(
                                        a_norm,
                                        b_norm,
                                        _w,
                                        direction_aligned=_engine_b_direction_aligned,
                                    )
                                    sig_a["combinedConviction"] = combined_conviction
                                    sig_a["engine_b_scoreNorm"] = round(b_norm, 4)
                                    _apply_engine_b_scan_confidence_gate(
                                        sig_a,
                                        conf_b,
                                        style_profile_b,
                                        regime_label,
                                        ptype,
                                    )
                                    sig_a["engine_b_confidence_passed"] = bool(conf_b.get("passed", False))
                                    sig_a["engine_b_direction_aligned_with_a"] = _engine_b_direction_aligned
                                    if not _engine_b_direction_aligned:
                                        sig_a["enginesAligned"] = False
                                    _mark_engine_b_structure_ready_watchlist(
                                        sig_a,
                                        conf_b,
                                        res_b,
                                    )

                                    log.debug(
                                        f"[SCAN+B] {pair.get('display')} A={a_score:.2f}/{a_max} B={b_score:.2f}/{b_max} "
                                        f"regime={_rl} wA={_w_a} wB={_w_b} combined={combined_conviction:.3f}"
                                    )
                                else:
                                    a_max = sig_a.get("maxScore", 3.0)
                                    a_score = sig_a.get("confluenceScore", 0)
                                    a_norm = float(sig_a.get("scoreNorm", 0))
                                    # A-only fallback: do not cap with Engine C A/B blend weights.
                                    _w_a_fb = _a_only_auto_weight(pair)
                                    sig_a["combinedConviction"] = round(a_norm * _w_a_fb, 4)
                                    sig_a["enginesAligned"] = False
                                    sig_a["engine_b_verdict"] = res_b.get("structural_verdict", "UNCLEAR")
                                _eb_snap["res"] = res_b
                                _eb_snap["conf"] = conf_b
                                sig_a["engine_b_status"] = conf_b
                                sig_a["engine_b"] = res_b

                                if _threshold_audit_on:
                                    sig_a["_threshold_audit_b_res"] = res_b
                                    sig_a["_threshold_audit_b_conf"] = conf_b
                                    sig_a["_threshold_audit_b_threshold"] = engine_b_min_score_threshold(
                                        style_profile_b,
                                        regime_label,
                                        ptype,
                                    )
                                    sig_a["_threshold_audit_b_style_profile"] = style_profile_b

                            else:
                                _eb_funnel_extras.setdefault(
                                    "engine_b_skip_stage",
                                    "engine_a_direction_not_traded",
                                )
                        elif _eb_funnel_extras["candles_tf_ok"]:
                            _eb_funnel_extras.setdefault(
                                "engine_b_skip_stage",
                                "crypto_bybit_atr_unavailable"
                                if sig_a.get("engine_b_error") == "bybit_atr_unavailable"
                                else "atr_blocked_zero_or_invalid",
                            )
                    else:
                        _eb_funnel_extras.setdefault(
                            "engine_b_skip_stage",
                            "missing_engine_b_tf_candles",
                        )

                    _attach_engine_b_scan_gate_funnel(
                        sig=sig_a,
                        pair=pair,
                        score_group=_pair_score_group,
                        resolved_style=resolved_style_b,
                        style_profile_b=style_profile_b,
                        conf_b=_eb_snap["conf"],
                        res_b=_eb_snap["res"],
                        extras=dict(_eb_funnel_extras),
                    )

                except Exception as _b_err:
                    log.debug(f"[SCAN+B] {pair.get('display')} Engine B failed: {_b_err}")
                    a_max = sig_a.get("maxScore", 3.0)
                    a_score = sig_a.get("confluenceScore", 0)
                    a_norm = float(sig_a.get("scoreNorm", 0))
                    sig_a["combinedConviction"] = round(a_norm * _a_only_auto_weight(pair), 4)
                    sig_a["enginesAligned"] = False
                    sig_a["engine_b_error"] = str(_b_err)
                    if _threshold_audit_on:
                        sig_a["_threshold_audit_b_error"] = str(_b_err)
                    try:
                        _eb_funnel_extras.setdefault(
                            "engine_b_skip_stage", "engine_b_overlay_exception"
                        )
                        _sg = locals().get("_pair_score_group")
                        if _sg is None:
                            _sg = get_pair_score_group(pair)
                        _attach_engine_b_scan_gate_funnel(
                            sig=sig_a,
                            pair=pair,
                            score_group=_sg,
                            resolved_style=locals().get("resolved_style_b"),
                            style_profile_b=locals().get("style_profile_b"),
                            conf_b=_eb_snap["conf"],
                            res_b=_eb_snap["res"],
                            extras=dict(_eb_funnel_extras),
                        )
                    except Exception:
                        log.debug(
                            "[SCAN+B] %s engine_b_scan_gate_funnel attach failed",
                            pair.get("display", "?"),
                            exc_info=True,
                        )

                return pair, sig_a, None

            except Exception as e:
                return pair, None, str(e)

        buffered_ok: list[tuple[Any, dict]] = []

        with ThreadPoolExecutor(max_workers=_max_workers) as pool:
            futures = {pool.submit(_analyse, pair): pair for pair in candidate_pairs}

            for fut in as_completed(futures):
                pair = futures[fut]
                try:
                    pair_result, sig, err = fut.result(timeout=60)
                except TimeoutError:
                    errors.append({"pair": pair["display"], "error": "Scan timeout (60s)"})
                    scan_funnel["errors"] += 1
                    log.error(f"{pair['display']:12s} ERR: Scan timeout (60s)")
                    continue

                if err:
                    errors.append({"pair": pair["display"], "error": err})

                    scan_funnel["errors"] += 1

                    log.error(f"{pair['display']:12s} ERR: {err}")

                    continue

                if isinstance(sig, dict) and sig.get("skipCode") == "rate_limited":
                    if _threshold_audit_on:
                        threshold_audit_rows.append(
                            build_signal_funnel_row(
                                pair,
                                None,
                                tier="skip",
                                skipped_reason=sig.get("skipDetail", "Rate limited"),
                            )
                        )
                    skipped.append(
                        {
                            "pair": pair["display"],
                            "reason": sig.get("skipReason", "Rate limited"),
                            "tier": "skip",
                            "diagnostics": [
                                {
                                    "code": sig.get("skipCode", "rate_limited"),
                                    "detail": sig.get("skipDetail", "Rate limited"),
                                }
                            ],
                        }
                    )

                    scan_funnel["no_data"] += 1

                    log.info(f"{pair['display']:12s} SKIP: rate limited")

                    continue

                if not sig:
                    if _threshold_audit_on:
                        threshold_audit_rows.append(
                            build_signal_funnel_row(
                                pair,
                                None,
                                tier="skip",
                                skipped_reason="No data",
                            )
                        )
                    skipped.append(
                        {
                            "pair": pair["display"],
                            "reason": "No data",
                            "tier": "skip",
                            "diagnostics": [{"code": "no_data", "detail": "No data"}],
                        }
                    )

                    scan_funnel["no_data"] += 1

                    log.info(f"{pair['display']:12s} SKIP")

                    continue

                buffered_ok.append((pair, sig))

        # Cross-sectional quantile floors per asset class (this scan only).
        # Engine B-only rows must not enter the Engine A quantile cohort —
        # their confluenceScore is intentionally 0 and would skew the floor.
        scores_by_type: dict[str, list[float]] = {}
        for pair, sig in buffered_ok:
            if sig.get("engine_source") == ENGINE_B_SOURCE:
                continue
            ptype = pair.get("type") or "stock"
            scores_by_type.setdefault(ptype, []).append(float(sig.get("confluenceScore", 0)))

        _q_enabled = bool(CONFIG.get("SCAN_QUANTILE_ENABLED", False))
        _q_min_n = int(CONFIG.get("SCAN_QUANTILE_MIN_SAMPLES", 5))
        _q_frac = CONFIG.get("SCAN_QUANTILE_TOP_FRACTION") or {}
        if not isinstance(_q_frac, dict):
            _q_frac = {}

        quantile_floors = compute_scan_quantile_floors(
            scores_by_type,
            enabled=_q_enabled,
            min_samples=_q_min_n,
            top_fraction_cfg=_q_frac,
        )

        _q_excl = CONFIG.get("SCAN_QUANTILE_EXCLUDE_TYPES") or []
        if not isinstance(_q_excl, (list, tuple, set)):
            _q_excl = []
        _q_excl_set = {str(x).strip().lower() for x in _q_excl if x is not None}
        for _pt in _q_excl_set:
            if _pt in quantile_floors:
                quantile_floors[_pt] = None

        if _q_enabled and any(v is not None for v in quantile_floors.values()):
            log.info(f"[SCAN-Q] Per-type quantile floors: {quantile_floors}")

        for pair, sig in buffered_ok:
            # Engine B-only rows take a separate classification path. They
            # must never use Engine A's threshold, quantile floor, or
            # combinedConviction — those are Engine A semantics. Engine B
            # rows are classified by Engine B gates only and capped at
            # tier="watchlist" (never auto-trade).
            if sig.get("engine_source") == ENGINE_B_SOURCE:
                sig = _annotate_engine_b_only_signal_for_scan(
                    sig,
                    pair,
                    ds_ctx,
                    earnings_ctx,
                    _closed_exchanges,
                    news_ctx,
                )
                tier, tier_reason = _classify_engine_b_only_signal(sig, pair)
                if _threshold_audit_on:
                    threshold_audit_rows.append(
                        build_signal_funnel_row(
                            pair,
                            sig,
                            tier=tier,
                            tier_reason=tier_reason,
                            style_profile_b=sig.get("_threshold_audit_b_style_profile"),
                            engine_b_threshold=sig.get("_threshold_audit_b_threshold"),
                        )
                    )
                sig["signalTier"] = tier
                sig["signalTierReason"] = tier_reason
                sig["watchlistReason"] = tier_reason if tier == "watchlist" else None
                _patch_engine_b_funnel_final_tier(sig, tier, tier_reason)
                codes = {d.get("code") for d in sig.get("scanDiagnostics", [])}
                if "closed_exchange" in codes:
                    scan_funnel["closed_exchange"] += 1
                if "event_risk" in codes:
                    scan_funnel["event_block"] += 1
                if "inactive_pair" in codes:
                    scan_funnel["inactive_pair"] += 1
                if tier == "watchlist":
                    watchlist.append(sig)
                    scan_funnel["watchlist"] += 1
                    log.info(
                        f"{pair['display']:12s} WATCH [B-only] :: {tier_reason}"
                    )
                else:
                    _skip_payload: dict[str, Any] = {
                        "pair": pair["display"],
                        "reason": tier_reason,
                        "tier": "skip",
                        "engine_source": ENGINE_B_SOURCE,
                        "diagnostics": sig.get("scanDiagnostics", []),
                    }
                    _eb_fd = sig.get("engine_b_scan_gate_funnel")
                    if isinstance(_eb_fd, dict):
                        _skip_payload["engine_b_scan_gate_funnel"] = dict(_eb_fd)
                    skipped.append(_skip_payload)
                    log.info(f"{pair['display']:12s} SKIP  [B-only] :: {tier_reason}")
                continue

            # Unify threshold resolution — ensures live scan and backtest parity (BUG 7)
            # Pass regime for dynamic threshold adjustment when enabled
            regime = sig.get("regime")
            static_threshold = get_score_threshold(pair, regime=regime)

            if r.test_mode():
                static_threshold = max(0.1, static_threshold * 0.5)

            q_cut = quantile_floors.get(pair.get("type") or "stock")
            effective_threshold = static_threshold
            if q_cut is not None:
                effective_threshold = max(static_threshold, float(q_cut))

            sig["scanThresholdStatic"] = static_threshold
            sig["scanQuantileCut"] = q_cut
            sig["scanThresholdEffective"] = effective_threshold

            if (
                q_cut is not None
                and effective_threshold > static_threshold
                and sig.get("confluenceScore", 0) >= static_threshold
                and sig.get("confluenceScore", 0) < effective_threshold
            ):
                sig.setdefault("warnings", []).append(
                    f"SCAN QUANTILE: cross-section floor {effective_threshold:.3f} "
                    f"(static {static_threshold:.3f}, top-fraction cut {q_cut:.3f})"
                )

            sig = annotate_signal_for_scan(
                sig,
                pair,
                effective_threshold,
                ds_ctx,
                earnings_ctx,
                _closed_exchanges,
                news_ctx,
            )

            # Backend separated UI metrics: explicit progress vs absolute score capacity
            _cs = float(sig.get("confluenceScore", 0))
            if effective_threshold and effective_threshold > 0:
                sig["thresholdProgressPct"] = min(100, max(0, round((_cs / effective_threshold) * 67)))
                sig["confluencePct"] = sig["thresholdProgressPct"] # backward compat

            _maxs = float(sig.get("maxScore", 2.0))
            if _maxs > 0:
                sig["scoreNormPct"] = min(100, max(0, round((_cs / _maxs) * 100)))

            tier, tier_reason = _classify_signal(sig, pair)
            tier, tier_reason = _apply_engine_b_scan_gate(sig, tier, tier_reason)
            tier, tier_reason = _apply_engine_b_only_watchlist_scan_tier(
                sig,
                tier,
                tier_reason,
            )
            tier, tier_reason = _apply_engine_b_structure_ready_scan_tier(
                sig,
                tier,
                tier_reason,
            )
            if _threshold_audit_on:
                threshold_audit_rows.append(
                    build_signal_funnel_row(
                        pair,
                        sig,
                        tier=tier,
                        tier_reason=tier_reason,
                        style_profile_b=sig.get("_threshold_audit_b_style_profile"),
                        engine_b_threshold=sig.get("_threshold_audit_b_threshold"),
                    )
                )

            sig["signalTier"] = tier
            sig["signalTierReason"] = tier_reason

            sig["watchlistReason"] = tier_reason if tier == "watchlist" else None

            _patch_engine_b_funnel_final_tier(sig, tier, tier_reason)

            codes = {d.get("code") for d in sig.get("scanDiagnostics", [])}

            if "low_confluence" in codes:
                scan_funnel["low_score"] += 1

            if "closed_exchange" in codes:
                scan_funnel["closed_exchange"] += 1

            if "event_risk" in codes:
                scan_funnel["event_block"] += 1

            if "inactive_pair" in codes:
                scan_funnel["inactive_pair"] += 1

            if "counter_trend" in codes:
                scan_funnel["counter_trend"] += 1

            if "dead_ranging" in codes:
                scan_funnel["dead_ranging"] += 1

            if tier == "trade":
                try:
                    sig["serverIndicators"] = r.fetch_eodhd_indicators(pair)

                except Exception as _e:
                    log.debug(
                        f"[IND] {pair['display']} server indicators skipped: {_e}"
                    )

                results.append(sig)

                scan_funnel["passed"] += 1

                log.info(
                    f"{pair['display']:12s} {sig['direction']:5s} {sig['confluenceScore']}/{sig.get('maxScore', 3)} [{sig.get('trendState', '?')}]"
                )

            elif tier == "watchlist":
                watchlist.append(sig)

                scan_funnel["watchlist"] += 1

                log.info(
                    f"{pair['display']:12s} WATCH {sig['confluenceScore']}/{sig.get('maxScore', 3)} :: {tier_reason}"
                )

            else:
                _skip_payload: dict[str, Any] = {
                    "pair": pair["display"],
                    "reason": tier_reason,
                    "tier": "skip",
                    "diagnostics": sig.get("scanDiagnostics", []),
                }
                _eb_fd = sig.get("engine_b_scan_gate_funnel")
                if isinstance(_eb_fd, dict):
                    _skip_payload["engine_b_scan_gate_funnel"] = dict(_eb_fd)
                skipped.append(_skip_payload)

                log.info(
                    f"{pair['display']:12s} SKIP  {sig['confluenceScore']}/{sig.get('maxScore', 3)} :: {tier_reason}"
                )

        log.info(f"Scan funnel: {scan_funnel}")
        
        # REGRESSION CHECK: Print Engine A scan-funnel summary
        print("\n" + "="*80)
        print("REGRESSION CHECK - ENGINE A SCAN FUNNEL")
        print("="*80)
        print(f"Total pairs scanned: {scan_funnel['total']}")
        print(f"Active pairs: {scan_funnel['active']}")
        print(f"No data: {scan_funnel['no_data']}")
        print(f"Low score: {scan_funnel['low_score']}")
        print(f"Passed: {scan_funnel['passed']}")
        print(f"Watchlist: {scan_funnel['watchlist']}")
        print(f"Errors: {scan_funnel['errors']}")
        print(f"Closed exchange: {scan_funnel['closed_exchange']}")
        print(f"Event block: {scan_funnel['event_block']}")
        print(f"Inactive pair: {scan_funnel['inactive_pair']}")
        print(f"Counter trend: {scan_funnel['counter_trend']}")
        print(f"Dead ranging: {scan_funnel['dead_ranging']}")
        print("="*80 + "\n")
        
        if _threshold_audit_on:
            try:
                write_signal_funnel_rows(threshold_audit_rows)
                log.info("[THRESHOLD-AUDIT] wrote %d signal funnel rows", len(threshold_audit_rows))
            except Exception as _audit_err:
                log.warning("[THRESHOLD-AUDIT] write failed: %s", _audit_err)

        results.sort(
            key=lambda x: x.get(
                "combinedConviction", x.get("confluenceScore", 0) / x.get("maxScore", 3.0)
            ),
            reverse=True,
        )

        watchlist.sort(
            key=lambda x: x.get(
                "combinedConviction", x.get("confluenceScore", 0) / x.get("maxScore", 3.0)
            ),
            reverse=True,
        )

        results = apply_correlation_cap(results)

        watchlist = apply_correlation_cap(watchlist)

        _scan_out: dict[str, Any] = {
            "success": True,
            "signals": results,
            "tradeSignals": results,
            "watchlist": watchlist,
            "errors": errors,
            "skipped": skipped,
            "scanFunnel": scan_funnel,
            "btcBias": btc_bias,
            "totalPairs": len(candidate_pairs),
            "activePairs": len(active_pairs),
            "scannedAt": datetime.now(timezone.utc).isoformat(),
            "styleRequested": _requested_style,
            "style": _requested_style,
            "testMode": r.test_mode(),
            "scanMaxWorkersUsed": _max_workers,
            "scanQuantileEnabled": _q_enabled,
            "scanQuantileFloors": quantile_floors,
            "scanQuantileMinSamples": _q_min_n,
            "payloadVersion": "2.0",
            "contract": {
                "engineA": "v2_factor_scoring",
                "engineB": "naked_structure",
                "engineC": "consensus",
                "engineD": "scalp_vp",
                # NOTE: Engine D is NOT auto-executed. It is available via
                # manual/API endpoints (/api/scalp-scan, /api/scalp-execute).
            },
        }
        try:
            from athena_app.diagnostics.engine_b_gate_funnel_persist import (
                maybe_persist_engine_b_scan_gate_funnel,
            )

            _pair_type_lookup: dict[str, str] = {}
            for _p in candidate_pairs:
                _disp = str(_p.get("display") or _p.get("symbol") or "").strip()
                if not _disp:
                    continue
                _pair_type_lookup[_disp] = str(_p.get("type") or "").strip().lower()
            _scan_out.update(
                maybe_persist_engine_b_scan_gate_funnel(
                    _scan_out,
                    pair_types_by_display=_pair_type_lookup,
                )
            )
        except Exception as _persist_merge_err:
            log.warning(
                "[ENGINE_B_FUNNEL_PERSIST] merge failed (non-fatal): %s",
                _persist_merge_err,
            )
        try:
            from athena_app.diagnostics.engine_b_gate_funnel_persist import (
                write_engine_b_funnel_scan_touch_file,
            )

            touch = write_engine_b_funnel_scan_touch_file(_scan_out)
            _scan_out["engine_b_scan_funnel_touch"] = touch

        except Exception as _touch_err:
            log.warning("[ENGINE_B_FUNNEL_TOUCH] %s", _touch_err)
        return _scan_out

    finally:
        r.scan_lock.release()


def classify_signal(signal: dict[str, Any], pair: dict[str, Any]) -> tuple[str, str]:
    return _classify_signal(signal, pair)


def analyze_pair(
    pair: dict[str, Any],
    btc_bias: str,
    style: str = "swing",
    use_naked_engine: bool = False,
    regime_context: dict[str, Any] | None = None,
    preloaded_candles: dict[str, Any] | None = None,
    preloaded_fetch_meta: dict[str, Any] | None = None,
    intermarket_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Single-pair analysis; implementation remains on the monolith until further split."""
    try:
        return rt().analyze_pair(
            pair,
            btc_bias,
            style=style,
            use_naked_engine=use_naked_engine,
            regime_context=regime_context,
            preloaded_candles=preloaded_candles,
            preloaded_fetch_meta=preloaded_fetch_meta,
            intermarket_snapshot=intermarket_snapshot,
        )
    except RuntimeError:
        from athena_legacy import load as _load_legacy

        return _load_legacy().analyze_pair(
            pair,
            btc_bias,
            style=style,
            use_naked_engine=use_naked_engine,
            regime_context=regime_context,
            preloaded_candles=preloaded_candles,
            preloaded_fetch_meta=preloaded_fetch_meta,
            intermarket_snapshot=intermarket_snapshot,
        )
