"""Full-scan orchestration and scan-time signal annotation."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from athena_runtime import rt
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
    cfg = config or CONFIG
    return bool(cfg.get("ENGINE_B_SCAN_CONFIRMATION_GATE_ENABLED", True))


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


def _engine_b_level_pair(conf_b: dict | None, res_b: dict | None) -> tuple[float | None, float | None]:
    conf_b = conf_b or {}
    res_b = res_b or {}
    sl = conf_b.get("execution_sl") or res_b.get("execution_sl") or res_b.get("recommended_stop_loss")
    tp = conf_b.get("execution_tp") or res_b.get("execution_tp") or res_b.get("recommended_take_profit")
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


def _apply_engine_b_scan_gate(signal: dict, tier: str, reason: str) -> tuple[str, str]:
    if not _engine_b_scan_confirmation_gate_enabled():
        return tier, reason
    if tier != "trade":
        return tier, reason
    if bool(signal.get("enginesAligned", False)):
        return tier, reason
    detail = signal.get("engine_b_error") or signal.get("engine_b_verdict") or "not_confirmed"
    return "watchlist", f"Engine B confirmation failed ({detail})"


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

    for reason in signal["eventRisk"].get("reasons", []):
        warn = f"EVENT RISK: {reason}"

        if warn not in signal["warnings"]:
            signal["warnings"].append(warn)

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
                raw_candles = {
                    "D1": r.fetch_candles(pair, "D1", _lim["D1"]),
                    "H4": _im_h4 or r.fetch_candles(pair, "H4", _lim["H4"]),
                    "H1": r.fetch_candles(pair, "H1", _lim["H1"]),
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
                    "D1": get_candle_fetch_meta(pair, "D1", _lim["D1"]),
                    "H4": get_candle_fetch_meta(pair, "H4", _lim["H4"]),
                    "H1": get_candle_fetch_meta(pair, "H1", _lim["H1"]),
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

                if not sig_a:
                    return pair, None, None

                ptype = pair.get("type", "")
                try:
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

                    if zone_candles_b and entry_candles_b and atr_candles_b:
                        if engine_b_forex_asian_session_blocks_bar(
                            entry_candles_b, ptype
                        ):
                            return pair, sig_a, None
                        atr = _last_atr_from_candles(atr_candles_b, 14)
                        if (
                            ptype == "crypto"
                            and str(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
                            and hasattr(r, "bybit_atr_for_levels")
                        ):
                            bybit_atr = r.bybit_atr_for_levels(pair, resolved_style_b)
                            if bybit_atr:
                                atr = float(bybit_atr)
                            elif not bool(CONFIG.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
                                atr = 0.0
                                sig_a["engine_b_error"] = "bybit_atr_unavailable"
                        current_price = float(entry_candles_b[-1]["close"])

                        if atr and atr > 0:
                            regime_label = r.engine_b_regime_label(zone_candles_b, ptype)
                            direction = sig_a.get("direction")

                            if direction in ("LONG", "SHORT"):
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
                                )

                                conf_b = None
                                if res_b.get("structural_verdict") == "CLEAR":
                                    conf_b = _engine_b.calculate_confidence(
                                        res_b,
                                        current_price,
                                        direction,
                                        entry_candles=entry_candles_b or zone_candles_b,
                                        style_profile=style_profile_b,
                                    )
                                    b_score = float(conf_b.get("score", 0))
                                    b_max = float(conf_b.get("max_possible", 5))

                                    sig_a["engine_b_score"] = round(b_score, 2)
                                    sig_a["engine_b_max"] = round(b_max, 1)
                                    sig_a["engine_b_pct"] = round(b_score / b_max * 100, 1) if b_max else 0
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

                                    combined_conviction = (a_norm * _w_a) + (b_norm * _w_b)
                                    sig_a["combinedConviction"] = round(combined_conviction, 4)
                                    sig_a["engine_b_scoreNorm"] = round(b_norm, 4)
                                    sig_a["enginesAligned"] = bool(conf_b.get("passed", False))

                                    log.debug(
                                        f"[SCAN+B] {pair.get('display')} A={a_score:.2f}/{a_max} B={b_score:.2f}/{b_max} "
                                        f"regime={_rl} wA={_w_a} wB={_w_b} combined={combined_conviction:.3f}"
                                    )
                                else:
                                    a_max = sig_a.get("maxScore", 3.0)
                                    a_score = sig_a.get("confluenceScore", 0)
                                    a_norm = float(sig_a.get("scoreNorm", 0))
                                    # A-only fallback: use A weight only.
                                    _rl_fb = (regime_label or "").upper()
                                    _w_fb = ENGINE_C_AB_WEIGHTS.get(_rl_fb, ENGINE_C_AB_WEIGHTS.get("TRENDING", {"A": 0.40}))
                                    _w_a_fb = float(_w_fb.get("A", 0.40))
                                    sig_a["combinedConviction"] = round(a_norm * _w_a_fb, 4)
                                    sig_a["enginesAligned"] = False
                                    sig_a["engine_b_verdict"] = res_b.get("structural_verdict", "UNCLEAR")
                                if _threshold_audit_on:
                                    sig_a["_threshold_audit_b_res"] = res_b
                                    sig_a["_threshold_audit_b_conf"] = conf_b
                                    sig_a["_threshold_audit_b_threshold"] = engine_b_min_score_threshold(
                                        style_profile_b,
                                        regime_label,
                                        ptype,
                                    )
                                    sig_a["_threshold_audit_b_style_profile"] = style_profile_b

                except Exception as _b_err:
                    log.debug(f"[SCAN+B] {pair.get('display')} Engine B failed: {_b_err}")
                    a_max = sig_a.get("maxScore", 3.0)
                    a_score = sig_a.get("confluenceScore", 0)
                    a_norm = float(sig_a.get("scoreNorm", 0))
                    sig_a["combinedConviction"] = round(a_norm * 0.6, 4)
                    sig_a["enginesAligned"] = False
                    sig_a["engine_b_error"] = str(_b_err)
                    if _threshold_audit_on:
                        sig_a["_threshold_audit_b_error"] = str(_b_err)

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
        scores_by_type: dict[str, list[float]] = {}
        for pair, sig in buffered_ok:
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
            # Unify threshold resolution — ensures live scan and backtest parity (BUG 7)
            static_threshold = get_score_threshold(pair, is_backtest=False)

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
                skipped.append(
                    {
                        "pair": pair["display"],
                        "reason": tier_reason,
                        "tier": "skip",
                        "diagnostics": sig.get("scanDiagnostics", []),
                    }
                )

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

        return {
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
            },
        }

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
