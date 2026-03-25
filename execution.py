"""Execution and Engine C HTTP handlers (registered from athena.py)."""

from __future__ import annotations

import json
import sqlite3
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from athena_app.api.routes_execution import normalize_pip_mode
from athena_app.repositories.audit_repo import insert_manual_error
from athena_app.services.candle_service import recompute_levels_for_style
from athena_runtime import executed_signals, rt
from config import _json_safe, scan_candle_limits
from engine_c import compute_consensus
from market_structure import NakedEngine
from scoring import CORR_CLUSTERS, get_pair_score_group


def healthcheck():
    """Lightweight route for modular app wiring smoke-tests."""
    return jsonify({"ok": True, "route": request.path})


def api_quick_execute():
    d = request.json
    if not d or "signal" not in d:
        return jsonify({"error": "Invalid payload"}), 400

    _r = rt()
    sig = d["signal"]
    engine_b = d.get("engine_b") or {}

    pip_mode = normalize_pip_mode(d.get("pip_mode"))
    _sizing_override = float(d.get("sizing_override", 1.0))

    try:
        recomputed = recompute_levels_for_style(
            sig,
            pip_mode,
            resolve_pair_from_signal=_r.resolve_pair_from_signal,
            fetch_candles=_r.fetch_candles,
            calc_indicators_with_normalized=_r.calc_indicators_with_normalized,
            atr_for_levels=_r.atr_for_levels,
            calc_levels=_r.calc_levels,
            config=_r.CONFIG,
        )
        lvl = recomputed["levels"]
        sig["sl"] = lvl["sl"]
        sig["tp1"] = lvl["tp1"]
        sig["tp2"] = lvl["tp2"]
        _r.log.warning(
            f"[QUICK EXEC] {sig.get('pair')}: style={recomputed['pip_mode']}, "
            f"ATR={recomputed['atr']:.6f}, SL={lvl['sl']:.6f}, TP1={lvl['tp1']:.6f}"
        )
    except ValueError as _svc_err:
        if "recommended_stop_loss" in engine_b and engine_b["recommended_stop_loss"]:
            sig["sl"] = engine_b["recommended_stop_loss"]
        if "recommended_take_profit" in engine_b and engine_b["recommended_take_profit"]:
            sig["tp1"] = engine_b["recommended_take_profit"]
            sig["tp2"] = engine_b["recommended_take_profit"]
        _r.log.warning(
            f"[QUICK EXEC] {sig.get('pair')}: style={pip_mode} level service fallback ({_svc_err})"
        )

    is_crypto = sig.get("type") == "crypto"

    try:
        from risk_engine import risk_check

        if is_crypto:
            from bybit_executor import (
                bybit_execute,
                bybit_get_account,
                bybit_get_positions,
                bybit_get_symbol_info,
            )

            account = bybit_get_account()
            if not account:
                return jsonify({"error": "Bybit not connected"}), 400
            pos_result = bybit_get_positions()
            positions = (
                pos_result.get("positions", [])
                if isinstance(pos_result, dict)
                else (pos_result or [])
            )
            symbol_info = bybit_get_symbol_info(sig.get("pair") or sig.get("symbol"))
            executor = bybit_execute
        else:
            from mt5_executor import (
                mt5_execute,
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
            )

            account = mt5_get_account()
            if not account:
                return jsonify({"error": "MT5 not connected"}), 400
            pos_result = mt5_get_positions()
            positions = (
                pos_result.get("positions", [])
                if isinstance(pos_result, dict)
                else (pos_result or [])
            )
            symbol_info = mt5_get_symbol_info(sig.get("display") or sig.get("pair"))
            if not symbol_info or symbol_info.get("error"):
                return jsonify({"error": "Symbol not on broker"}), 400
            executor = mt5_execute

        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=False,
            sizing_override=_sizing_override,
            is_manual_override=True,
        )

        pair_name = sig.get("pair", sig.get("symbol", "N/A"))
        if not approval.approved:
            _r.log.warning(f"[QUICK EXEC] {pair_name} REJECTED: {approval.reason}")
            err_msg = f"Risk Blocked: {approval.reason}"
            if approval.reason == "CORRELATED_CLUSTER_FULL":
                try:
                    _cluster = None
                    _members = set()
                    for _cname, _pairs in CORR_CLUSTERS.items():
                        if pair_name in _pairs:
                            _cluster = _cname
                            _members = set(_pairs)
                            break
                    _corr_count = (
                        sum(1 for _p in positions if _p.get("pair") in _members)
                        if _members
                        else 0
                    )
                    _corr_max = int(_r.CONFIG.get("MAX_CORRELATED_POSITIONS", 2))
                    if _cluster:
                        err_msg = (
                            f"Risk Blocked: {approval.reason} "
                            f"({_cluster} {_corr_count}/{_corr_max})"
                        )
                except Exception:
                    pass
            try:
                insert_manual_error(
                    _r.AUDIT_DB,
                    ts=datetime.now(timezone.utc).isoformat(),
                    pair=pair_name,
                    score=sig.get("confluenceScore", 0),
                    direction=sig.get("direction"),
                    style=pip_mode or "structural",
                    error_tag=approval.reason,
                )
            except Exception as _e:
                _r.log.warning(f"[QUICK EXEC] Failed to log rejection to audit_db: {_e}")
            return jsonify({"error": err_msg}), 400

        result = executor(sig, approval)
        if result.get("success"):
            _b_factors = {}
            if "structural_verdict" in engine_b:
                bos = engine_b.get("bos_data", {})
                sweep = engine_b.get("sweep_data", {})
                seq = engine_b.get("current_swing_sequence", "RANGING")
                engine_b.get("macro_swing_sequence", "RANGING")

                _b_factors["Naked_BOS_Bull"] = 1.0 if bos.get("bos_bull") else 0.0
                _b_factors["Naked_BOS_Bear"] = 1.0 if bos.get("bos_bear") else 0.0
                _b_factors["Naked_Sweep_Bull"] = 1.0 if sweep.get("bull_sweep") else 0.0
                _b_factors["Naked_Sweep_Bear"] = 1.0 if sweep.get("bear_sweep") else 0.0
                _b_factors["Naked_Seq_Bull"] = 1.0 if seq == "HH_HL" else 0.0
                _b_factors["Naked_Seq_Bear"] = 1.0 if seq == "LH_LL" else 0.0

            _factors = {
                "scores": _b_factors,
                "weights": {},
                "disabled": [],
                "regime": engine_b.get("regime", "RANGING"),
            }
            try:
                with sqlite3.connect(_r.AUDIT_DB, timeout=15.0) as con:
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair_name,
                            sig.get("confluenceScore", 0),
                            sig.get("direction"),
                            engine_b.get("current_swing_sequence", "RANGING"),
                            "EXECUTED",
                            engine_b.get("ai_analysis", {}).get("edgeProbability")
                            if isinstance(engine_b.get("ai_analysis"), dict)
                            else None,
                            f"${approval.risk_amount}",
                            pip_mode or "structural",
                            result.get("entryPrice"),
                            sig.get("sl"),
                            sig.get("tp1"),
                            result.get("volume"),
                            engine_b.get("regime", "RANGING"),
                            approval.risk_amount,
                            approval.risk_pct,
                            str(result.get("ticket", "")),
                            result.get("feeCost"),
                            json.dumps(_factors),
                        ),
                    )
                    con.commit()
            except Exception as ae:
                _r.log.warning(f"[QUICK EXEC] Audit DB write failed: {ae}")

            return jsonify(
                {
                    "success": True,
                    "ticket": result.get("ticket"),
                    "message": "Executed instantly!",
                }
            )
        else:
            return jsonify({"error": result.get("error", "Execution failed")}), 400

    except Exception as e:
        _r.log.error(f"quick_execute error: {e}")
        return jsonify({"error": str(e)}), 500


def api_engine_c_scan():
    """Engine C consensus scan — runs Engine A then Engine B on all pairs."""
    _r = rt()
    d = request.get_json() or {}
    asset_class = d.get("assetClass", "").lower()
    requested_style = d.get("style", "auto")

    if not asset_class:
        return jsonify({"error": "assetClass required"}), 400

    candidate_pairs = [
        p
        for p in _r.ALL_PAIRS
        if p.get("type", "").lower() == asset_class
        and p.get("enabled", True)
        and p["display"] not in _r.disabled_pairs
    ]

    if not candidate_pairs:
        return jsonify({"error": f"No enabled pairs for {asset_class}"}), 404

    results = {"aligned": [], "a_only": [], "b_only": [], "conflict": [], "skipped": []}

    btc_bias = _r.current_btc_bias() if asset_class == "crypto" else "neutral"

    engine_b = NakedEngine()
    _EC_PAIR_TIMEOUT = 30  # seconds max per pair

    for pair in candidate_pairs:
        _pair_start = time.time()
        try:
            time.sleep(0.1)

            symbol = pair.get("symbol", pair.get("display"))
            display = pair.get("display", symbol)
            ptype = pair.get("type", "")
            _pair_score_group = get_pair_score_group(pair)

            engine_a_style = _r.resolve_scan_style(
                _r.normalize_style(requested_style), pair
            )
            sig_a = _r.analyze_pair(pair, btc_bias, style=engine_a_style)
            if not sig_a:
                sig_a = {}

            resolved_style_b, style_profile_b = _r.naked_scan_style_profile(
                requested_style, score_group=_pair_score_group
            )
            _forex_struct_tf = _r.CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
            if ptype == "forex" and _forex_struct_tf == "D1" and resolved_style_b == "intraday":
                resolved_style_b, style_profile_b = _r.naked_scan_style_profile(
                    "swing", score_group=_pair_score_group
                )

            _lim = scan_candle_limits()
            d1 = _r.fetch_candles(pair, "D1", _lim["D1"])
            h4 = _r.fetch_candles(pair, "H4", _lim["H4"])
            h1 = _r.fetch_candles(pair, "H1", _lim["H1"])
            if d1 and len(d1) > 1:
                d1 = d1[:-1]
            if h4 and len(h4) > 1:
                h4 = h4[:-1]
            if h1 and len(h1) > 1:
                h1 = h1[:-1]

            # fetch_candles already routes through CandleBuilder (WS) first,
            # then EODHD REST as fallback — no need for separate EODHD calls.

            if (time.time() - _pair_start) > _EC_PAIR_TIMEOUT:
                _r.log.warning(f"[ENGINE C] {display}: timeout after candle fetch ({_EC_PAIR_TIMEOUT}s)")
                results["skipped"].append({"display": display, "reason": "timeout"})
                continue

            if not h4 or len(h4) < 20:
                results["skipped"].append({"display": display, "reason": "insufficient_data"})
                continue

            current_price = float(sig_a.get("price") or h4[-1]["close"])
            atr = float(sig_a.get("atr") or 0.0)
            if not atr or atr <= 0:
                from indicators import calc_atr

                _highs = [float(c["high"]) for c in h4]
                _lows = [float(c["low"]) for c in h4]
                _closes = [float(c["close"]) for c in h4]
                atr_series = calc_atr(_highs, _lows, _closes, 14)
                atr = float(atr_series[-1]) if atr_series else 0.0

            if not atr or atr <= 0:
                results["skipped"].append({"display": display, "reason": "zero_atr"})
                continue

            regime_label = _r.engine_b_regime_label(h4, ptype, sig_a.get("regime"))

            sig_b_best = None
            conf_b_best = None
            b_direction = None

            test_directions = []
            if sig_a.get("direction") in ("LONG", "SHORT"):
                test_directions = [sig_a["direction"]]
            else:
                test_directions = ["LONG", "SHORT"]

            for test_dir in test_directions:
                res_b = engine_b.analyze_structure(
                    d1 or [],
                    h4,
                    h1 or [],
                    current_price,
                    test_dir,
                    atr,
                    regime_label,
                    fallback_rr=style_profile_b.get("fallback_rr", 2.0),
                    asset_type=ptype,
                )
                if res_b.get("structural_verdict") == "CLEAR":
                    conf_b = engine_b.calculate_confidence(
                        res_b,
                        current_price,
                        test_dir,
                        entry_candles=h1 or h4,
                        style_profile=style_profile_b,
                    )
                    b_score = float(conf_b.get("score", 0))
                    if sig_b_best is None or b_score > float(conf_b_best.get("score", 0)):
                        sig_b_best = res_b
                        sig_b_best["direction"] = test_dir
                        conf_b_best = conf_b
                        b_direction = test_dir

            if sig_b_best is None:
                sig_b_best = {"structural_verdict": "ERROR", "direction": None}
            if conf_b_best is None:
                conf_b_best = {"score": 0, "max_possible": 5, "pct": 0}

            consensus = compute_consensus(
                signal_a=sig_a,
                signal_b=sig_b_best,
                confidence_b=conf_b_best,
                asset_type=ptype,
                regime=regime_label,
                entry_price=current_price,
                atr=atr,
            )

            consensus["display"] = display
            consensus["symbol"] = symbol
            consensus["type"] = ptype
            consensus["scoreGroup"] = _pair_score_group
            consensus["style"] = engine_a_style
            consensus["atr"] = round(atr, 6)
            consensus["engine_a_raw"] = {
                "direction": sig_a.get("direction"),
                "score": sig_a.get("confluenceScore"),
                "maxScore": sig_a.get("maxScore"),
                "sl": sig_a.get("sl"),
                "tp1": sig_a.get("tp1"),
                "regime": sig_a.get("regime"),
                "style": sig_a.get("style", sig_a.get("tradeStyle")),
                "cot": sig_a.get("votes", {}).get("derivatives"),
                "carry": sig_a.get("votes", {}).get("carry"),
            }
            consensus["engine_b_raw"] = {
                "direction": b_direction,
                "score": conf_b_best.get("score"),
                "max_possible": conf_b_best.get("max_possible"),
                "sl": sig_b_best.get("recommended_stop_loss"),
                "tp": sig_b_best.get("recommended_take_profit"),
                "sequence": sig_b_best.get("current_swing_sequence"),
                "bos": sig_b_best.get("bos_confirmed"),
                "bos_mtf": sig_b_best.get("bos_mtf_confirmed"),
                "ob_at_zone": sig_b_best.get("ob_at_zone"),
                "choch": sig_b_best.get("choch_confirmed"),
                "trigger": conf_b_best.get("trigger_pattern"),
            }

            verdict = consensus["verdict"]
            if verdict == "ALIGNED":
                _r.insert_shadow_from_engine_c(consensus)
                results["aligned"].append(consensus)
            elif verdict == "A_ONLY":
                results["a_only"].append(consensus)
            elif verdict in (
                "B_ONLY",
                "B_ONLY_SCORED",
                "B_ONLY_VISION_CONFIRMED",
                "B_OVERRIDE_CONFLICT",
            ):
                results["b_only"].append(consensus)
            elif verdict in (
                "DIRECTION_CONFLICT",
                "OPPOSING_HIGH_CONFIDENCE",
                "REGIME_CHANGE_DETECTED",
            ):
                results["conflict"].append(consensus)
            else:
                results["skipped"].append(consensus)

        except Exception as e:
            _r.log.error(f"[ENGINE C] Error on {pair.get('display')}: {e}")
            results["skipped"].append({"display": pair.get("display"), "reason": str(e)})

    results["aligned"].sort(key=lambda x: x.get("conviction", 0), reverse=True)
    results["a_only"].sort(key=lambda x: x.get("conviction", 0), reverse=True)
    results["b_only"].sort(key=lambda x: x.get("conviction", 0), reverse=True)

    total = (
        len(results["aligned"])
        + len(results["a_only"])
        + len(results["b_only"])
        + len(results["conflict"])
    )

    _r.log.warning(
        f"[ENGINE C] Scan complete: {total} signals "
        f"(aligned={len(results['aligned'])}, a_only={len(results['a_only'])}, "
        f"b_only={len(results['b_only'])}, conflict={len(results['conflict'])}, "
        f"skipped={len(results['skipped'])})"
    )

    return jsonify(_json_safe(results))


def api_engine_c_confirm():
    """Apply AI Vision result to Engine C consensus signal."""
    _r = rt()
    try:
        d = request.get_json() or {}
        consensus = d.get("consensus")
        vision = d.get("vision")

        if not consensus:
            return jsonify({"error": "Missing consensus data"}), 400
        if not vision:
            return jsonify({"error": "Missing vision data"}), 400

        from engine_c import apply_vision

        updated = apply_vision(consensus, vision)

        _r.log.warning(
            f"[ENGINE C] Vision confirmed {consensus.get('display', '?')}: "
            f"{updated.get('vision_rating')} → {updated.get('vision_action')} "
            f"→ tier={updated.get('tier')} trade={updated.get('trade')}"
        )

        return jsonify(updated)

    except Exception as e:
        _r.log.error(f"[ENGINE C CONFIRM] Error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def api_execute():
    """Execute a trade signal via MT5 (forex/stocks) or Bybit (crypto)."""
    _r = rt()

    if not _r.CONFIG.get("EXECUTION_ENABLED", False):
        return jsonify(
            {"error": "Execution disabled. Set EXECUTION_ENABLED: true in config.yaml"}
        ), 403

    if _r.kill_switch():
        return jsonify({"error": "Kill-switch active — execution blocked"}), 503

    d = request.json

    if not d or "signal" not in d:
        return jsonify({"error": "Invalid payload: expected {signal: {...}}"}), 400

    sig = d["signal"]

    pair = sig.get("pair", "")

    force = d.get("force", False) and _r.test_mode()

    sig_id = f"{pair}_{sig.get('direction')}_{sig.get('timestamp', '')}"

    if sig_id in executed_signals and not force:
        return jsonify({"error": "DUPLICATE: This signal has already been executed"}), 409

    if force:
        _r.log.warning(
            f"[EXEC] FORCE EXECUTE: {pair} {sig.get('direction')} (test mode, score {sig.get('confluenceScore', '?')})"
        )

    _sig_age = 9999

    _ts_str = sig.get("timestamp", "")

    if _ts_str:
        try:
            _sig_age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(_ts_str.replace("Z", "+00:00"))
            ).total_seconds()

        except Exception:
            _sig_age = 9999

    _max_age = _r.CONFIG.get("SIGNAL_MAX_AGE_SEC", 300)

    if _sig_age > _max_age / 2:
        _pair_obj = next((p for p in _r.ALL_PAIRS if p["display"] == pair), None)

        if _pair_obj:
            try:
                _btc_bias = "neutral"

                _fresh = _r.analyze_pair(
                    _pair_obj, _btc_bias, style=sig.get("style", "swing")
                )

                if _fresh:
                    _orig_dir = sig.get("direction", "")

                    if _fresh["direction"] != _orig_dir:
                        return jsonify(
                            {
                                "error": f"SIGNAL_FLIPPED: {pair} is now {_fresh['direction']} (was {_orig_dir})",
                                "newDirection": _fresh["direction"],
                                "refreshedAt": _fresh["timestamp"],
                            }
                        ), 409

                    sig["price"] = _fresh["price"]

                    sig["ts"] = datetime.now(timezone.utc).isoformat()

                    sig["sl"] = _fresh["sl"]

                    sig["tp1"] = _fresh["tp1"]

                    sig["tp2"] = _fresh["tp2"]

                    sig["atr"] = _fresh.get("atr", sig.get("atr", 0))

                    sig["confluenceScore"] = _fresh["confluenceScore"]

                    sig["maxScore"] = _fresh.get("maxScore", sig.get("maxScore", 3))

                    sig["trendState"] = _fresh.get("trendState", sig.get("trendState"))

                    sig["timestamp"] = _fresh["timestamp"]

                    _r.log.info(
                        f"[EXEC] {pair}: signal refreshed @ {_fresh['price']} (was {_sig_age:.0f}s old)"
                    )

            except Exception as _re:
                _r.log.warning(
                    f"[EXEC] {pair}: live refresh failed ({_re}) — using original signal, refreshing timestamp"
                )

                sig["timestamp"] = datetime.now(timezone.utc).isoformat()

        else:
            sig["timestamp"] = datetime.now(timezone.utc).isoformat()

    try:
        from risk_engine import risk_check

        sig_type = sig.get("type", "")

        is_crypto = sig_type == "crypto"

        if is_crypto:
            from bybit_executor import (
                bybit_execute,
                bybit_get_account,
                bybit_get_positions,
                bybit_get_symbol_info,
            )

            account = bybit_get_account()

            if not account or account.get("error"):
                return jsonify(
                    {
                        "error": "Bybit not connected. Set BYBIT_API_KEY and BYBIT_API_SECRET in .env"
                    }
                ), 503

            _pos_resp = bybit_get_positions()

            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            symbol_info = bybit_get_symbol_info(pair)

            if symbol_info and symbol_info.get("error"):
                symbol_info = None

        else:
            from mt5_executor import (
                mt5_execute,
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
            )

            account = mt5_get_account()

            if not account or account.get("error"):
                return jsonify(
                    {
                        "error": "MT5 not connected. Start MT5 terminal and check credentials."
                    }
                ), 503

            _pos_resp = mt5_get_positions()

            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            symbol_info = mt5_get_symbol_info(pair)

            if not symbol_info or symbol_info.get("error"):
                return jsonify(
                    {
                        "error": f"Symbol '{pair}' not available on your MT5 broker. "
                        f"Check Market Watch or use a broker that offers this instrument."
                    }
                ), 200

        _grade = d.get("grade", "")

        _pos_size_str = d.get("positionSizing", "").lower()

        if "quarter" in _pos_size_str:
            _sizing_override = 0.25

        elif "half" in _pos_size_str:
            _sizing_override = 0.5

        elif "normal" in _pos_size_str or _grade == "A":
            _sizing_override = 0.75

        else:
            _sizing_override = 1.0

        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_r.kill_switch(),
            sizing_override=_sizing_override,
        )

        if not approval.approved:
            _r.log.warning(f"[EXEC] {pair} REJECTED by risk engine: {approval.reason}")

            try:
                with sqlite3.connect(_r.AUDIT_DB, timeout=15.0) as _con:
                    _con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,style,grade,error_tag) VALUES(?,?,?,?,?,?,?)",
                        (
                            datetime.utcnow().isoformat(),
                            pair,
                            sig.get("score"),
                            sig.get("direction"),
                            sig.get("style", "manual"),
                            "MANUAL-ERR",
                            approval.reason,
                        ),
                    )
            except Exception as _e:
                _r.log.warning(f"[EXEC] Failed to log rejection to audit_db: {_e}")

            return jsonify(
                {
                    "error": f"Risk engine rejected: {approval.reason}",
                    "approval": approval.to_dict(),
                }
            ), 200

        if is_crypto:
            result = bybit_execute(sig, approval)

        else:
            result = mt5_execute(sig, approval)

        if result.get("success"):
            executed_signals.add(sig_id)

            try:
                with sqlite3.connect(_r.AUDIT_DB, timeout=15.0) as con:
                    _factors = {
                        "scores": sig.get("factorScores"),
                        "weights": sig.get("factorWeights"),
                        "disabled": sig.get("disabledFactors"),
                        "regime": sig.get("regimeName"),
                    }
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json,"
                        "signal_price_ref,slippage_bps) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair,
                            sig.get("confluenceScore"),
                            sig.get("direction"),
                            sig.get("trendState"),
                            "EXECUTED",
                            None,
                            f"${approval.risk_amount}",
                            "execution",
                            result.get("entryPrice"),
                            sig.get("sl"),
                            sig.get("tp1"),
                            result.get("volume"),
                            sig.get("trendState"),
                            approval.risk_amount,
                            approval.risk_pct,
                            str(result.get("ticket", "")),
                            result.get("feeCost"),
                            json.dumps(_factors),
                            result.get("signalPriceRef"),
                            result.get("slippageBps"),
                        ),
                    )

                    con.commit()

            except Exception as ae:
                _r.log.warning(f"Audit DB write failed: {ae}")

            _r.log.info(
                f"[EXEC] {pair} EXECUTED: ticket={result.get('ticket')}, volume={result.get('volume')}"
            )

        return jsonify(result)

    except Exception as e:
        _r.log.error(f"[EXEC] execution error: {e}")

        return jsonify({"error": "Execution failed — check logs"}), 500


def register_execution_routes(app: Flask) -> None:
    """Attach execution + Engine C handlers (idempotent endpoint names)."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    if "/api/quick-execute" not in rules:
        app.add_url_rule(
            "/api/quick-execute",
            "api_quick_execute",
            api_quick_execute,
            methods=["POST"],
        )
    if "/api/engine-c-scan" not in rules:
        app.add_url_rule(
            "/api/engine-c-scan",
            "api_engine_c_scan",
            api_engine_c_scan,
            methods=["POST"],
        )
    if "/api/engine-c-confirm" not in rules:
        app.add_url_rule(
            "/api/engine-c-confirm",
            "api_engine_c_confirm",
            api_engine_c_confirm,
            methods=["POST"],
        )
    if "/api/execute" not in rules:
        app.add_url_rule("/api/execute", "api_execute", api_execute, methods=["POST"])
