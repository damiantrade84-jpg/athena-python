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
from indicators import calc_atr, calc_indicators
from intermarket import build_scan_snapshot
from scoring import (
    _build_event_risk,
    _classify_signal,
    _pair_exchange_closed,
    apply_correlation_cap,
    get_score_threshold,
    get_pair_score_group,
)
from market_structure import NakedEngine
from factor_scoring import make_regime_smoothing_context

log = logging.getLogger("sentinel")


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

    signal["scanDiagnostics"] = diagnostics

    for reason in signal["eventRisk"].get("reasons", []):
        warn = f"EVENT RISK: {reason}"

        if warn not in signal["warnings"]:
            signal["warnings"].append(warn)

    return signal


def run_full_scan(style: str = "auto", asset_class: str | None = None) -> dict[str, Any]:
    """Parallel scan of tracked pairs. Optional asset_class filter."""
    r = rt()

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
        candidate_pairs = [p for p in r.ALL_PAIRS if p["display"] not in r.disabled_pairs]

        if _ac:
            candidate_pairs = [p for p in candidate_pairs if p.get("type") == _ac]

        active_pairs = [p for p in candidate_pairs if p.get("enabled", True)]

        results, watchlist, errors, skipped = [], [], [], []

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

        news_ctx = r.fetch_news_context(candidate_pairs)

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
                    preloaded_candles=raw_candles,
                    preloaded_fetch_meta=fetch_meta,
                    intermarket_snapshot=intermarket_snapshot,
                )

                if not sig_a:
                    return pair, None, None

                ptype = pair.get("type", "")
                try:
                    _pair_score_group = get_pair_score_group(pair)
                    resolved_style_b, style_profile_b = r.naked_scan_style_profile(
                        _pair_style, score_group=_pair_score_group
                    )
                    _forex_struct_tf = CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
                    if ptype == "forex" and _forex_struct_tf == "D1" and resolved_style_b == "intraday":
                        resolved_style_b, style_profile_b = r.naked_scan_style_profile(
                            "swing", score_group=_pair_score_group
                        )

                    d1 = raw_candles.get("D1")
                    h4 = raw_candles.get("H4")
                    h1 = raw_candles.get("H1")
                    if d1 and len(d1) > 1:
                        d1 = d1[:-1]
                    if h4 and len(h4) > 1:
                        h4 = h4[:-1]
                    if h1 and len(h1) > 1:
                        h1 = h1[:-1]

                    if h4 and len(h4) >= 20:
                        _highs = [float(c["high"]) for c in h4]
                        _lows = [float(c["low"]) for c in h4]
                        _closes = [float(c["close"]) for c in h4]
                        atr_series = calc_atr(_highs, _lows, _closes, 14)
                        atr = float(atr_series[-1]) if atr_series else 0.0
                        current_price = float(h4[-1]["close"])

                        if atr and atr > 0:
                            regime_label = r.engine_b_regime_label(h4, ptype)
                            direction = sig_a.get("direction")

                            if direction in ("LONG", "SHORT"):
                                res_b = _engine_b.set_registry_context(
                                    pair.get("symbol") or pair.get("display")
                                ).analyze_structure(
                                    d1 or [],
                                    h4,
                                    h1 or [],
                                    current_price,
                                    direction,
                                    atr,
                                    regime_label,
                                    fallback_rr=style_profile_b.get("fallback_rr", 2.0),
                                    asset_type=ptype,
                                )

                                if res_b.get("structural_verdict") == "CLEAR":
                                    conf_b = _engine_b.calculate_confidence(
                                        res_b,
                                        current_price,
                                        direction,
                                        entry_candles=h1 or h4,
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

                                    a_max = sig_a.get("maxScore", 3.0)
                                    a_score = sig_a.get("confluenceScore", 0)
                                    a_norm = float(sig_a.get("scoreNorm", 0))
                                    b_norm = min(b_score / b_max, 1.0) if b_max else 0

                                    combined_conviction = (a_norm * 0.6) + (b_norm * 0.4)
                                    sig_a["combinedConviction"] = round(combined_conviction, 4)
                                    sig_a["engine_b_scoreNorm"] = round(b_norm, 4)
                                    sig_a["enginesAligned"] = True

                                    log.debug(
                                        f"[SCAN+B] {pair.get('display')} A={a_score:.2f}/{a_max} B={b_score:.2f}/{b_max} "
                                        f"combined={combined_conviction:.3f}"
                                    )
                                else:
                                    a_max = sig_a.get("maxScore", 3.0)
                                    a_score = sig_a.get("confluenceScore", 0)
                                    a_norm = float(sig_a.get("scoreNorm", 0))
                                    sig_a["combinedConviction"] = round(a_norm * 0.6, 4)
                                    sig_a["enginesAligned"] = False
                                    sig_a["engine_b_verdict"] = res_b.get("structural_verdict", "UNCLEAR")

                except Exception as _b_err:
                    log.debug(f"[SCAN+B] {pair.get('display')} Engine B failed: {_b_err}")
                    a_max = sig_a.get("maxScore", 3.0)
                    a_score = sig_a.get("confluenceScore", 0)
                    a_norm = float(sig_a.get("scoreNorm", 0))
                    sig_a["combinedConviction"] = round(a_norm * 0.6, 4)
                    sig_a["enginesAligned"] = False
                    sig_a["engine_b_error"] = str(_b_err)

                return pair, sig_a, None

            except Exception as e:
                return pair, None, str(e)

        buffered_ok: list[tuple[Any, dict]] = []

        with ThreadPoolExecutor(max_workers=_max_workers) as pool:
            futures = {pool.submit(_analyse, pair): pair for pair in candidate_pairs}

            for fut in as_completed(futures):
                pair, sig, err = fut.result()

                if err:
                    errors.append({"pair": pair["display"], "error": err})

                    scan_funnel["errors"] += 1

                    log.error(f"{pair['display']:12s} ERR: {err}")

                    continue

                if isinstance(sig, dict) and sig.get("skipCode") == "rate_limited":
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

            # analyze_pair anchored confluencePct to static threshold only — re-anchor to scan gate
            if effective_threshold and effective_threshold > 0:
                _cs = float(sig.get("confluenceScore", 0))
                sig["confluencePct"] = min(
                    100, max(0, round((_cs / effective_threshold) * 67))
                )

            tier, tier_reason = _classify_signal(sig, pair)

            sig["signalTier"] = tier

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
