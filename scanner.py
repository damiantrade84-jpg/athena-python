"""Full-scan orchestration and scan-time signal annotation."""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from athena_runtime import rt
from athena_app.services.crypto_signal_feed import (
    fetch_crypto_signal_candles,
    resolve_crypto_signal_feed,
)
from candles_cache import get_candle_fetch_meta
from config import CONFIG, scan_candle_limits, get_optimal_workers
from data_feeds import http_requests
from indicators import calc_atr, calc_indicators, calc_indicators_with_normalized
from intermarket import build_scan_snapshot
from scoring import (
    _build_event_risk,
    _classify_signal as _classify_legacy_signal,
    _pair_exchange_closed,
    apply_correlation_cap,
    engine_a_regime_label_for_threshold,
    get_score_threshold,
    get_pair_score_group,
)
from engine_c import ENGINE_C_AB_WEIGHTS
from athena_app.services.engine_b_direction import (
    annotate_signal_direction_metadata,
    independent_conflict_blocks_emit,
)
from engine_b_quality import engine_b_conviction_norm
from engine_b_subsystems import engine_b_direction_min_score_gap, engine_b_pick_directional_candidate
from market_structure import (
    NakedEngine,
    _reset_engine_b_gate_failures,
    engine_b_confidence_passes,
    engine_b_low_volatility_gate,
    engine_b_live_trigger_kwargs,
    engine_b_forex_asian_session_blocks_bar,
    engine_b_min_score_threshold,
)
from exit_mode_apply import apply_engine_b_exit_strategy
from threshold_audit import (
    audit_enabled as threshold_audit_enabled,
    build_signal_funnel_row,
    write_signal_funnel_rows,
)
from factor_scoring import make_regime_smoothing_context

log = logging.getLogger("sentinel")

_SPEED_STATE_BY_SYMBOL: dict[str, Any] = {}
_SPEED_STATE_LOCK = threading.Lock()


def _scan_speed_state(
    pair: dict[str, Any],
    market_states: dict[str, Any],
    live_prices: dict[str, Any] | None,
    *,
    current_session: str | None = None,
    scheduled_event: bool | None = None,
):
    """Build persistent speed state from confirmed bars and quote quality."""
    from athena_app.services.market_state import candle_timestamp_epoch
    from timeframe_policy import (
        baseline_liquidity_for_group,
        calculate_speed_state,
        canonical_symbol,
        resolve_timeframe_policy,
        thresholds_for_group,
    )
    from engine_a_groups import resolve_score_group_by_type

    requested_symbol = pair.get("display") or pair.get("symbol") or ""
    key = canonical_symbol(requested_symbol) or "".join(
        ch for ch in str(requested_symbol).upper() if ch.isalnum()
    )

    def _quote_key(value: Any) -> str:
        return "".join(ch for ch in str(value or "").upper() if ch.isalnum())

    wanted = {
        _quote_key(pair.get("display")),
        _quote_key(pair.get("symbol")),
        _quote_key(pair.get("pair")),
    }
    wanted.discard("")
    quote = next(
        (
            dict(value)
            for raw_key, value in (live_prices or {}).items()
            if _quote_key(raw_key) in wanted and isinstance(value, dict)
        ),
        {},
    )
    try:
        bid = float(quote.get("bid"))
        ask = float(quote.get("ask"))
        spread = ask - bid if ask >= bid and bid > 0 else None
    except (TypeError, ValueError):
        spread = None
    try:
        quote_ts_raw = quote.get("broker_ts")
        quote_source = str(quote.get("source") or "").strip().lower()
        if quote_ts_raw is None and quote_source != "mt5":
            quote_ts_raw = quote.get("ts")
        quote_ts = float(quote_ts_raw)
        if quote_ts > 1e12:
            quote_ts /= 1000.0
        quote_age = max(0.0, datetime.now(timezone.utc).timestamp() - quote_ts) if quote_ts > 0 else None
    except (TypeError, ValueError):
        quote_age = None

    h1 = list((market_states.get("H1") or {}).get("confirmed") or [])
    m15 = list((market_states.get("M15") or {}).get("confirmed") or [])
    last_h1_epoch = candle_timestamp_epoch(h1[-1]) if h1 else None
    gap_status = "stale" if any(
        bool((market_states.get(tf) or {}).get("stale")) for tf in ("H1", "M15")
    ) else "normal"
    score_group = resolve_score_group_by_type(pair)
    speed_thresholds, liquidity_thresholds = thresholds_for_group(
        CONFIG,
        score_group,
    )
    baseline_policy = resolve_timeframe_policy(
        requested_symbol,
        pair.get("type", ""),
        score_group,
        "intraday",
    )
    with _SPEED_STATE_LOCK:
        previous = _SPEED_STATE_BY_SYMBOL.get(key)
    state = calculate_speed_state(
        h1,
        m15,
        spread=spread,
        quote_age_sec=quote_age,
        current_session=current_session,
        previous=previous,
        last_closed_h1_open_time=int(last_h1_epoch) if last_h1_epoch else None,
        gap_status=gap_status,
        scheduled_event=scheduled_event,
        provider_market_state=quote.get("market_state"),
        baseline_speed_class=baseline_policy.baseline_speed_class,
        baseline_liquidity_class=baseline_liquidity_for_group(CONFIG, score_group),
        thresholds=speed_thresholds,
        liquidity_thresholds=liquidity_thresholds,
    )
    with _SPEED_STATE_LOCK:
        _SPEED_STATE_BY_SYMBOL[key] = state
    return state


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


def _engine_b_scan_freshness_stale_tfs(
    pair: dict,
    d1: list | None,
    h4: list | None,
    h1: list | None,
    *,
    config: dict | None = None,
    score_group: str | None = None,
    style: str | None = None,
    active_entry_tfs: dict[str, list] | None = None,
) -> tuple[list[str], dict[str, dict]]:
    """Return stale TF labels for Engine B scan when freshness gate is enabled."""
    cfg = config or CONFIG
    if not bool(cfg.get("ENGINE_B_SCAN_FRESHNESS_GATE", True)):
        return [], {}

    try:
        from athena_app.services.data_freshness import (
            d1_consumed_by_score_group,
            pre_scoring_allows_confirmed_only_stale_1,
            pre_scoring_allows_intraday_calendar_gap,
        )
        from athena_app.services.market_state import candle_freshness_diagnostic
    except Exception as exc:
        return ["FRESHNESS_DEPENDENCY_UNAVAILABLE"], {
            "FRESHNESS_DEPENDENCY_UNAVAILABLE": {
                "stalenessSeverity": "dependency_unavailable",
                "errorType": type(exc).__name__,
            }
        }

    stale_tfs: list[str] = []
    freshness_diag: dict[str, dict] = {}
    pair_type = pair.get("type", "")
    is_forex_stock = pair_type in (
        "forex",
        "stock",
        "index",
        "commodity",
        "etf",
        "etf_bond",
    )
    allow_confirmed_only_stale_1 = pre_scoring_allows_confirmed_only_stale_1(pair)
    # B3: skip the D1 freshness BLOCK when the (score_group, style) does not
    # consume D1 for trend/momentum scoring. The diagnostic is still recorded
    # for display. Mirrors Engine A's behaviour at athena.py:13351-13369.
    # Without this, energy_oil/commodity_other intraday were blocked on stale
    # D1 that doesn't affect their score (audit MED #9). Conservative default
    # True (require D1) when score_group/style are not supplied, preserving the
    # historic fail-closed D1 gate for callers that don't pass the new args.
    d1_required = d1_consumed_by_score_group(score_group, style) if score_group else True

    for tf, candles in (("D1", d1), ("H4", h4), ("H1", h1)):
        diag = candle_freshness_diagnostic(
            pair,
            tf,
            candles or [],
            source=pair.get("source"),
        )
        freshness_diag[tf] = diag
        sev = diag.get("stalenessSeverity", "")
        if not sev or sev == "fresh":
            continue

        # B3: D1 only blocks pre-scoring when D1 feeds this group's trend/momentum
        # scoring. The diagnostic stays in freshness_diag for display.
        if tf == "D1" and not d1_required:
            continue

        if allow_confirmed_only_stale_1 and sev == "stale_1_bucket":
            continue

        if pre_scoring_allows_intraday_calendar_gap(pair, tf, diag):
            continue

        if sev in ("d1_calendar_gap_policy_ok", "intraday_calendar_gap_policy_ok"):
            continue

        if is_forex_stock and tf == "D1" and sev == "stale_multi_bucket":
            import datetime as _dt_mod

            utc_weekday = _dt_mod.datetime.now(_dt_mod.timezone.utc).weekday()
            bucket_lag = int(diag.get("bucketLag") or 99)
            if utc_weekday in (0, 5, 6) and bucket_lag <= 4:
                continue

        if is_forex_stock and tf == "D1" and sev == "d1_calendar_gap_policy_ok":
            import datetime as _dt_mod

            utc_weekday = _dt_mod.datetime.now(_dt_mod.timezone.utc).weekday()
            if utc_weekday in (0, 5, 6):
                continue

        stale_tfs.append(f"{tf}:{sev}")

    # Lower entry/trigger series intentionally includes the active bar. Unlike
    # confirmed structure, stale_1_bucket is not acceptable here for real-time
    # feeds: a missing current M15/M30 bucket would score the previous close as
    # the entry.
    #
    # MT5 equity CFDs (stock/etf/etf_bond) are the deliberate exception. Their
    # intraday bars legitimately lag ~1 bucket on the broker feed, so under an
    # M15 trigger the whole US equity/ETF universe was hard-blocked every scan
    # (STALE_DATA_ENGINE_B:M15:stale_1_bucket). The executable price for these
    # groups is guarded SEPARATELY by the live-quote gate (LIVE_PRICE_MAX_AGE_SEC
    # stock/etf = 90s), so a one-bucket-stale signal bar must not determine entry
    # here. Only stale_1_bucket is tolerated; stale_multi_bucket / missing still
    # fail closed. Config-reversible via ENGINE_B_ENTRY_TF_ALLOW_MT5_EQUITY_STALE_1.
    _entry_tf_allow_mt5_equity_stale_1 = bool(
        cfg.get("ENGINE_B_ENTRY_TF_ALLOW_MT5_EQUITY_STALE_1", True)
    )
    _is_mt5_equity = (
        str(pair.get("source") or "").strip().lower() == "mt5"
        and pair_type in ("stock", "etf", "etf_bond")
    )
    for tf, candles in (active_entry_tfs or {}).items():
        tf_u = str(tf or "").upper()
        diag = candle_freshness_diagnostic(
            pair,
            tf_u,
            candles or [],
            source=pair.get("source"),
        )
        freshness_diag[tf_u] = diag
        sev = str(diag.get("stalenessSeverity") or "missing")
        if sev == "fresh":
            continue
        if (
            _entry_tf_allow_mt5_equity_stale_1
            and _is_mt5_equity
            and sev == "stale_1_bucket"
        ):
            continue
        stale_tfs.append(f"{tf_u}:{sev}")

    if pair.get("type") == "crypto" and d1 and d1_required:
        try:
            from scalp_engine import _coerce_utc_datetime, _current_utc_datetime

            d1_fresh_diag = freshness_diag.get("D1") or {}
            d1_bucket_lag = int(d1_fresh_diag.get("bucketLag") or 0)
            d1_sev = str(d1_fresh_diag.get("stalenessSeverity") or "")
            genuine_multi_bucket = (
                d1_bucket_lag >= 2
                or d1_sev in ("stale_multi_bucket", "missing_current_bucket")
            )
            last_candle = d1[-1] if d1 else None
            if last_candle and genuine_multi_bucket:
                last_time = last_candle.get("time")
                if last_time:
                    last_ts = _coerce_utc_datetime(last_time)
                    if last_ts:
                        now = _current_utc_datetime()
                        age_hours = (now - last_ts).total_seconds() / 3600
                        max_hours = cfg.get("CRYPTO_D1_MAX_STALE_HOURS", 25)
                        if age_hours > max_hours:
                            stale_tfs.append(f"D1:crypto_stale_{int(age_hours)}h")
        except Exception:
            pass

    return stale_tfs, freshness_diag


def _attach_engine_a_execution_freshness(
    signal: dict[str, Any],
    pair: dict[str, Any],
    *,
    preloaded_market_state: dict[str, Any],
    raw_candles: dict[str, list],
    config: dict[str, Any] | None = None,
    time_now: float | None = None,
) -> dict[str, Any]:
    """Attach policy-aware execution freshness to an Engine A scan signal."""
    from athena_app.services.data_freshness import (
        check_live_candle_consistency,
        evaluate_execution_data_freshness,
    )
    from athena_app.services.market_state import candle_freshness_diagnostic

    cfg = config or CONFIG
    now = time_now if time_now is not None else datetime.now(timezone.utc).timestamp()

    def _scan_state(tf_key: str) -> dict[str, Any]:
        state = preloaded_market_state.get(tf_key)
        if isinstance(state, dict):
            return state
        return {"confirmed": list(raw_candles.get(tf_key) or []), "forming": None}

    def _scan_series_for_diag(tf_key: str) -> list:
        state = _scan_state(tf_key)
        confirmed = list(state.get("confirmed") or [])
        forming = state.get("forming")
        return confirmed + ([forming] if forming else [])

    diagnostic_tfs = ["D1", "H4", "H1"]
    for tf_u in ("M30", "M15", "M5"):
        if isinstance(preloaded_market_state.get(tf_u), dict) or raw_candles.get(tf_u):
            diagnostic_tfs.append(tf_u)

    signal["candleFreshness"] = {
        tf_u: candle_freshness_diagnostic(
            pair,
            tf_u,
            _scan_series_for_diag(tf_u),
            source=pair.get("source"),
            time_now=now,
        )
        for tf_u in diagnostic_tfs
    }

    for tf_u in diagnostic_tfs:
        state = _scan_state(tf_u)
        confirmed = list(state.get("confirmed") or [])
        forming = state.get("forming")
        provider_series = list(raw_candles.get(tf_u) or [])
        if not provider_series:
            provider_series = confirmed + ([forming] if forming else [])
        consistency_paths = {
            "raw_provider": provider_series,
            "market_state": state,
            "engine_a": confirmed,
            "engine_b": confirmed,
            "scanner": confirmed,
            "compare": confirmed,
        }
        consistency = check_live_candle_consistency(
            pair,
            tf_u,
            consistency_paths,
            time_now=now,
        )
        if consistency:
            signal.setdefault("candleConsistency", {})[tf_u] = consistency

    exec_fresh = evaluate_execution_data_freshness(signal, cfg)
    signal["dataFreshness"] = exec_fresh
    if (
        bool(cfg.get("SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS", True))
        and isinstance(exec_fresh, dict)
        and not exec_fresh.get("allowed")
    ):
        signal["executable"] = False
    return exec_fresh


def _fetch_ab_crypto_signal_candles(
    runtime,
    pair: dict,
    tf: str,
    limit: int,
    *,
    force_refresh: bool = False,
):
    """Fetch shared Engine A/B scan candles with optional crypto Bybit experiment."""
    def _default_fetch(pair_arg: dict, tf_arg: str, limit_arg: int):
        if force_refresh:
            return runtime.fetch_candles(
                pair_arg, tf_arg, limit_arg, force_refresh=True
            )
        return runtime.fetch_candles(pair_arg, tf_arg, limit_arg)

    if (
        str((pair or {}).get("type") or "").lower() != "crypto"
        or resolve_crypto_signal_feed("AB", CONFIG) == "binance"
    ):
        return _default_fetch(pair, tf, limit), None

    result = fetch_crypto_signal_candles(
        pair,
        tf,
        limit,
        engine="AB",
        config=CONFIG,
        default_fetch=_default_fetch,
        bybit_fetch=getattr(runtime, "fetch_bybit_klines", None),
        bybit_paginated_fetch=getattr(runtime, "fetch_bybit_klines_paginated", None),
    )
    return result.candles, result.meta


def _fetch_scan_h4_candles(
    runtime,
    pair: dict,
    limit: int,
    preloaded_h4: list | None,
    *,
    force_refresh: bool = False,
):
    """Return H4 scan candles, reusing intermarket preloads when feed policy allows it."""
    crypto_bybit_signal_feed = (
        str((pair or {}).get("type") or "").lower() == "crypto"
        and resolve_crypto_signal_feed("AB", CONFIG) == "bybit"
    )
    if preloaded_h4 is not None and not crypto_bybit_signal_feed:
        return preloaded_h4, None, crypto_bybit_signal_feed
    candles, meta = _fetch_ab_crypto_signal_candles(
        runtime, pair, "H4", limit, force_refresh=force_refresh
    )
    return candles, meta, crypto_bybit_signal_feed


def _engine_b_execution_levels_marked_invalid(conf_b: dict | None) -> bool:
    conf_b = conf_b or {}
    if conf_b.get("execution_levels_valid") is False:
        return True
    return conf_b.get("execution_level_reject_reason") in (
        "max_sl_exceeded",
        "tp_wrong_side",
        "levels_missing",
    )


def _engine_b_level_pair(conf_b: dict | None, res_b: dict | None) -> tuple[float | None, float | None]:
    conf_b = conf_b or {}
    res_b = res_b or {}
    exec_invalid = _engine_b_execution_levels_marked_invalid(conf_b)

    sl = conf_b["execution_sl"] if "execution_sl" in conf_b else None
    if sl is None and not exec_invalid:
        sl = res_b.get("execution_sl")
    if sl is None and not exec_invalid:
        sl = res_b.get("recommended_stop_loss")

    tp = conf_b["execution_tp"] if "execution_tp" in conf_b else None
    if tp is None and not exec_invalid:
        tp = res_b.get("execution_tp")
    if tp is None and not exec_invalid:
        tp = res_b.get("recommended_take_profit")
    try:
        sl_f = float(sl) if sl is not None else None
        tp_f = float(tp) if tp is not None else None
    except (TypeError, ValueError):
        return None, None
    if sl_f is None or tp_f is None:
        return None, None
    return sl_f, tp_f


def _engine_b_level_targets(
    conf_b: dict | None,
    res_b: dict | None,
) -> tuple[
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    str | None,
]:
    sl, legacy_tp = _engine_b_level_pair(conf_b, res_b)
    if sl is None or legacy_tp is None:
        return None, None, None, None, None, None
    conf_b = conf_b or {}
    exec_invalid = _engine_b_execution_levels_marked_invalid(conf_b)
    tp1 = conf_b.get("execution_tp1") if "execution_tp1" in conf_b else None
    tp2 = conf_b.get("execution_tp2") if "execution_tp2" in conf_b else None
    if tp1 is None and not exec_invalid:
        tp1 = legacy_tp
    if tp2 is None and not exec_invalid:
        tp2 = legacy_tp
    try:
        tp1_f = float(tp1) if tp1 is not None else None
        tp2_f = float(tp2) if tp2 is not None else None
    except (TypeError, ValueError):
        return None, None, None, None, None, None
    if tp1_f is None or tp2_f is None:
        return None, None, None, None, None, None
    rr1 = conf_b.get("execution_rr1")
    rr2 = conf_b.get("execution_rr2")
    try:
        rr1_f = float(rr1) if rr1 is not None else None
    except (TypeError, ValueError):
        rr1_f = None
    try:
        rr2_f = float(rr2) if rr2 is not None else None
    except (TypeError, ValueError):
        rr2_f = None
    return sl, tp1_f, tp2_f, rr1_f, rr2_f, conf_b.get("exit_strategy")


def _engine_b_levels_apply_to_generic(signal: dict) -> bool:
    """Return True when Engine B execution levels may overwrite generic SL/TP.

    Engine A rows must keep their Engine A levels even when an Engine B
    overlay is attached. Generic level overwrite is permitted only when the
    selected execution engine is explicitly Engine B, or when Engine C has
    explicitly selected Engine B execution levels.
    """
    raw_identity = (
        signal.get("engine")
        or signal.get("engine_source")
        or signal.get("source_engine")
        or ""
    )
    ident = str(raw_identity).strip().lower()
    if ident in (
        "engine_b",
        "b",
        "naked",
        "naked_structure",
        "structure",
        "smc",
    ):
        return True
    if ident in ("engine_c", "engine_c_consensus", "consensus", "c"):
        selected = str(signal.get("engine_c_selected_levels") or "").strip().lower()
        if selected in ("engine_b", "b"):
            return True
    return False


def _resolve_engine_b_h4_snap(
    h4_candles: list | None,
    zone_candles: list | None,
    asset_type: str,
) -> dict:
    """Return the H4 indicator snapshot Engine B should consume."""
    from market_structure import resolve_engine_b_h4_snap

    _ = zone_candles  # legacy param; zone TF may be D1 for swing
    return resolve_engine_b_h4_snap(h4_candles, asset_type)


def _apply_engine_b_scan_levels(signal: dict, conf_b: dict | None, res_b: dict | None) -> None:
    if not bool((conf_b or {}).get("passed", False)):
        signal["engine_b_levels_gate_passed"] = False
        return
    sl, tp1, tp2, rr1, rr2, exit_strategy = _engine_b_level_targets(conf_b, res_b)
    if sl is None or tp1 is None or tp2 is None:
        signal["engine_b_levels_gate_passed"] = False
        return
    signal["engine_b_levels_gate_passed"] = True
    legacy_tp = (conf_b or {}).get("execution_tp")
    try:
        legacy_tp = float(legacy_tp) if legacy_tp is not None else tp2
    except (TypeError, ValueError):
        legacy_tp = tp2
    # Engine B overlay levels are always stored separately so diagnostics,
    # research, and Engine C have access to them without contaminating
    # Engine A's generic SL/TP fields.
    signal["engine_b_execution_sl"] = sl
    signal["engine_b_execution_tp"] = legacy_tp
    signal["engine_b_execution_tp1"] = tp1
    signal["engine_b_execution_tp2"] = tp2
    signal["engine_b_execution_rr1"] = rr1
    signal["engine_b_execution_rr2"] = rr2
    signal["engine_b_exit_strategy"] = exit_strategy
    signal["engine_b_level_source"] = "engine_b_execution"
    signal["engine_b_rr_used_for_gate"] = (conf_b or {}).get("rr_used_for_gate")
    # Execution must consume the same venue-aware plan that Engine B gated.
    # Keep this annotation separate from generic levels so Engine A/C overlays
    # cannot inherit an Engine B runner exception.
    for key in (
        "execution_plan",
        "execution_plan_reason",
        "rr_required",
        "rr_passed",
        "single_target_rr_floor",
        "single_target_execution_tp",
        "single_target_valid",
    ):
        signal[f"engine_b_{key}"] = (conf_b or {}).get(key)

    # Stamp the resolved Engine B exit mode + runner directive. The helper
    # no-ops for non-Engine-B rows, so Engine A rows carrying a B overlay
    # keep their own exit_mode untouched.
    apply_engine_b_exit_strategy(
        signal,
        signal.get("engine") or signal.get("engine_source") or signal.get("source_engine"),
        CONFIG,
    )

    # Engine identity is the primary gate: generic level overwrite only fires
    # for explicit Engine B rows (or Engine C with B levels selected). Engine
    # A rows are protected regardless of the legacy config flag's value.
    if not _engine_b_levels_apply_to_generic(signal):
        return
    if not bool(CONFIG.get("ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", False)):
        return
    signal["sl"] = sl
    signal["tp1"] = tp1
    signal["tp2"] = tp2
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

    # min_score_scaled is a TOTAL-score floor (gates + quality bonuses), so it
    # must be compared against the total score. gate_score/gate_pct are wrong
    # units here: gate_pct is 100 for any full-gate pass regardless of quality.
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
    try:
        score = float(signal.get("confluenceScore", 0) or 0)
        threshold = float(
            signal.get("scanThreshold")
            or signal.get("scanThresholdEffective")
            or signal.get("threshold")
            or 0
        )
    except (TypeError, ValueError):
        score = 0.0
        threshold = 0.0
    if threshold > 0 and score >= threshold:
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


def _is_engine_a_v3_signal(signal: dict | None) -> bool:
    return bool(
        isinstance(signal, dict)
        and str(signal.get("engine") or "").upper() == "ENGINE_A_V3"
        and str(signal.get("contractVersion") or "").startswith("3.")
    )


def _classify_signal(signal: dict, pair: dict) -> tuple[str, str]:
    if not _is_engine_a_v3_signal(signal):
        return _classify_legacy_signal(signal, pair)

    from athena_app.services.engine_a_v3_classify import classify_engine_a_v3_signal

    tier, reason = classify_engine_a_v3_signal(signal, pair)
    diagnostics = [
        str(item.get("detail"))
        for item in signal.get("scanDiagnostics") or []
        if isinstance(item, dict) and item.get("detail")
    ]
    if diagnostics:
        merged = "; ".join(dict.fromkeys(([reason] if reason else []) + diagnostics))
        return tier, merged
    return tier, reason


def _make_engine_b_only_signal_stub(pair: dict) -> dict:
    """Build a minimal sig stub when Engine A produced no signal.

    The stub keeps the downstream pipeline able to attach Engine B overlay
    fields (verdict, score, SL/TP, RR, funnel) and route the row as an Engine
    B-only result. Engine A scoring fields are zeroed/absent — the row is
    never auto-traded and is classified by ``_classify_engine_b_only_signal``.
    """
    from engine_a_analyze_abort import build_abort_stub_fields, pop_analyze_pair_abort
    from scoring import get_pair_score_group

    display = pair.get("display") or pair.get("symbol")
    stub = {
        "engine_source": ENGINE_B_SOURCE,
        "engine": "B",
        "engine_name": "Engine B",
        "pair": display,
        "symbol": pair.get("symbol"),
        "display": display,
        "type": pair.get("type"),
        "asset_type": pair.get("type"),
        "scoreGroup": get_pair_score_group(pair),
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
    abort = pop_analyze_pair_abort(pair)
    if abort:
        stub.update(build_abort_stub_fields(abort))
    return stub


def _engine_a_block_reason(engine_a_signal: dict | None) -> str:
    if not isinstance(engine_a_signal, dict):
        return "engine_a_no_direction"

    if _is_engine_a_v3_signal(engine_a_signal):
        reasons = engine_a_signal.get("rejectionReasons")
        if isinstance(reasons, list) and reasons:
            return "; ".join(str(reason) for reason in reasons if reason)
        return f"engine_a_v3:{engine_a_signal.get('decision') or 'NO_SIGNAL'}"

    data_freshness = engine_a_signal.get("dataFreshness")
    if isinstance(data_freshness, dict):
        reason = data_freshness.get("reason") or data_freshness.get("status")
        if reason:
            return str(reason)

    for key in ("skipReason", "signalTierReason", "watchlistReason", "reason"):
        reason = engine_a_signal.get(key)
        if reason:
            return str(reason)

    fd = engine_a_signal.get("factorDiagnostics")
    if isinstance(fd, dict):
        abort_reason = fd.get("abortReason")
        if abort_reason:
            return f"engine_a_abort:{abort_reason}"

    direction = engine_a_signal.get("direction")
    if direction:
        return f"engine_a_no_trade_direction:{direction}"
    return "engine_a_no_direction"


def _safe_regression_tag_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "_".join(text.split())


def _engine_a_regression_tags(engine_a_signal: dict | None) -> str:
    """Build explicit Engine A provenance tags for the REGRESSION-A scan line."""
    if not isinstance(engine_a_signal, dict):
        return " scorer=not_called fallback_used=false failure=no_signal"

    fd = engine_a_signal.get("factorDiagnostics") or {}
    if isinstance(fd, dict) and fd.get("engineVersion"):
        tags = [f"scorer={_safe_regression_tag_value(fd.get('engineVersion'))}"]
        selected = _safe_regression_tag_value(fd.get("scorerSelected"))
        if selected:
            tags.append(f"selected={selected}")
        abort = _safe_regression_tag_value(fd.get("abortReason"))
        if abort:
            tags.append(f"abort={abort}")
    else:
        tags = ["scorer=not_called"]
        data_freshness = engine_a_signal.get("dataFreshness")
        failure = ""
        if isinstance(data_freshness, dict) and data_freshness.get("allowed") is False:
            tags.append("selected=pre_scoring_freshness_gate")
            failure = data_freshness.get("reason") or data_freshness.get("status")
        if not failure:
            failure = (
                engine_a_signal.get("engineAAbortReason")
                or engine_a_signal.get("failureReason")
                or engine_a_signal.get("skipReason")
                or engine_a_signal.get("reason")
            )
        failure = _safe_regression_tag_value(failure)
        if failure:
            tags.append(f"failure={failure}")

    fallback_used = bool(
        engine_a_signal.get("fallback_used")
        or engine_a_signal.get("fallbackUsed")
        or engine_a_signal.get("legacyFallbackUsed")
    )
    tags.append(f"fallback_used={str(fallback_used).lower()}")
    return " " + " ".join(tags)


def _regression_candle_count(raw_candles: dict | None, tf: str) -> int:
    if not isinstance(raw_candles, dict):
        return 0
    return len(raw_candles.get(tf) or [])


def _copy_engine_a_v3_stub_fields(stub: dict, engine_a_signal: dict) -> None:
    """Preserve V3 specialist display fields when row is demoted to Engine B-only."""
    if not _is_engine_a_v3_signal(engine_a_signal):
        return
    stub["engine_a_v3_blocked"] = True
    for key in (
        "setupId",
        "decision",
        "rejectionReasons",
        "predicates",
        "qualified",
        "price",
        "sl",
        "tp1",
        "tp2",
        "rr1",
        "rr2",
        "entryZone",
        "invalidation",
        "family",
        "subclass",
        "horizon",
        "contractVersion",
        "validationStatus",
        "validationArtifact",
        "executionScope",
        "exitPolicy",
        "scoringProfile",
        "componentScores",
        "confluenceThreshold",
        "engineATradeEnabled",
    ):
        value = engine_a_signal.get(key)
        if value is not None:
            stub[f"engine_a_{key}"] = value


def _make_engine_b_only_signal_stub_from_blocked_engine_a(
    pair: dict,
    engine_a_signal: dict | None,
) -> dict:
    """Convert a blocked/neutral Engine A row into an explicit B-only row.

    Engine B may still be scanned independently, but its direction must not be
    written onto an Engine A-sourced row. Preserve the A block as diagnostics so
    the UI/audit trail can show why Engine A did not produce a trade direction.
    """
    stub = _make_engine_b_only_signal_stub(pair)
    stub["engine_a_blocked"] = True
    stub["engine_a_block_reason"] = _engine_a_block_reason(engine_a_signal)
    # UI row keys require LONG/SHORT; keep display direction when A scored but
    # was demoted (e.g. indeterminate_trend) without using it for execution.
    if isinstance(engine_a_signal, dict):
        _a_dir = engine_a_signal.get("direction")
        if _a_dir in ("LONG", "SHORT"):
            stub["direction"] = _a_dir
            stub["engine_a_direction"] = _a_dir

    if isinstance(engine_a_signal, dict):
        stub["engine_a_direction"] = engine_a_signal.get("direction")
        stub["engine_a_confluenceScore"] = engine_a_signal.get("confluenceScore")
        stub["engine_a_maxScore"] = engine_a_signal.get("maxScore")
        stub["engine_a_threshold"] = (
            engine_a_signal.get("confluenceThreshold")
            or engine_a_signal.get("threshold")
            or engine_a_signal.get("liveThreshold")
            or engine_a_signal.get("scanThresholdEffective")
            or engine_a_signal.get("scanThreshold")
        )
        stub["engine_a_scoreNorm"] = engine_a_signal.get("scoreNorm")
        if engine_a_signal.get("factorDiagnostics") is not None:
            stub["engine_a_factorDiagnostics"] = engine_a_signal.get("factorDiagnostics")
        if engine_a_signal.get("factorScores") is not None:
            stub["engine_a_factorScores"] = engine_a_signal.get("factorScores")
        if engine_a_signal.get("dataFreshness") is not None:
            stub["engine_a_dataFreshness"] = engine_a_signal.get("dataFreshness")
        if engine_a_signal.get("candleFetchMeta") is not None:
            stub["engine_a_candleFetchMeta"] = engine_a_signal.get("candleFetchMeta")
        if engine_a_signal.get("scanDiagnostics") is not None:
            stub["engine_a_scanDiagnostics"] = engine_a_signal.get("scanDiagnostics")
        if engine_a_signal.get("rejectionReasons") is not None:
            stub["engine_a_rejectionReasons"] = engine_a_signal.get("rejectionReasons")
        _copy_engine_a_v3_stub_fields(stub, engine_a_signal)

    return stub


def _engine_b_scan_direction_input(
    engine_a_direction: str | None,
    independent_direction: str | None,
    *,
    engine_b_only: bool,
    independent_enabled: bool,
) -> str | None:
    """Choose B's analysis direction without silently falling back across engines."""
    if engine_b_only or independent_enabled:
        return (
            independent_direction
            if independent_direction in ("LONG", "SHORT")
            else None
        )
    return engine_a_direction if engine_a_direction in ("LONG", "SHORT") else None


def _engine_b_independent_direction_probe(
    pair: dict,
    *,
    engine,
    d1_candles: list,
    h4_candles: list,
    h1_candles: list,
    entry_candles: list,
    current_price: float,
    atr: float,
    regime_label: str | None,
    style_profile: dict,
    resolved_style: str,
    asset_type: str,
    d1_snap: dict | None,
    h4_snap: dict | None,
    confidence_entry_candles: list | None = None,
    role_candles: dict[str, list] | None = None,
    dxy_h4_closes: list | None = None,
) -> tuple[str | None, dict | None, dict | None]:
    """Pick best Engine B direction independently of Engine A.

    Precomputes structure once (BT parity), then runs
    ``analyze_structure_direction`` for LONG and SHORT. For any CLEAR verdict
    computes confidence and tests the style/regime gate. Returns the best
    ``(direction, res_b, conf_b)`` tuple — preferring gate-passed over
    not-passed, then higher confidence score. Returns ``(None, None, None)``
    when neither direction has a CLEAR structural verdict.
    """
    from engine_b_snapshot import evaluate_engine_b_snapshot

    candles_by_role = {
        "D1": list(d1_candles or []),
        "H4": list(h4_candles or []),
        "H1": list(h1_candles or []),
        **{str(tf).upper(): list(rows or []) for tf, rows in (role_candles or {}).items()},
    }
    entry_tf = str(style_profile.get("entry_tf") or "H1").upper()
    candles_by_role[entry_tf] = list(entry_candles or candles_by_role.get(entry_tf) or [])

    def _gate(confidence, profile, regime, kind, **_kwargs):
        return engine_b_confidence_passes(confidence, profile, regime, kind)

    result = evaluate_engine_b_snapshot(
        pair,
        candles_by_role,
        current_price=current_price,
        style=resolved_style,
        atr_override=atr,
        score_group=style_profile.get("score_group"),
        style_profile=style_profile,
        regime_label=regime_label,
        d1_snapshot=d1_snap,
        h4_snapshot=h4_snap,
        dxy_h4_closes=dxy_h4_closes,
        confidence_entry_candles=(
            list(confidence_entry_candles)
            if confidence_entry_candles is not None
            else list(entry_candles or [])
        ),
        engine=engine,
        context_mode="live",
        gate_fn=_gate,
        picker_fn=engine_b_pick_directional_candidate,
        conflict_fn=independent_conflict_blocks_emit,
    )
    selected = result.selected
    if selected is None:
        return None, None, None
    return (
        str(selected["direction"]),
        dict(selected["structure"]),
        dict(selected["confidence"] or {}),
    )


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
    if signal.get("engine_a_blocked"):
        diagnostics.append(
            {
                "code": "engine_a_blocked",
                "detail": str(signal.get("engine_a_block_reason") or "engine_a_no_direction"),
            }
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


def _scan_signal_rank(signal: dict) -> float:
    """Sort key for final scan buckets without assuming Engine A score fields."""
    try:
        conviction = signal.get("combinedConviction")
        if conviction is not None:
            return float(conviction)
    except (TypeError, ValueError):
        pass

    try:
        score = float(signal.get("confluenceScore", 0) or 0)
        max_score = float(signal.get("maxScore", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if max_score <= 0:
        return 0.0
    return score / max_score


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


def _scalar_bool_gate(x: Any) -> bool | None:
    """Normalize native/numpy scalar booleans for JSON funnel diagnostics."""
    if x is None:
        return None
    if isinstance(x, str):
        value = x.strip().lower()
        if value == "true":
            return True
        if value == "false":
            return False
        return None
    try:
        x = x.item()
    except (AttributeError, ValueError):
        pass
    return x if isinstance(x, bool) else bool(x)


def _attach_engine_b_timeframe_provenance(
    res_b: dict,
    policy: Any,
    *,
    actual_structure_tf: str,
    actual_trigger_tf: str,
    actual_atr_tf: str,
) -> None:
    """Record actual consumed TFs without relabelling policy-only roles."""
    structure_tf = str(res_b.get("structure_tf") or actual_structure_tf).upper()
    trigger_tf = str(res_b.get("trigger_timeframe") or actual_trigger_tf).upper()
    atr_tf = str(actual_atr_tf).upper()
    execution_tf_actual = (
        res_b.get("executionTfActual") or res_b.get("execution_tf_actual")
    )
    execution_tf_actual = (
        str(execution_tf_actual).upper() if execution_tf_actual else None
    )
    execution_tf_consumed = bool(
        res_b.get("executionTfConsumed", res_b.get("execution_tf_consumed", False))
    )
    res_b.update(
        {
            "structure_tf": structure_tf,
            "entry_tf": trigger_tf,
            "trigger_tf": trigger_tf,
            "execution_tf": policy.execution_tf.value,
            "atr_tf": atr_tf,
            "structure_tf_actual": structure_tf,
            "entry_tf_actual": trigger_tf,
            "trigger_tf_actual": trigger_tf,
            "execution_tf_actual": execution_tf_actual,
            "execution_tf_consumed": execution_tf_consumed,
            "atr_tf_actual": atr_tf,
            "structure_tf_policy": policy.structure_tf.value,
            "setup_tf_policy": policy.setup_tf.value,
            "trigger_tf_policy": policy.trigger_tf.value,
            "execution_tf_policy": policy.execution_tf.value,
            "atr_tf_policy": policy.structure_tf.value,
            "nearest_support_resistance_timeframe": structure_tf,
        }
    )


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
        # Advisory-only, mirrors cross_engine_context._cross_context: surfaced
        # here so the same signal is visible in the operator-facing scan
        # diagnostic, not only inside the AI review packet. Self-computed from
        # the two direction fields above rather than a pass-through, so it is
        # always populated regardless of which code path produced this row.
        # Never used to hide, veto, or reorder a signal — see CLAUDE.md
        # "Engine A and Engine B scoring must not affect each other."
        "direction_conflict": bool(
            sig.get("direction") in ("LONG", "SHORT")
            and sig.get("engine_b_direction") in ("LONG", "SHORT")
            and sig.get("direction") != sig.get("engine_b_direction")
        ),
        # Pass-through of the full-scan blended-conviction alignment check
        # (scanner._engine_b_scan_combined_conviction). None when that check
        # never ran for this row (e.g. an early gate skip before B scored).
        "engines_aligned": sig.get("engine_b_direction_aligned_with_a"),
        "structure_ok": _scalar_bool_gate(cnf.get("structure_ok")),
        "location_ok": _scalar_bool_gate(cnf.get("location_ok")),
        "entry_ok": _scalar_bool_gate(cnf.get("entry_ok")),
        "room_ok": _scalar_bool_gate(cnf.get("room_ok")),
        "space_gate_ok": _scalar_bool_gate(cnf.get("space_gate_ok")),
        "rr_ok": _scalar_bool_gate(cnf.get("rr_ok")),
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
        "execution_tp1": cnf.get("execution_tp1"),
        "execution_tp2": cnf.get("execution_tp2"),
        "execution_rr1": cnf.get("execution_rr1"),
        "execution_rr2": cnf.get("execution_rr2"),
        "exit_strategy": cnf.get("exit_strategy"),
        "tp1_source": cnf.get("tp1_source"),
        "tp2_source": cnf.get("tp2_source"),
        "rr": _scalar_float_gate(cnf.get("rr")),
        "rr_source": cnf.get("rr_source"),
        "execution_level_reject_reason": cnf.get("execution_level_reject_reason"),
        "tp1_path_clear": _scalar_bool_gate(cnf.get("tp1_path_clear")),
        "tp1_path_block_reason": cnf.get("tp1_path_block_reason"),
        "entry_inside_opposing_zone": _scalar_bool_gate(
            cnf.get("entry_inside_opposing_zone")
        ),
        "tp1_before_opposing_zone": _scalar_bool_gate(
            cnf.get("tp1_before_opposing_zone")
        ),
        "tp1_clamped_to_opposing_zone": _scalar_bool_gate(
            cnf.get("tp1_clamped_to_opposing_zone")
        ),
        "tp1_clamp_reject_reason": cnf.get("tp1_clamp_reject_reason"),
        "opposing_zone_distance": _scalar_float_gate(
            (cnf.get("tp1_path_diag") or {}).get("opposing_zone_distance")
            if isinstance(cnf.get("tp1_path_diag"), dict)
            else None
        ),
        "tp_structural_limited": rb.get("tp_structural_limited"),
        "engine_b_aggtrade_required": cnf.get("aggtrade_required", rb.get("aggtrade_required")),
        "engine_b_aggtrade_available": cnf.get("aggtrade_available", rb.get("aggtrade_available")),
        "engine_b_aggtrade_reason": cnf.get("aggtrade_reason", rb.get("aggtrade_reason")),
        "engine_b_orderflow_points": _scalar_float_gate(cnf.get("aggtrade_orderflow_points")),
        "engine_b_data_fidelity": cnf.get("engine_b_data_fidelity") or rb.get("engine_b_data_fidelity"),
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

    if _is_engine_a_v3_signal(signal):
        for reason in signal.get("rejectionReasons") or []:
            diagnostics.append({"code": "v3_rejection", "detail": str(reason)})
    elif signal["confluenceScore"] < threshold:
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


def run_full_scan(
    style: str = "auto",
    asset_class: str | None = None,
    refresh_market_data: bool = False,
) -> dict[str, Any]:
    """Parallel scan of tracked pairs. Optional asset_class filter."""
    r = rt()

    if refresh_market_data and callable(getattr(r, "clear_bybit_kline_cache", None)):
        r.clear_bybit_kline_cache()

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

    _reset_engine_b_gate_failures()

    _requested_style = _normalize_style(style)

    _valid_classes = {"crypto", "forex", "stock", "commodity", "index", "etf"}

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
            if _ac == "etf":
                candidate_pairs = [
                    p for p in candidate_pairs if p.get("type") in ("etf", "etf_bond")
                ]
            else:
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

        if refresh_market_data:
            news_ctx = r.fetch_news_context(
                candidate_pairs, allow_refresh=True, force_refresh=True
            )
        else:
            news_ctx = r.fetch_news_context(candidate_pairs, allow_refresh=False)

        try:
            yield_ctx = (
                r.fetch_yield_curve(force_refresh=True)
                if refresh_market_data
                else r.fetch_yield_curve()
            )

            if yield_ctx:
                news_ctx["yieldCurve"] = yield_ctx

        except Exception as e:
            log.warning(f"[YIELD] scan fetch err: {e}")

        try:
            ds_ctx = (
                r.fetch_div_split_context(force_refresh=True)
                if refresh_market_data
                else r.fetch_div_split_context()
            )

            if ds_ctx:
                news_ctx["divSplit"] = ds_ctx

        except Exception as e:
            log.warning(f"[DIVS] scan fetch err: {e}")

        try:
            earnings_ctx = (
                r.fetch_upcoming_earnings_context(candidate_pairs, force_refresh=True)
                if refresh_market_data
                else r.fetch_upcoming_earnings_context(candidate_pairs)
            )

            if earnings_ctx:
                news_ctx["upcomingEarnings"] = earnings_ctx

        except Exception as e:
            log.warning(f"[EARN] scan fetch err: {e}")

        try:
            _btc_pair = {"symbol": "BTCUSDT", "source": "binance"}
            btc = (
                r.fetch_candles(
                    _btc_pair, "D1", CONFIG["D1_CANDLES"], force_refresh=True
                )
                if refresh_market_data
                else r.fetch_candles(_btc_pair, "D1", CONFIG["D1_CANDLES"])
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
        # Resolved ahead of the intermarket preload so that fan-out can reuse the
        # same bounded pool size as the scan itself.
        _max_workers = get_optimal_workers(
            configured_max=int(CONFIG.get("SCAN_MAX_WORKERS", 8) or 8),
            conservative=True
        )
        _scan_timeout = int(CONFIG.get("SCAN_TIMEOUT_SEC", 45) or 45)

        intermarket_snapshot = None
        _im_cfg = CONFIG.get("INTERMARKET_CONFIRMATION", {}) or {}
        if bool(_im_cfg.get("enabled")) and bool(_im_cfg.get("full_scan_time_matrix", True)):
            try:
                _im_h4_limit = max(int(_scan_limits["H4"]), 220)
                _im_preloaded_h4 = {}

                def _preload_intermarket_h4(_pair):
                    # Route crypto pairs through the same Bybit/Binance resolver the
                    # per-pair scoring path uses so intermarket H4 context is built on
                    # the same provider Engine A scores. Non-crypto / binance-feed
                    # pairs fall through to fetch_candles unchanged.
                    _candles, _ = _fetch_ab_crypto_signal_candles(
                        r, _pair, "H4", _im_h4_limit,
                        force_refresh=refresh_market_data,
                    )
                    return _pair["display"], _candles

                # These fetches used to run one at a time on the critical path,
                # before the scan pool started — the single largest cost of
                # enabling intermarket. Same per-pair fan-out the scan already
                # does, so no new concurrency is introduced against the feeds.
                with ThreadPoolExecutor(max_workers=_max_workers) as _im_pool:
                    _im_futures = {
                        _im_pool.submit(_preload_intermarket_h4, _pair): _pair
                        for _pair in active_pairs
                    }
                    for _im_fut in as_completed(_im_futures):
                        _im_pair = _im_futures[_im_fut]
                        try:
                            _display, _candles = _im_fut.result(timeout=_scan_timeout)
                        except Exception as _im_fetch_err:
                            # One bad symbol must not cost the whole snapshot; an
                            # absent preload just falls back to fetch_candles.
                            log.debug(
                                "[SCAN] %s intermarket H4 preload failed: %s",
                                _im_pair.get("display", "?"),
                                _im_fetch_err,
                            )
                            continue
                        if _candles:
                            _im_preloaded_h4[_display] = _candles
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

        def _analyse(pair):
            try:
                _pair_style = r.resolve_scan_style(_requested_style, pair)
                _lim = scan_candle_limits()
                _im_h4 = (
                    ((intermarket_snapshot or {}).get("seriesStore", {}) or {})
                    .get(pair.get("display"), {})
                    .get("candles")
                )
                _d1_res, _d1_meta = _fetch_ab_crypto_signal_candles(
                    r, pair, "D1", _lim["D1"], force_refresh=refresh_market_data
                )
                _h4_res, _h4_meta, _crypto_bybit_signal_feed = _fetch_scan_h4_candles(
                    r, pair, _lim["H4"], _im_h4, force_refresh=refresh_market_data
                )
                _h1_res, _h1_meta = _fetch_ab_crypto_signal_candles(
                    r, pair, "H1", _lim["H1"], force_refresh=refresh_market_data
                )
                from engine_a_v3.timeframes import resolve_live_v3_entry_timeframe
                from engine_a_groups import resolve_score_group_by_type
                from engine_a_v3.routing import route_specialist
                from timeframe_policy import resolve_timeframe_policy

                # analyze_pair resolves the Engine A policy from
                # route_specialist(), which honours an explicit pair
                # score_group / PAIR_PROFILES override before falling back to
                # type dispatch. Prefetching under the type-dispatch group alone
                # meant an overridden pair had a different policy's rungs
                # preloaded than the one it scores with.
                _policy_score_group = (
                    route_specialist(pair).score_group
                    or resolve_score_group_by_type(pair)
                )
                _policy_a = resolve_timeframe_policy(
                    pair.get("display") or pair.get("symbol") or "",
                    pair.get("type", ""),
                    _policy_score_group,
                    _pair_style,
                )
                _policy_b = resolve_timeframe_policy(
                    pair.get("display") or pair.get("symbol") or "",
                    pair.get("type", ""),
                    _policy_score_group,
                    f"engine_b_{_pair_style}",
                )

                _required_analysis_tfs = {
                    tf
                    for tf in (
                        resolve_live_v3_entry_timeframe(
                            pair.get("type", ""), _pair_style, source=pair.get("source")
                        ),
                        _policy_a.setup_tf.value,
                        _policy_a.trigger_tf.value,
                        _policy_b.setup_tf.value,
                        _policy_b.trigger_tf.value,
                    )
                    if tf and tf not in {"D1", "H4", "H1"}
                }
                _execution_only_tfs = {
                    tf
                    for tf in (
                        _policy_a.execution_tf.value,
                        _policy_b.execution_tf.value,
                    )
                    if tf
                    and tf not in {"D1", "H4", "H1"}
                    and tf not in _required_analysis_tfs
                }
                _live_entry_tfs = _required_analysis_tfs | _execution_only_tfs
                _lower_results: dict[str, tuple[list | None, dict | None]] = {}
                for _tf in _live_entry_tfs:
                    _lower_results[_tf] = _fetch_ab_crypto_signal_candles(
                        r, pair, _tf, _lim[_tf], force_refresh=refresh_market_data
                    )
                raw_candles = {
                    "D1": _d1_res,
                    "H4": (None if _crypto_bybit_signal_feed else _im_h4) or _h4_res,
                    "H1": _h1_res,
                    **{tf: result[0] for tf, result in _lower_results.items()},
                }
                preloaded_market_state = {}
                preloaded_candles_for_a = dict(raw_candles)
                if pair.get("source") == "mt5":
                    try:
                        from athena_app.services.market_state import (
                            candle_timestamp_epoch,
                            get_tf_market_state,
                        )

                        for _tf in ("D1", "H4", "H1", *sorted(_live_entry_tfs)):
                            _raw = raw_candles.get(_tf) or []
                            _state = get_tf_market_state(
                                pair,
                                _tf,
                                candles=_raw,
                                provider_symbol=(
                                    r.mt5_map_symbol(pair.get("display") or pair.get("symbol") or "")
                                    if callable(getattr(r, "mt5_map_symbol", None))
                                    else pair.get("mt5_symbol")
                                ),
                            )
                            preloaded_market_state[_tf] = _state
                            _active = list(_state.get("confirmed") or [])
                            if _tf in _live_entry_tfs and _state.get("forming"):
                                _active.append(_state["forming"])
                            preloaded_candles_for_a[_tf] = _active
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
                else:
                    try:
                        from athena_app.services.market_state import get_tf_market_state

                        for _tf in ("D1", "H4", "H1", *sorted(_live_entry_tfs)):
                            _state = get_tf_market_state(
                                pair,
                                _tf,
                                candles=list(raw_candles.get(_tf) or []),
                                provider_symbol=(
                                    pair.get("bybit_symbol")
                                    or pair.get("symbol")
                                ),
                            )
                            preloaded_market_state[_tf] = _state
                            _active = list(_state.get("confirmed") or [])
                            if _tf in _live_entry_tfs and _state.get("forming"):
                                _active.append(_state["forming"])
                            preloaded_candles_for_a[_tf] = _active
                    except Exception as _state_err:
                        log.debug(
                            "[SCAN][STATE][A] %s provider market-state preload failed: %s",
                            pair.get("display", "?"),
                            _state_err,
                        )
                try:
                    with r.live_prices_lock:
                        _policy_live_prices = dict(r.live_prices)
                except (AttributeError, TypeError):
                    _policy_live_prices = {}
                try:
                    from scalp_engine import get_sessions_for_time

                    _sessions = get_sessions_for_time(
                        pair.get("type", ""),
                        symbol=pair.get("display") or pair.get("symbol") or "",
                    )
                    _speed_session = "+".join(_sessions) if _sessions else "closed"
                except Exception:
                    _speed_session = None
                try:
                    _speed_event_risk = _build_event_risk(
                        pair,
                        ds_ctx,
                        earnings_ctx,
                        _closed_exchanges,
                    )
                    _speed_scheduled_event = bool(_speed_event_risk.get("reasons"))
                except Exception:
                    _speed_scheduled_event = None
                _speed_state = _scan_speed_state(
                    pair,
                    preloaded_market_state,
                    _policy_live_prices,
                    current_session=_speed_session,
                    scheduled_event=_speed_scheduled_event,
                )
                fetch_meta = {
                    "D1": _d1_meta or get_candle_fetch_meta(pair, "D1", _lim["D1"]),
                    "H4": _h4_meta or get_candle_fetch_meta(pair, "H4", _lim["H4"]),
                    "H1": _h1_meta or get_candle_fetch_meta(pair, "H1", _lim["H1"]),
                    **{
                        tf: meta or get_candle_fetch_meta(pair, tf, _lim[tf])
                        for tf, (_, meta) in _lower_results.items()
                    },
                }
                rate_limited_tfs = [
                    tf
                    for tf, meta in fetch_meta.items()
                    if tf in _required_analysis_tfs or tf in {"D1", "H4", "H1"}
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
                _analyze_kwargs = {
                    "style": _pair_style,
                    "regime_context": _regime_context,
                    "preloaded_candles": preloaded_candles_for_a,
                    "preloaded_market_state": preloaded_market_state,
                    "preloaded_fetch_meta": fetch_meta,
                    "intermarket_snapshot": intermarket_snapshot,
                    "timeframe_speed_state": _speed_state,
                }
                if refresh_market_data:
                    _analyze_kwargs["refresh_market_data"] = True
                sig_a = r.analyze_pair(
                    pair,
                    btc_bias,
                    **_analyze_kwargs,
                )

                if sig_a:
                    from timeframe_policy import attach_timeframe_policy_payload

                    attach_timeframe_policy_payload(
                        sig_a,
                        pair,
                        _pair_style,
                        engine="engine_a",
                        market_states=preloaded_market_state,
                        speed_state=_speed_state,
                    )

                if sig_a:
                    try:
                        from news_sentiment_feed import apply_news_sentiment_to_scan_result

                        apply_news_sentiment_to_scan_result(
                            sig_a,
                            pair,
                            config=CONFIG,
                            eodhd_ticker_for_pair=r.eodhd_ticker_for_pair,
                            threshold=sig_a.get("threshold"),
                            max_score=sig_a.get("maxScore", 3.0),
                        )
                    except Exception as _news_sent_exc:
                        log.debug(
                            "[SCAN] %s news sentiment blend skipped: %s",
                            pair.get("display", "?"),
                            _news_sent_exc,
                        )

                # Attach execution-gate freshness so the scan UI shows execution
                # blocks. analyze_pair only sets dataFreshness from the pre-scoring
                # gate (allowed=True); the execution gate is stricter and can block
                # on stale_1_bucket. Mirrors the athena.py naked-scan pattern.
                if sig_a:
                    try:
                        _attach_engine_a_execution_freshness(
                            sig_a,
                            pair,
                            preloaded_market_state=preloaded_market_state,
                            raw_candles=raw_candles,
                            config=CONFIG,
                        )
                    except Exception as _scan_fresh_err:
                        log.debug(
                            "[SCAN] %s execution freshness unavailable: %s",
                            pair.get("display", "?"),
                            _scan_fresh_err,
                        )

                # REGRESSION CHECK: Log per-pair Engine A details
                d1_count = _regression_candle_count(raw_candles, "D1")
                h4_count = _regression_candle_count(raw_candles, "H4")
                h1_count = _regression_candle_count(raw_candles, "H1")
                if sig_a:
                    direction = str(sig_a.get("direction") or "NONE")
                    pair_type = str(pair.get("type") or "?")
                    _regression_tags = _engine_a_regression_tags(sig_a)
                    if _is_engine_a_v3_signal(sig_a):
                        print(
                            f"[REGRESSION-A] {pair['display']:12s} type={pair_type:8s} "
                            f"D1={d1_count:3d} H4={h4_count:3d} H1={h1_count:3d} "
                            f"decision={sig_a.get('decision')} setup={sig_a.get('setupId')} "
                            f"dir={direction:5s}{_regression_tags}"
                        )
                    else:
                        score = float(sig_a.get("confluenceScore", 0) or 0)
                        max_score = float(sig_a.get("maxScore", 3.0) or 3.0)
                        print(
                            f"[REGRESSION-A] {pair['display']:12s} type={pair_type:8s} "
                            f"D1={d1_count:3d} H4={h4_count:3d} H1={h1_count:3d} "
                            f"score={score:.2f}/{max_score:.1f} dir={direction:5s}"
                            f"{_regression_tags}"
                        )
                else:
                    pair_type = str(pair.get("type") or "?")
                    print(
                        f"[REGRESSION-A] {pair['display']:12s} type={pair_type:8s} "
                        f"D1={d1_count:3d} H4={h4_count:3d} H1={h1_count:3d} NO SIGNAL"
                        f"{_engine_a_regression_tags(None)}"
                    )

                # Engine separation: when Engine A produces no signal we MUST
                # still run Engine B and emit either an Engine B-only signal
                # row or an Engine B rejection/funnel row. Engine B output
                # must never depend on Engine A.
                engine_b_scan_only = False
                if not sig_a:
                    sig_a = _make_engine_b_only_signal_stub(pair)
                    engine_b_scan_only = True
                else:
                    # Engine B scan path must not branch on v3 decision — only on
                    # whether a tradable direction exists (pre-v3 contract).
                    if sig_a.get("direction") not in ("LONG", "SHORT"):
                        sig_a = _make_engine_b_only_signal_stub_from_blocked_engine_a(
                            pair,
                            sig_a,
                        )
                        engine_b_scan_only = True
                    else:
                        sig_a.setdefault("engine_source", ENGINE_A_SOURCE)
                        sig_a.setdefault("engine", "A")
                        sig_a.setdefault("engine_name", "Engine A")
                        sig_a["engine_a_present"] = True

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
                        symbol=pair.get("display") or pair.get("symbol") or "",
                        speed_state=_speed_state,
                    )
                    _dxy_h4_closes_b = None
                    if ptype == "forex" and (
                        bool(CONFIG.get("ENGINE_B_DXY_MACRO_GATE_ENABLED", False))
                        or bool(style_profile_b.get("macro_required", False))
                    ):
                        _dxy_fetch = getattr(r, "get_dxy_h4_closes", None)
                        if callable(_dxy_fetch):
                            try:
                                _dxy_h4_closes_b = _dxy_fetch()
                            except Exception as _dxy_err:
                                log.warning(
                                    "[SCAN+B] %s DXY history unavailable: %s",
                                    pair.get("display", "?"),
                                    _dxy_err,
                                )

                    _resolved_execution_tf_b = str(
                        style_profile_b.get("execution_tf")
                        or style_profile_b.get("entry_tf")
                        or "H1"
                    ).upper()
                    if (
                        _resolved_execution_tf_b not in {"D1", "H4", "H1"}
                        and _resolved_execution_tf_b not in preloaded_market_state
                    ):
                        _exec_raw_b, _exec_meta_b = _fetch_ab_crypto_signal_candles(
                            r,
                            pair,
                            _resolved_execution_tf_b,
                            _lim[_resolved_execution_tf_b],
                            force_refresh=refresh_market_data,
                        )
                        from athena_app.services.market_state import get_tf_market_state

                        _exec_state_b = get_tf_market_state(
                            pair,
                            _resolved_execution_tf_b,
                            candles=list(_exec_raw_b or []),
                        )
                        raw_candles[_resolved_execution_tf_b] = list(_exec_raw_b or [])
                        preloaded_market_state[_resolved_execution_tf_b] = _exec_state_b
                        _live_entry_tfs.add(_resolved_execution_tf_b)
                        fetch_meta[_resolved_execution_tf_b] = (
                            _exec_meta_b
                            or get_candle_fetch_meta(
                                pair,
                                _resolved_execution_tf_b,
                                _lim[_resolved_execution_tf_b],
                            )
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
                    _tf_active_map_b = dict(_tf_map_b)
                    for _tf in _live_entry_tfs:
                        _state = preloaded_market_state.get(_tf)
                        if not isinstance(_state, dict):
                            try:
                                from athena_app.services.market_state import (
                                    market_state_offset_hours,
                                    split_market_state,
                                )

                                _state = split_market_state(
                                    list(raw_candles.get(_tf) or []),
                                    _tf,
                                    pair.get("display") or pair.get("symbol") or "",
                                    offset_hours=market_state_offset_hours(pair, _tf),
                                )
                            except Exception:
                                _state = {}
                        _confirmed = list((_state or {}).get("confirmed") or [])
                        _active = list(_confirmed)
                        if (
                            bool(CONFIG.get("ENGINE_B_USE_FORMING_FOR_TRIGGER", True))
                            and (_state or {}).get("forming")
                        ):
                            _active.append(_state["forming"])
                        _tf_map_b[_tf] = _confirmed
                        _tf_active_map_b[_tf] = _active
                    _zone_tf_b = str(style_profile_b.get("zone_tf", "H4")).upper()
                    _entry_tf_b = str(style_profile_b.get("entry_tf", "H1")).upper()
                    _atr_tf_b = str(style_profile_b.get("atr_tf", "H4")).upper()
                    zone_candles_b = _select_engine_b_tf_candles(_zone_tf_b, _tf_map_b)
                    entry_candles_b = _select_engine_b_tf_candles(_entry_tf_b, _tf_map_b)
                    active_entry_candles_b = _select_engine_b_tf_candles(
                        _entry_tf_b, _tf_active_map_b
                    )
                    atr_candles_b = _select_engine_b_tf_candles(_atr_tf_b, _tf_map_b)

                    _eb_funnel_extras["candles_tf_ok"] = bool(
                        zone_candles_b and entry_candles_b and atr_candles_b
                    )

                    # Conditional M5 consumes the setup rung as its arming
                    # prerequisite, so that series is now scoring input and has
                    # to clear the same freshness bar as the trigger.
                    _active_lower_tfs_b: dict[str, list] = {}
                    if _entry_tf_b not in {"D1", "H4", "H1"}:
                        _active_lower_tfs_b[_entry_tf_b] = active_entry_candles_b
                    _m5_prereq_tf_b = str(
                        style_profile_b.get("execution_prerequisite_tf")
                        or style_profile_b.get("setup_tf")
                        or ""
                    ).upper()
                    if (
                        _entry_tf_b == "M5"
                        and str(style_profile_b.get("m5_policy") or "").lower() == "conditional"
                        and _m5_prereq_tf_b
                        and _m5_prereq_tf_b not in {"D1", "H4", "H1"}
                    ):
                        _active_lower_tfs_b[_m5_prereq_tf_b] = _select_engine_b_tf_candles(
                            _m5_prereq_tf_b, _tf_active_map_b
                        )

                    if zone_candles_b and entry_candles_b and atr_candles_b:
                        _stale_b_tfs, _fresh_diag_b = _engine_b_scan_freshness_stale_tfs(
                            pair, d1, h4, h1,
                            score_group=_pair_score_group,
                            style=resolved_style_b,
                            active_entry_tfs=_active_lower_tfs_b or None,
                        )
                        if _stale_b_tfs:
                            _fresh_reason = "STALE_DATA_ENGINE_B:" + ",".join(_stale_b_tfs)
                            sig_a["engine_b_error"] = _fresh_reason
                            sig_a["engine_b_freshness_blocked"] = True
                            sig_a["engine_b_freshness_diag"] = _fresh_diag_b
                            _eb_funnel_extras["engine_b_skip_stage"] = "freshness_gate"
                            _eb_funnel_extras["engine_b_freshness_stale_tfs"] = _stale_b_tfs
                            log.warning(
                                "[SCAN+B] %s freshness gate block: %s",
                                pair.get("display", "?"),
                                _fresh_reason,
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
                        if engine_b_forex_asian_session_blocks_bar(
                            entry_candles_b,
                            ptype,
                            display=pair.get("display") or pair.get("symbol"),
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
                        _atr_highs_b = [float(c["high"]) for c in atr_candles_b]
                        _atr_lows_b = [float(c["low"]) for c in atr_candles_b]
                        _atr_closes_b = [float(c["close"]) for c in atr_candles_b]
                        _atr_series_b = calc_atr(
                            _atr_highs_b, _atr_lows_b, _atr_closes_b, 14
                        )
                        atr_signal = float(_atr_series_b[-1]) if _atr_series_b else 0.0
                        atr = atr_signal
                        _eb_funnel_extras["atr_value"] = atr
                        _eb_funnel_extras["atr_source"] = "candle_atr_tf"
                        _eb_funnel_extras["bybit_atr_available"] = None
                        if (
                            ptype == "crypto"
                            and str(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
                            and hasattr(r, "bybit_atr_for_levels")
                        ):
                            bybit_atr = r.bybit_atr_for_levels(
                                pair,
                                resolved_style_b,
                                atr_tf=_atr_tf_b,
                            )
                            _eb_funnel_extras["bybit_atr_available"] = bool(bybit_atr)
                            if bybit_atr:
                                atr = float(bybit_atr)
                                _eb_funnel_extras["atr_value"] = atr
                                _eb_funnel_extras["atr_source"] = "bybit_levels"
                            elif not bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                                atr = 0.0
                                sig_a["engine_b_error"] = "bybit_atr_unavailable"
                        if atr and atr > 0:
                            _volatility_ok_b, _volatility_diag_b = engine_b_low_volatility_gate(
                                atr,
                                _atr_series_b,
                                config_map=CONFIG,
                            )
                        else:
                            _volatility_ok_b = True
                            _volatility_diag_b = {"reason": "invalid_atr_preexisting_block"}
                        _eb_funnel_extras["volatility_gate"] = _volatility_diag_b
                        if not _volatility_ok_b:
                            sig_a["engine_b_error"] = "volatility_gate"
                            _eb_funnel_extras["engine_b_skip_stage"] = "volatility_gate"
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
                        from athena_app.services.engine_b_market_state import engine_b_live_gate_quote

                        try:
                            with r.live_prices_lock:
                                _engine_b_live_prices = dict(r.live_prices)
                            current_price, _live_quote_diag = engine_b_live_gate_quote(
                                pair,
                                _engine_b_live_prices,
                                CONFIG,
                            )
                        except (AttributeError, ValueError) as _live_quote_err:
                            _fresh_reason = str(_live_quote_err)
                            sig_a["engine_b_error"] = _fresh_reason
                            sig_a["engine_b_freshness_blocked"] = True
                            _eb_funnel_extras["engine_b_skip_stage"] = "live_quote_freshness_gate"
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
                            log.warning("[SCAN+B] %s live quote block: %s", pair.get("display", "?"), _fresh_reason)
                            return pair, sig_a, None
                        sig_a["quoteAgeSec"] = _live_quote_diag["ageSec"]
                        sig_a["quoteTimestamp"] = _live_quote_diag["timestamp"]
                        sig_a["engine_b_price_source"] = "fresh_live_quote"
                        _eb_funnel_extras["entry_price"] = current_price

                        if atr and atr > 0:
                            regime_label = r.engine_b_regime_label(zone_candles_b, ptype)
                            _eb_funnel_extras["regime_label"] = regime_label

                            # Snapshots are needed for both A-driven and Engine
                            # B-only paths. `h4_snap` is computed from the real
                            # H4 candle series (not zone_candles_b, which can be
                            # D1 for swing style) so Engine B's H4 ADX matches
                            # the timeframe it claims to be reading from.
                            _sc_d1_snap = {}
                            try:
                                _sc_d1_snap = (
                                    calc_indicators_with_normalized(d1 or [], ptype) or {}
                                ).get("snap") or {}
                            except Exception:
                                pass
                            _sc_h4_snap = _resolve_engine_b_h4_snap(
                                h4_candles=h4 or [],
                                zone_candles=zone_candles_b,
                                asset_type=ptype,
                            )

                            # Engine B owns its direction whenever independent
                            # scanning is enabled. Engine A's direction is used
                            # only after B scoring to calculate alignment; it is
                            # never an input to B's structure/confidence score.
                            _engine_a_direction = (
                                sig_a.get("direction")
                                if not engine_b_scan_only
                                else None
                            )
                            _independent_direction_enabled = bool(
                                CONFIG.get(
                                    "ENGINE_B_SCAN_INDEPENDENT_DIRECTION_ENABLED",
                                    False,
                                )
                            )
                            _b_only_probe_res = None
                            _b_only_probe_conf = None
                            _b_dir = None
                            if engine_b_scan_only or _independent_direction_enabled:
                                _b_dir, _b_only_probe_res, _b_only_probe_conf = (
                                    _engine_b_independent_direction_probe(
                                        pair,
                                        engine=_engine_b,
                                        d1_candles=d1 or [],
                                        h4_candles=h4 or [],
                                        h1_candles=h1 or [],
                                        entry_candles=entry_candles_b,
                                        confidence_entry_candles=active_entry_candles_b,
                                        current_price=current_price,
                                        atr=atr,
                                        regime_label=regime_label,
                                        style_profile=style_profile_b,
                                        resolved_style=resolved_style_b,
                                        asset_type=ptype,
                                        d1_snap=_sc_d1_snap,
                                        h4_snap=_sc_h4_snap,
                                        role_candles=_tf_map_b,
                                        dxy_h4_closes=_dxy_h4_closes_b,
                                    )
                                )
                                if _b_dir not in ("LONG", "SHORT"):
                                    _eb_funnel_extras.setdefault(
                                        "engine_b_skip_stage",
                                        "no_clear_structural_verdict",
                                    )
                            _b_direction_for_analysis = _engine_b_scan_direction_input(
                                _engine_a_direction,
                                _b_dir,
                                engine_b_only=engine_b_scan_only,
                                independent_enabled=_independent_direction_enabled,
                            )
                            if _b_direction_for_analysis in ("LONG", "SHORT"):
                                if engine_b_scan_only:
                                    sig_a["direction"] = _b_direction_for_analysis
                                elif _independent_direction_enabled:
                                    sig_a["engine_b_independent_direction_scan_applied"] = True
                                    sig_a["engine_b_original_direction"] = _engine_a_direction

                            if _b_direction_for_analysis in ("LONG", "SHORT"):
                                if _b_only_probe_res is not None:
                                    # Reuse probe result so we don't re-run
                                    # analyze_structure for the picked direction.
                                    res_b = _b_only_probe_res
                                else:
                                    res_b = _engine_b.set_registry_context(
                                        pair.get("symbol") or pair.get("display")
                                    ).analyze_structure(
                                        d1 or [],
                                        h4 or [],
                                        h1 or [],
                                        current_price,
                                        _b_direction_for_analysis,
                                        atr,
                                        regime_label,
                                        fallback_rr=style_profile_b.get("fallback_rr", 2.0),
                                        asset_type=ptype,
                                        d1_snap=_sc_d1_snap,
                                        h4_snap=_sc_h4_snap,
                                        style=resolved_style_b,
                                        pair=pair,
                                        dxy_h4_closes=_dxy_h4_closes_b,
                                        **engine_b_live_trigger_kwargs(
                                            style_profile_b, _tf_map_b
                                        ),
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
                                            _b_direction_for_analysis,
                                            entry_candles=(
                                                active_entry_candles_b
                                                or entry_candles_b
                                                or zone_candles_b
                                            ),
                                            style_profile=style_profile_b,
                                        )
                                    _engine_b_direction_used = _b_direction_for_analysis
                                    # The selected B direction is now final; A is
                                    # consulted only for alignment below.
                                    b_score = float(conf_b.get("score", 0))
                                    b_max = float(conf_b.get("max_possible", 5))
                                    b_gate_score = float(conf_b.get("gate_score", b_score))
                                    b_gate_max = float(conf_b.get("gate_max_possible", 0.0) or 0.0)
                                    b_gate_pct = conf_b.get("gate_pct")
                                    try:
                                        b_gate_pct_f = float(b_gate_pct)
                                    except (TypeError, ValueError):
                                        b_gate_pct_f = (
                                            round((b_gate_score / b_gate_max) * 100.0, 1)
                                            if b_gate_max > 0
                                            else round(b_score / b_max * 100, 1) if b_max else 0.0
                                        )

                                    sig_a["engine_b_score"] = round(b_score, 2)
                                    sig_a["engine_b_max"] = round(b_max, 1)
                                    sig_a["engine_b_gate_score"] = round(b_gate_score, 2)
                                    sig_a["engine_b_gate_max"] = round(b_gate_max, 2) if b_gate_max else None
                                    # Graded pct (score/max). gate_pct saturates at 100 whenever the
                                    # checklist passes, so it stays a separate diagnostic field.
                                    # engine_b_quality_pct is the only one of the three that spans
                                    # a full range for passing signals — gate_pct is always 100 and
                                    # engine_b_pct floors near 83% (gate_score == gate_max on pass).
                                    sig_a["engine_b_pct"] = round(b_score / b_max * 100, 1) if b_max else 0.0
                                    sig_a["engine_b_gate_pct"] = round(b_gate_pct_f, 1)
                                    sig_a["engine_b_quality_pct"] = conf_b.get("quality_pct")
                                    sig_a["engine_b_direction"] = _engine_b_direction_used
                                    annotate_signal_direction_metadata(
                                        sig_a,
                                        res_b,
                                        _engine_b_direction_used,
                                    )
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
                                    # Blend input is the earned quality layer, never gate_pct
                                    # (100 for any pass) and no longer the total ratio, whose
                                    # lower ~83% is constant for every passing signal because
                                    # gate_score == gate_max whenever `passed` is true. See
                                    # engine_b_quality.engine_b_conviction_norm.
                                    b_norm = engine_b_conviction_norm(conf_b)

                                    # Use same regime-conditional weights as engine_c.
                                    _rl = (regime_label or "").upper()
                                    _w = ENGINE_C_AB_WEIGHTS.get(_rl, ENGINE_C_AB_WEIGHTS.get("TRENDING", {"A": 0.40, "B": 0.60}))
                                    _w_a = float(_w.get("A", 0.40))
                                    _w_b = float(_w.get("B", 0.60))

                                    _engine_b_direction_aligned = bool(
                                        _engine_a_direction in ("LONG", "SHORT")
                                        and _engine_b_direction_used == _engine_a_direction
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
                                    a_norm = float(sig_a.get("scoreNorm", 0))
                                    # A-only fallback: do not cap with Engine C A/B blend weights.
                                    _w_a_fb = _a_only_auto_weight(pair)
                                    sig_a["combinedConviction"] = round(a_norm * _w_a_fb, 4)
                                    sig_a["enginesAligned"] = False
                                    sig_a["engine_b_verdict"] = res_b.get("structural_verdict", "UNCLEAR")
                                from timeframe_policy import attach_timeframe_policy_payload

                                _engine_b_policy = attach_timeframe_policy_payload(
                                    res_b,
                                    pair,
                                    resolved_style_b,
                                    engine="engine_b",
                                    market_states=preloaded_market_state,
                                    speed_state=_speed_state,
                                )
                                # Record consumed-vs-policy timeframes in every
                                # mode. attach_timeframe_policy_payload stamps
                                # the policy TF fields unconditionally, so
                                # skipping this in non-authoritative modes left
                                # signals labelled with timeframes Engine B did
                                # not read (the legacy H1 matrix).
                                _attach_engine_b_timeframe_provenance(
                                    res_b,
                                    _engine_b_policy,
                                    actual_structure_tf=_zone_tf_b,
                                    actual_trigger_tf=_entry_tf_b,
                                    actual_atr_tf=_atr_tf_b,
                                )
                                res_b["signal_price_rr"] = res_b.get("structural_rr")
                                res_b["live_price_rr"] = (
                                    res_b.get("execution_rr2")
                                    or res_b.get("execution_rr")
                                    or conf_b.get("execution_rr2")
                                    or conf_b.get("execution_rr")
                                )
                                res_b["tp_crosses_opposing_structural_zone"] = (
                                    res_b.get("tp1_path_clear") is False
                                )
                                if engine_b_scan_only:
                                    attach_timeframe_policy_payload(
                                        sig_a,
                                        pair,
                                        resolved_style_b,
                                        engine="engine_b",
                                        market_states=preloaded_market_state,
                                        speed_state=_speed_state,
                                    )
                                _eb_snap["res"] = res_b
                                _eb_snap["conf"] = conf_b
                                sig_a["engine_b_status"] = conf_b
                                sig_a["engine_b"] = res_b

                                # Lower-TF shadow diagnostics (M30/M15/M5).
                                # DIAGNOSTIC ONLY — never affects Engine B
                                # direction, confidence, eligibility, ranking,
                                # SL/TP, sizing, or execution. Default OFF; adds
                                # no key when disabled (output stays identical).
                                try:
                                    from lower_tf_shadow import (
                                        compute_lower_tf_shadow,
                                        lower_tf_shadow_enabled,
                                    )

                                    if lower_tf_shadow_enabled("engine_b"):
                                        sig_a["lower_tf_shadow"] = compute_lower_tf_shadow(
                                            pair=pair,
                                            source=pair.get("source"),
                                            fetch_candles=r.fetch_candles,
                                            direction=_engine_b_direction_used,
                                            atr=atr,
                                            component="engine_b",
                                        )
                                except Exception as _ltf_err:
                                    log.debug(
                                        "[SCAN+B] %s lower_tf_shadow build failed: %s",
                                        pair.get("display"),
                                        _ltf_err,
                                    )
                                sig_a["engine_b_aggtrade_required"] = conf_b.get(
                                    "aggtrade_required", res_b.get("aggtrade_required")
                                )
                                sig_a["engine_b_aggtrade_available"] = conf_b.get(
                                    "aggtrade_available", res_b.get("aggtrade_available")
                                )
                                sig_a["engine_b_aggtrade_reason"] = conf_b.get(
                                    "aggtrade_reason", res_b.get("aggtrade_reason")
                                )
                                sig_a["engine_b_orderflow_points"] = conf_b.get(
                                    "aggtrade_orderflow_points"
                                )
                                sig_a["engine_b_data_fidelity"] = (
                                    conf_b.get("engine_b_data_fidelity")
                                    or res_b.get("engine_b_data_fidelity")
                                )
                                # F2: surface Engine B ATR provenance on the overlay
                                # so dashboards / audits can detect stale or mis-sourced
                                # structural ATR without parsing the funnel stream.
                                # Observability-only — does not affect verdicts.
                                try:
                                    _eb_last_ts = None
                                    _eb_age_sec = None
                                    if atr_candles_b:
                                        _last_b = atr_candles_b[-1]
                                        if isinstance(_last_b, dict):
                                            _eb_ts_raw = (
                                                _last_b.get("time")
                                                or _last_b.get("ts")
                                            )
                                            if _eb_ts_raw is not None:
                                                _eb_last_ts = str(_eb_ts_raw)
                                                try:
                                                    if isinstance(
                                                        _eb_ts_raw, (int, float)
                                                    ):
                                                        _eb_dt = datetime.fromtimestamp(
                                                            float(_eb_ts_raw)
                                                            / (1000.0 if float(_eb_ts_raw) > 1e11 else 1.0),
                                                            tz=timezone.utc,
                                                        )
                                                    else:
                                                        _eb_dt = datetime.fromisoformat(
                                                            str(_eb_ts_raw).replace(
                                                                "Z", "+00:00"
                                                            )
                                                        )
                                                    if _eb_dt.tzinfo is None:
                                                        _eb_dt = _eb_dt.replace(
                                                            tzinfo=timezone.utc
                                                        )
                                                    _eb_age_sec = (
                                                        datetime.now(timezone.utc)
                                                        - _eb_dt
                                                    ).total_seconds()
                                                except Exception:
                                                    _eb_age_sec = None
                                    res_b["atrDiagnostics"] = {
                                        "atr_value": (
                                            round(float(atr), 6)
                                            if atr
                                            else None
                                        ),
                                        "atr_tf": _atr_tf_b,
                                        "atr_source": _eb_funnel_extras.get(
                                            "atr_source"
                                        ),
                                        "atr_source_engine": "engine_b",
                                        "atr_candle_last_ts": _eb_last_ts,
                                        "atr_age_seconds": (
                                            round(float(_eb_age_sec), 3)
                                            if _eb_age_sec is not None
                                            else None
                                        ),
                                        "atr_confirmed_only": True,
                                        "bybit_atr_available": _eb_funnel_extras.get(
                                            "bybit_atr_available"
                                        ),
                                    }
                                    try:
                                        from atr_diagnostics import (
                                            evaluate_freshness_from_config,
                                        )
                                        res_b["atrFreshness"] = (
                                            evaluate_freshness_from_config(
                                                res_b["atrDiagnostics"],
                                                CONFIG.get("ATR_FRESHNESS"),
                                            )
                                        )
                                    except Exception:
                                        pass
                                except Exception as _eb_diag_err:
                                    log.debug(
                                        "[SCAN+B] %s atrDiagnostics build failed: %s",
                                        pair.get("display"),
                                        _eb_diag_err,
                                    )

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
                    a_norm = float(sig_a.get("scoreNorm", 0))
                    sig_a["combinedConviction"] = round(
                        a_norm * _a_only_auto_weight(pair), 4
                    )
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
                    pair_result, sig, err = fut.result(timeout=_scan_timeout)
                except TimeoutError:
                    errors.append({"pair": pair["display"], "error": f"Scan timeout ({_scan_timeout}s)"})
                    scan_funnel["errors"] += 1
                    log.error(f"{pair['display']:12s} ERR: Scan timeout ({_scan_timeout}s)")
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
            if _is_engine_a_v3_signal(sig):
                sig["scanThresholdStatic"] = None
                sig["scanQuantileCut"] = None
                sig["scanThresholdEffective"] = None
                sig["threshold"] = None
                sig["liveThreshold"] = None
                sig = annotate_signal_for_scan(
                    sig,
                    pair,
                    0.0,
                    ds_ctx,
                    earnings_ctx,
                    _closed_exchanges,
                    news_ctx,
                )
                tier, tier_reason = _classify_signal(sig, pair)
            else:
                regime = engine_a_regime_label_for_threshold(sig)
                static_threshold = get_score_threshold(pair, regime=regime)
                if r.test_mode():
                    static_threshold = max(0.1, static_threshold * 0.5)
                q_cut = quantile_floors.get(pair.get("type") or "stock")
                effective_threshold = (
                    max(static_threshold, float(q_cut))
                    if q_cut is not None
                    else static_threshold
                )
                sig["scanThresholdStatic"] = static_threshold
                sig["scanQuantileCut"] = q_cut
                sig["scanThresholdEffective"] = effective_threshold
                sig["threshold"] = effective_threshold
                sig["liveThreshold"] = static_threshold
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
                _cs = float(sig.get("confluenceScore", 0))
                if effective_threshold > 0:
                    sig["thresholdProgressPct"] = min(
                        100, max(0, round((_cs / effective_threshold) * 67))
                    )
                    sig["confluencePct"] = sig["thresholdProgressPct"]
                _maxs = float(sig.get("maxScore", 2.0))
                if _maxs > 0:
                    sig["scoreNormPct"] = min(
                        100, max(0, round((_cs / _maxs) * 100))
                    )
                tier, tier_reason = _classify_signal(sig, pair)
            # Engine B scan gates run for all non-B-only rows (v3 does not bypass).
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

                if _is_engine_a_v3_signal(sig):
                    log.info(
                        "%-12s %s V3 %s %s",
                        pair["display"],
                        sig.get("direction"),
                        sig.get("decision"),
                        sig.get("setupId"),
                    )
                else:
                    log.info(
                        f"{pair['display']:12s} {sig['direction']:5s} {sig['confluenceScore']}/{sig.get('maxScore', 3)} [{sig.get('trendState', '?')}]"
                    )

            elif tier == "watchlist":
                watchlist.append(sig)

                scan_funnel["watchlist"] += 1

                if _is_engine_a_v3_signal(sig):
                    log.info(
                        "%-12s WATCH V3 %s :: %s",
                        pair["display"],
                        sig.get("setupId"),
                        tier_reason,
                    )
                else:
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

                if _is_engine_a_v3_signal(sig):
                    log.info(
                        "%-12s SKIP V3 %s :: %s",
                        pair["display"],
                        sig.get("setupId"),
                        tier_reason,
                    )
                else:
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

        results.sort(key=_scan_signal_rank, reverse=True)

        watchlist.sort(key=_scan_signal_rank, reverse=True)

        results = apply_correlation_cap(results)

        watchlist = apply_correlation_cap(watchlist)

        # Macro (FOMC) risk overlay — additive metadata only; never alters score/SL/TP/RR.
        # During an FOMC lockout it sets macro* fields and escalates the existing
        # majorEventRisk.blocksAutoExecution contract for affected candidates.
        try:
            from macro.scan_integration import annotate_signals

            annotate_signals(results)
            annotate_signals(watchlist)
        except Exception as _macro_err:
            log.debug("[MACRO] scan annotation skipped: %s", _macro_err)

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
            "assetClass": _ac,
            "scannedAt": datetime.now(timezone.utc).isoformat(),
            "styleRequested": _requested_style,
            "style": _requested_style,
            "testMode": r.test_mode(),
            "scanMaxWorkersUsed": _max_workers,
            "scanQuantileEnabled": _q_enabled,
            "scanQuantileFloors": quantile_floors,
            "scanQuantileMinSamples": _q_min_n,
            "payloadVersion": "2.0",
            "marketDataRefreshed": bool(refresh_market_data),
            "contract": {
                "engineA": "engine_a_v3",
                "engineB": "naked_structure",
                "engineC": "consensus",
                "engineD": "scalp_vp",
                # NOTE: Engine D is NOT auto-executed. It is available via
                # manual/API endpoints (/api/scalp-scan, /api/scalp-execute).
            },
        }
        try:
            from mt5_executor import apply_mt5_spread_to_sl_scan_gate

            with r.live_prices_lock:
                _spread_gate_live_prices = dict(r.live_prices)
            apply_mt5_spread_to_sl_scan_gate(
                results + watchlist,
                _spread_gate_live_prices,
                config=CONFIG,
            )
        except Exception as _spread_gate_err:
            # The broker executor retains the same final fail-closed gate.
            log.warning(
                "[SCAN][MT5] spread-to-SL classification unavailable: %s",
                _spread_gate_err,
            )
        try:
            from athena_app.diagnostics.engine_b_gate_funnel_persist import (
                scheduled_engine_b_scan_gate_funnel_meta,
            )
            from athena_app.services.scan_completion_hooks import (
                schedule_engine_b_funnel_persist_hook,
            )

            _pair_type_lookup: dict[str, str] = {}
            for _p in candidate_pairs:
                _disp = str(_p.get("display") or _p.get("symbol") or "").strip()
                if not _disp:
                    continue
                _pair_type_lookup[_disp] = str(_p.get("type") or "").strip().lower()
            _persist_meta = scheduled_engine_b_scan_gate_funnel_meta()
            _scan_out.update(_persist_meta)
            if not _persist_meta.get("engine_b_scan_gate_funnel_persist_skipped"):
                schedule_engine_b_funnel_persist_hook(
                    _scan_out,
                    pair_types_by_display=_pair_type_lookup,
                    logger=log,
                )
        except Exception as _persist_merge_err:
            log.warning(
                "[ENGINE_B_FUNNEL_PERSIST] scheduling failed (non-fatal): %s",
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
        try:
            from athena_app.services.scan_completion_hooks import (
                schedule_ase_post_scan_hook,
            )

            _scan_out["ase"] = schedule_ase_post_scan_hook(logger=log)
        except Exception as _ase_err:
            log.warning("[ASE] post-scan scheduling failed (non-fatal): %s", _ase_err)
            _scan_out["ase"] = {"success": False, "error": str(_ase_err)}
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
    """Single-pair production analysis through the initialized runtime."""
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
