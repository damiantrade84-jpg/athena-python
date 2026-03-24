"""Full-scan orchestration and scan-time signal annotation."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from athena_runtime import rt
from config import CONFIG
from data_feeds import http_requests
from indicators import calc_atr, calc_indicators
from scoring import (
    _build_event_risk,
    _classify_signal,
    _pair_exchange_closed,
    apply_correlation_cap,
    get_min_confluence_threshold,
    get_pair_score_group,
)
from market_structure import NakedEngine

log = logging.getLogger("sentinel")


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

        _engine_b = NakedEngine()

        def _analyse(pair):
            try:
                _pair_style = r.resolve_scan_style(_requested_style, pair)
                sig_a = r.analyze_pair(pair, btc_bias, style=_pair_style)

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

                    d1 = r.fetch_candles(pair, "D1", CONFIG.get("D1_CANDLES", 250))
                    h4 = r.fetch_candles(pair, "H4", CONFIG.get("H4_CANDLES", 250))
                    h1 = r.fetch_candles(pair, "H1", CONFIG.get("H1_CANDLES", 250))

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
                                res_b = _engine_b.analyze_structure(
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

                                    a_max = sig_a.get("maxScore", 3.0)
                                    a_score = sig_a.get("confluenceScore", 0)
                                    a_norm = min(a_score / a_max, 1.0) if a_max else 0
                                    b_norm = min(b_score / b_max, 1.0) if b_max else 0

                                    combined_conviction = (a_norm * 0.6) + (b_norm * 0.4)
                                    sig_a["combinedConviction"] = round(combined_conviction, 4)
                                    sig_a["enginesAligned"] = True

                                    log.debug(
                                        f"[SCAN+B] {pair.get('display')} A={a_score:.2f}/{a_max} B={b_score:.2f}/{b_max} "
                                        f"combined={combined_conviction:.3f}"
                                    )
                                else:
                                    a_max = sig_a.get("maxScore", 3.0)
                                    a_score = sig_a.get("confluenceScore", 0)
                                    a_norm = min(a_score / a_max, 1.0) if a_max else 0
                                    sig_a["combinedConviction"] = round(a_norm * 0.6, 4)
                                    sig_a["enginesAligned"] = False
                                    sig_a["engine_b_verdict"] = res_b.get("structural_verdict", "UNCLEAR")

                except Exception as _b_err:
                    log.debug(f"[SCAN+B] {pair.get('display')} Engine B failed: {_b_err}")
                    a_max = sig_a.get("maxScore", 3.0)
                    a_score = sig_a.get("confluenceScore", 0)
                    a_norm = min(a_score / a_max, 1.0) if a_max else 0
                    sig_a["combinedConviction"] = round(a_norm * 0.6, 4)
                    sig_a["enginesAligned"] = False

                return pair, sig_a, None

            except Exception as e:
                return pair, None, str(e)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(_analyse, pair): pair for pair in candidate_pairs}

            for fut in as_completed(futures):
                pair, sig, err = fut.result()

                if err:
                    errors.append({"pair": pair["display"], "error": err})

                    scan_funnel["errors"] += 1

                    log.error(f"{pair['display']:12s} ERR: {err}")

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

                threshold = get_min_confluence_threshold(pair)

                if r.test_mode():
                    threshold = max(0.1, threshold * 0.5)

                sig = annotate_signal_for_scan(
                    sig,
                    pair,
                    threshold,
                    ds_ctx,
                    earnings_ctx,
                    _closed_exchanges,
                    news_ctx,
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
) -> dict[str, Any] | None:
    """Single-pair analysis; implementation remains on the monolith until further split."""
    try:
        return rt().analyze_pair(pair, btc_bias, style=style, use_naked_engine=use_naked_engine)
    except RuntimeError:
        from athena_legacy import load as _load_legacy

        return _load_legacy().analyze_pair(
            pair, btc_bias, style=style, use_naked_engine=use_naked_engine
        )
