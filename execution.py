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
from execution_lifecycle import run_managed_execution
from indicators import calc_indicators_with_normalized
from market_structure import NakedEngine, engine_b_confidence_passes
from guardian import pre_trade_check as _guardian_pre_trade
from scoring import CORR_CLUSTERS, get_pair_score_group


def healthcheck():
    """Lightweight route for modular app wiring smoke-tests."""
    return jsonify({"ok": True, "route": request.path})


def _engine_c_accepts_engine_b(
    confidence_b: dict, style_profile: dict, regime_label: str, pair_type: str
) -> tuple[bool, float]:
    """Reuse Engine B's standalone confidence gate inside Engine C.

    This keeps the Engine C scan from surfacing B-only structures that the
    dedicated naked scan would reject for the same pair/style/regime.
    """
    gate_ok, scaled_min = engine_b_confidence_passes(
        confidence_b,
        style_profile,
        regime_label,
        pair_type,
    )
    return bool(gate_ok), float(scaled_min)


def _engine_c_best_score(confidence_b: dict | None) -> float:
    """Rank Engine B candidates by checklist score."""
    if not isinstance(confidence_b, dict):
        return 0.0
    try:
        return float(confidence_b.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _audit_engine_from_signal(sig: dict) -> str:
    engine = str((sig or {}).get("engine") or "").strip().lower()
    if engine in ("engine_a", "engine_b", "engine_c", "scalp"):
        return engine
    if bool((sig or {}).get("is_naked")) or (sig or {}).get("naked_data"):
        return "engine_b"
    style = str((sig or {}).get("style") or "").strip().lower()
    if style == "scalp":
        return "scalp"
    return "engine_a"


def _apply_level_override(sig: dict, override: dict) -> str | None:
    if not isinstance(override, dict):
        return "override payload must be an object"

    try:
        sl = float(override.get("sl"))
        tp1 = float(override.get("tp1"))
    except (TypeError, ValueError):
        return "override requires numeric sl and tp1"

    try:
        tp2 = float(override.get("tp2", tp1))
    except (TypeError, ValueError):
        tp2 = tp1

    try:
        anchor_entry = float(override.get("anchor_entry") or 0)
    except (TypeError, ValueError):
        anchor_entry = 0.0

    direction = str(sig.get("direction") or "").upper()
    try:
        entry = float(sig.get("price") or sig.get("livePrice") or 0)
    except (TypeError, ValueError):
        entry = 0.0

    if direction not in ("LONG", "SHORT"):
        return "signal direction missing"
    if entry <= 0:
        return "signal entry price missing"
    if sl <= 0 or tp1 <= 0:
        return "override prices must be positive"

    sl_offset = None
    tp1_offset = None
    tp2_offset = None
    if anchor_entry > 0:
        sl_offset = sl - anchor_entry
        tp1_offset = tp1 - anchor_entry
        tp2_offset = tp2 - anchor_entry
        sl = entry + sl_offset
        tp1 = entry + tp1_offset
        tp2 = entry + tp2_offset

    if direction == "LONG" and not (sl < entry < tp1):
        return "LONG override must satisfy SL < entry < TP"
    if direction == "SHORT" and not (sl > entry > tp1):
        return "SHORT override must satisfy SL > entry > TP"

    sig["sl"] = sl
    sig["tp1"] = tp1
    sig["tp2"] = tp2
    if override.get("style"):
        sig["style"] = normalize_pip_mode(override.get("style")) or sig.get("style")
    sig["level_source"] = str(override.get("source") or "manual_override")
    sig["level_override"] = {
        "style": sig.get("style"),
        "source": sig.get("level_source"),
        "model": override.get("model"),
        "tf": override.get("tf"),
    }
    if anchor_entry > 0 and sl_offset is not None and tp1_offset is not None:
        sig["level_override"].update(
            {
                "entry_rebase": True,
                "anchor_entry": anchor_entry,
                "sl_offset": sl_offset,
                "tp1_offset": tp1_offset,
                "tp2_offset": tp2_offset if tp2_offset is not None else tp1_offset,
            }
        )
    return None


def api_quick_execute():
    d = request.json
    if not d or "signal" not in d:
        return jsonify({"error": "Invalid payload"}), 400

    _r = rt()
    sig = d["signal"]
    engine_b = d.get("engine_b") or {}
    level_override = d.get("level_override") or sig.get("level_override")

    pip_mode = normalize_pip_mode(d.get("pip_mode"))
    try:
        _sizing_override = max(0.25, min(1.0, float(d.get("sizing_override", 1.0))))
    except (TypeError, ValueError):
        _sizing_override = 1.0
    sig["style"] = pip_mode or sig.get("style", "swing")

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
        pair = sig.get("pair", "")
        _pair_obj = next((p for p in _r.ALL_PAIRS if p["display"] == pair), None)
        if _pair_obj:
            try:
                _fresh = _r.analyze_pair(_pair_obj, "neutral", style=sig["style"])
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
                    sig["atr"] = _fresh.get("atr", sig.get("atr", 0))
                    sig["confluenceScore"] = _fresh.get(
                        "confluenceScore", sig.get("confluenceScore")
                    )
                    sig["maxScore"] = _fresh.get("maxScore", sig.get("maxScore", 3))
                    sig["trendState"] = _fresh.get(
                        "trendState", sig.get("trendState")
                    )
                    sig["timestamp"] = _fresh["timestamp"]
            except Exception as _fresh_err:
                _r.log.warning(
                    f"[QUICK EXEC] {pair}: refresh failed ({_fresh_err}) - continuing with original direction"
                )

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

    if level_override:
        _override_err = _apply_level_override(sig, level_override)
        if _override_err:
            return jsonify({"error": f"Invalid AI level override: {_override_err}"}), 400
        _r.log.warning(
            f"[QUICK EXEC] {sig.get('pair')}: applied {sig.get('level_source')} levels "
            f"for style={sig.get('style')} SL={sig.get('sl')} TP1={sig.get('tp1')}"
        )

    is_crypto = sig.get("type") == "crypto"

    try:
        from risk_engine import risk_check

        if is_crypto:
            from bybit_executor import (
                bybit_get_account,
                bybit_get_positions,
                bybit_get_symbol_info,
            )

            account = bybit_get_account()
            if not account:
                return jsonify({"error": "Bybit not connected"}), 400
            pos_result = bybit_get_positions()
            if isinstance(pos_result, dict) and pos_result.get("error"):
                return jsonify({"error": "Positions unavailable — cannot verify exposure"}), 503
            positions = (
                pos_result.get("positions", [])
                if isinstance(pos_result, dict)
                else (pos_result or [])
            )
            symbol_info = bybit_get_symbol_info(sig.get("pair") or sig.get("symbol"))
            _exec_venue = "bybit"
        else:
            from mt5_executor import (
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
            )

            account = mt5_get_account()
            if not account:
                return jsonify({"error": "MT5 not connected"}), 400
            pos_result = mt5_get_positions()
            if isinstance(pos_result, dict) and pos_result.get("error"):
                return jsonify({"error": "Positions unavailable — cannot verify exposure"}), 503
            positions = (
                pos_result.get("positions", [])
                if isinstance(pos_result, dict)
                else (pos_result or [])
            )
            symbol_info = mt5_get_symbol_info(sig.get("display") or sig.get("pair"))
            if not symbol_info or symbol_info.get("error"):
                return jsonify({"error": "Symbol not on broker"}), 400
            _exec_venue = "mt5"

        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_r.kill_switch(),
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

        _ptc_ok, _ptc_reason = _guardian_pre_trade(sig, positions, account, pos_result)
        if not _ptc_ok:
            _r.log.warning(f"[QUICK EXEC] {pair_name} GUARDIAN BLOCKED: {_ptc_reason}")
            return jsonify({"error": f"Guardian: {_ptc_reason}"}), 400

        result = run_managed_execution(_exec_venue, sig, approval)
        if result.get("success"):
            _b_factors = {}
            if "structural_verdict" in engine_b:
                bos = engine_b.get("bos_data", {})
                sweep = engine_b.get("sweep_data", {})
                seq = engine_b.get("current_swing_sequence", "RANGING")

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
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json,"
                        "max_score,score_pct) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair_name,
                            engine_b.get("score", 0),
                            "engine_b",
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
                            engine_b.get("max_possible"),
                            engine_b.get("pct"),
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
            symbol = pair.get("symbol", pair.get("display"))
            display = pair.get("display", symbol)
            ptype = pair.get("type", "")
            _pair_score_group = get_pair_score_group(pair)

            engine_a_style = _r.resolve_scan_style(
                _r.normalize_style(requested_style), pair
            )

            resolved_style_b, style_profile_b = _r.naked_scan_style_profile(
                requested_style, score_group=_pair_score_group
            )
            _forex_struct_tf = _r.CONFIG.get("ENGINE_B_FOREX_STRUCTURE_TF", "D1").upper()
            if ptype == "forex" and _forex_struct_tf == "D1" and resolved_style_b == "intraday":
                resolved_style_b, style_profile_b = _r.naked_scan_style_profile(
                    "swing", score_group=_pair_score_group
                )

            _zone_tf = str(style_profile_b.get("zone_tf", "H4")).upper()
            _entry_tf = str(style_profile_b.get("entry_tf", "H1")).upper()
            _atr_tf = str(style_profile_b.get("atr_tf", _zone_tf)).upper()
            _lim = scan_candle_limits()

            # Fetch candles ONCE — share between Engine A (full) and Engine B (last bar dropped).
            # Eliminates double-fetching and ensures both engines score identical data.
            _tf_map_full: dict[str, list] = {}
            _tf_map: dict[str, list] = {}
            for tf in {_zone_tf, _entry_tf, _atr_tf, "D1"}:
                limit = _lim.get(tf, _lim.get("H4", 0))
                raw = _r.fetch_candles(pair, tf, limit)
                _tf_map_full[tf] = raw or []
                if raw and len(raw) > 1:
                    _tf_map[tf] = raw[:-1]
                else:
                    _tf_map[tf] = raw or []
            d1 = _tf_map.get("D1", [])
            zone_candles = _tf_map.get(_zone_tf, [])
            entry_candles = _tf_map.get(_entry_tf, [])
            atr_candles = _tf_map.get(_atr_tf, zone_candles)

            # Engine A: pass full (undropped) candles so analyze_pair() doesn't re-fetch
            _preloaded_for_a = {
                "D1": _tf_map_full.get("D1"),
                "H4": _tf_map_full.get(_zone_tf),
                "H1": _tf_map_full.get(_entry_tf),
            }
            sig_a = _r.analyze_pair(pair, btc_bias, style=engine_a_style,
                                    preloaded_candles=_preloaded_for_a)
            if not sig_a:
                sig_a = {}

            # fetch_candles already routes through CandleBuilder (WS) first,
            # then EODHD REST as fallback — no need for separate EODHD calls.

            if (time.time() - _pair_start) > _EC_PAIR_TIMEOUT:
                _r.log.warning(f"[ENGINE C] {display}: timeout after candle fetch ({_EC_PAIR_TIMEOUT}s)")
                results["skipped"].append({"display": display, "reason": "timeout"})
                continue

            if len(zone_candles) < 10 or len(entry_candles) < 10:
                results["skipped"].append({"display": display, "reason": "insufficient_data"})
                continue

            current_price = float(sig_a.get("price") or entry_candles[-1]["close"])
            atr = float(sig_a.get("atr") or 0.0)
            if not atr or atr <= 0:
                from indicators import calc_atr

                _highs = [float(c["high"]) for c in atr_candles]
                _lows = [float(c["low"]) for c in atr_candles]
                _closes = [float(c["close"]) for c in atr_candles]
                atr_series = calc_atr(_highs, _lows, _closes, 14)
                atr = float(atr_series[-1]) if atr_series else 0.0

            if not atr or atr <= 0:
                results["skipped"].append({"display": display, "reason": "zero_atr"})
                continue

            _ec_d1_snap = {}
            _ec_h4_snap = {}
            try:
                _ec_d1_snap = (calc_indicators_with_normalized(d1, ptype) or {}).get("snap") or {}
                _ec_h4_snap = (
                    calc_indicators_with_normalized(zone_candles, ptype) or {}
                ).get("snap") or {}
            except Exception:
                pass

            regime_label = _r.engine_b_regime_label(
                zone_candles, ptype, sig_a.get("regime")
            )

            sig_b_best = None
            conf_b_best = None
            b_direction = None
            min_score_scaled_best = None

            sig_b_candidate_best = None
            conf_b_candidate_best = None
            b_candidate_direction = None
            min_score_scaled_candidate = None
            gate_ok_candidate = False

            for test_dir in ("LONG", "SHORT"):
                res_b = engine_b.set_registry_context(
                    pair.get("symbol") or display
                ).analyze_structure(
                    d1 or [],
                    zone_candles,
                    entry_candles or [],
                    current_price,
                    test_dir,
                    atr,
                    regime_label,
                    fallback_rr=style_profile_b.get("fallback_rr", 2.0),
                    asset_type=ptype,
                    d1_snap=_ec_d1_snap,
                    h4_snap=_ec_h4_snap,
                )
                if res_b.get("structural_verdict") == "CLEAR":
                    conf_b = engine_b.calculate_confidence(
                        res_b,
                        current_price,
                        test_dir,
                        entry_candles=entry_candles or zone_candles,
                        style_profile=style_profile_b,
                    )
                    gate_ok, _scaled_min = _engine_c_accepts_engine_b(
                        conf_b,
                        style_profile_b,
                        regime_label,
                        ptype,
                    )
                    b_score = _engine_c_best_score(conf_b)
                    if (
                        sig_b_candidate_best is None
                        or b_score > _engine_c_best_score(conf_b_candidate_best)
                    ):
                        sig_b_candidate_best = dict(res_b)
                        sig_b_candidate_best["direction"] = test_dir
                        conf_b_candidate_best = dict(conf_b)
                        b_candidate_direction = test_dir
                        min_score_scaled_candidate = float(_scaled_min)
                        gate_ok_candidate = bool(gate_ok)
                    if not gate_ok:
                        continue
                    if sig_b_best is None or b_score > _engine_c_best_score(conf_b_best):
                        sig_b_best = dict(res_b)
                        sig_b_best["direction"] = test_dir
                        conf_b_best = dict(conf_b)
                        b_direction = test_dir
                        min_score_scaled_best = float(_scaled_min)

            raw_sig_b = sig_b_best if sig_b_best is not None else sig_b_candidate_best
            raw_conf_b = conf_b_best if conf_b_best is not None else conf_b_candidate_best
            raw_b_direction = b_direction if b_direction is not None else b_candidate_direction
            raw_min_score_scaled = (
                min_score_scaled_best
                if sig_b_best is not None
                else min_score_scaled_candidate
            )
            raw_gate_ok = bool(sig_b_best is not None or gate_ok_candidate)

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
                "direction": raw_b_direction,
                "score": (raw_conf_b or {}).get("score"),
                "max_possible": (raw_conf_b or {}).get("max_possible"),
                "sl": (raw_sig_b or {}).get("recommended_stop_loss"),
                "tp": (raw_sig_b or {}).get("recommended_take_profit"),
                "sequence": (raw_sig_b or {}).get("current_swing_sequence"),
                "bos": (raw_sig_b or {}).get("bos_confirmed"),
                "bos_mtf": (raw_sig_b or {}).get("bos_mtf_confirmed"),
                "ob_at_zone": (raw_sig_b or {}).get("ob_at_zone"),
                "choch": (raw_sig_b or {}).get("choch_confirmed"),
                "trigger": (raw_conf_b or {}).get("trigger_pattern"),
                "style": resolved_style_b,
            }
            consensus["engine_b_status"] = {
                "eligible": bool(sig_b_best.get("direction")),
                "has_candidate": bool(raw_b_direction),
                "gate_ok": raw_gate_ok,
                "checklist_passed": bool((raw_conf_b or {}).get("passed")),
                "min_score_scaled": raw_min_score_scaled,
                "reason_codes": (
                    ((raw_conf_b or {}).get("engine_b_diagnostics") or {}).get(
                        "reason_codes"
                    )
                    or []
                ),
                "resolved_style": resolved_style_b,
                "zone_tf": _zone_tf,
                "entry_tf": _entry_tf,
                "atr_tf": _atr_tf,
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
    level_override = d.get("level_override") or sig.get("level_override")

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

    if level_override:
        _override_err = _apply_level_override(sig, level_override)
        if _override_err:
            return jsonify({"error": f"Invalid AI level override: {_override_err}"}), 400
        _r.log.warning(
            f"[EXEC] {pair}: applied {sig.get('level_source')} levels "
            f"for style={sig.get('style')} SL={sig.get('sl')} TP1={sig.get('tp1')}"
        )

    try:
        from risk_engine import risk_check

        sig_type = sig.get("type", "")

        is_crypto = sig_type == "crypto"

        if is_crypto:
            from bybit_executor import (
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
            if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
                return jsonify({"error": "Positions unavailable — cannot verify exposure"}), 503

            positions = (
                _pos_resp.get("positions", [])
                if isinstance(_pos_resp, dict)
                else (_pos_resp or [])
            )

            symbol_info = bybit_get_symbol_info(pair)

            if symbol_info and symbol_info.get("error"):
                symbol_info = None

            _exec_venue = "bybit"

        else:
            from mt5_executor import (
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
            if isinstance(_pos_resp, dict) and _pos_resp.get("error"):
                return jsonify({"error": "Positions unavailable — cannot verify exposure"}), 503

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

            _exec_venue = "mt5"

        _raw_so = d.get("sizing_override")
        _sizing_override = None
        if _raw_so is not None:
            try:
                _sizing_override = max(0.25, min(1.0, float(_raw_so)))
            except (TypeError, ValueError):
                _sizing_override = None

        _grade = d.get("grade", "")

        if _sizing_override is None:
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
                            datetime.now(timezone.utc).isoformat(),
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

        _ptc_ok, _ptc_reason = _guardian_pre_trade(sig, positions, account, _pos_resp)
        if not _ptc_ok:
            _r.log.warning(f"[EXEC] {pair} GUARDIAN BLOCKED: {_ptc_reason}")
            return jsonify({"error": f"Guardian: {_ptc_reason}"}), 400

        result = run_managed_execution(_exec_venue, sig, approval)

        if result.get("success"):
            executed_signals.add(sig_id)

            try:
                with sqlite3.connect(_r.AUDIT_DB, timeout=15.0) as con:
                    _factors = {
                        "scores": sig.get("factor_scores"),
                        "weights": sig.get("factor_weights"),
                        "disabled": sig.get("disabledFactors"),
                        "regime": sig.get("regimeName"),
                        "lifecycle_state": sig.get("engine_b_lifecycle_state", sig.get("lifecycle_state", "unknown")),
                        "lifecycle_reason": sig.get("engine_b_lifecycle_reason", sig.get("lifecycle_reason", "")),
                    }
                    _audit_engine = _audit_engine_from_signal(sig)
                    _eng_b_data = sig.get("engine_b") or sig.get("naked_data") or {}
                    
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json,"
                        "signal_price_ref,slippage_bps,max_score,score_pct) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            pair,
                            sig.get("confluenceScore"),
                            _audit_engine,
                            sig.get("direction"),
                            sig.get("trendState"),
                            "EXECUTED",
                            None,
                            f"${approval.risk_amount}",
                            sig.get("style") or "execution",
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
                            _eng_b_data.get("max_possible") if _audit_engine == "engine_b" else sig.get("maxScore"),
                            _eng_b_data.get("pct") if _audit_engine == "engine_b" else None,
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
    if "/api/scalp-pairs" not in rules:
        app.add_url_rule(
            "/api/scalp-pairs",
            "api_scalp_pairs",
            api_scalp_pairs,
            methods=["GET"],
        )
    if "/api/scalp-scan" not in rules:
        app.add_url_rule(
            "/api/scalp-scan",
            "api_scalp_scan",
            api_scalp_scan,
            methods=["POST"],
        )
    if "/api/scalp-execute" not in rules:
        app.add_url_rule(
            "/api/scalp-execute",
            "api_scalp_execute",
            api_scalp_execute,
            methods=["POST"],
        )


def api_scalp_pairs():
    """List Engine D scan universe (mirrors athena when registered)."""
    from scalp_engine import get_scalp_pairs

    try:
        pairs = get_scalp_pairs(rt().ACTIVE_PAIRS)
        return jsonify({"pairs": pairs, "count": len(pairs)})
    except Exception as e:
        rt().log.error(f"[SCALP API] scalp-pairs error: {e}")
        return jsonify({"error": str(e)}), 500


def api_scalp_scan():
    """Engine D scalp scan — M15 zones + M5 entry triggers (MT5 non-crypto, Binance/crypto path for USDT pairs)."""
    from scalp_engine import get_scalp_pairs, run_scalp_scan
    
    d = request.get_json() or {}
    requested_pairs = d.get("pairs")
    
    if not requested_pairs or requested_pairs == "all":
        pairs = get_scalp_pairs(rt().ACTIVE_PAIRS)
    elif isinstance(requested_pairs, list):
        pairs = requested_pairs
    else:
        return jsonify({"error": "Invalid pairs list"}), 400
        
    try:
        results = run_scalp_scan(pairs)
        out = dict(results)
        out["pairs"] = list(pairs)
        out["pair_count"] = len(pairs)
        return jsonify(out)
    except Exception as e:
        rt().log.error(f"[SCALP API] Scan error: {e}")
        return jsonify({"error": str(e)}), 500


def api_scalp_execute():
    """Execute a scalp signal after passing risk check (MT5 or Bybit for crypto)."""
    _r = rt()
    d = request.get_json() or {}
    sig = d.get("signal")
    try:
        _sizing_override = max(0.25, min(1.0, float(d.get("sizing_override", 1.0))))
    except (TypeError, ValueError):
        _sizing_override = 1.0

    if not sig:
        return jsonify({"error": "Missing signal data"}), 400
        
    try:
        from risk_engine import risk_check

        is_crypto = (sig.get("type") == "crypto")
        pair_key = sig.get("pair") or sig.get("display") or ""

        if is_crypto:
            from bybit_executor import (
                bybit_get_account,
                bybit_get_positions,
                bybit_get_symbol_info,
            )

            account = bybit_get_account()
            if not account or account.get("error"):
                return jsonify({"error": "Bybit not connected"}), 503

            pos_result = bybit_get_positions()
            if isinstance(pos_result, dict) and pos_result.get("error"):
                return jsonify({"error": "Positions unavailable — cannot verify exposure"}), 503
            positions = pos_result.get("positions", []) if isinstance(pos_result, dict) else (pos_result or [])

            symbol_info = bybit_get_symbol_info(pair_key)
            if not symbol_info or symbol_info.get("error"):
                return jsonify({"error": f"Symbol {pair_key} not available on Bybit"}), 400
            _exec_venue = "bybit"
        else:
            from mt5_executor import (
                mt5_get_account,
                mt5_get_positions,
                mt5_get_symbol_info,
            )

            account = mt5_get_account()
            if not account or account.get("error"):
                return jsonify({"error": "MT5 not connected"}), 503
            
            pos_result = mt5_get_positions()
            if isinstance(pos_result, dict) and pos_result.get("error"):
                return jsonify({"error": "Positions unavailable — cannot verify exposure"}), 503
            positions = pos_result.get("positions", []) if isinstance(pos_result, dict) else (pos_result or [])

            symbol_info = mt5_get_symbol_info(pair_key)
            if not symbol_info or symbol_info.get("error"):
                return jsonify({"error": f"Symbol {pair_key} not available on MT5"}), 400
            _exec_venue = "mt5"
            
        # Format signal for risk engine
        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_r.kill_switch(),
            sizing_override=_sizing_override,
            is_manual_override=True
        )
        
        if not approval.approved:
            _r.log.warning(f"[SCALP EXEC] {sig.get('pair')} REJECTED: {approval.reason}")
            return jsonify({"error": f"Risk Blocked: {approval.reason}"}), 400

        _ptc_ok, _ptc_reason = _guardian_pre_trade(sig, positions, account, pos_result)
        if not _ptc_ok:
            _r.log.warning(f"[SCALP EXEC] {sig.get('pair')} GUARDIAN BLOCKED: {_ptc_reason}")
            return jsonify({"error": f"Guardian: {_ptc_reason}"}), 400

        result = run_managed_execution(_exec_venue, sig, approval)
        if result.get("success"):
            # Log to audit_db
            try:
                with sqlite3.connect(_r.AUDIT_DB, timeout=15.0) as con:
                    con.execute(
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,grade,risk,style,entry_price,sl,tp,volume,ticket,risk_amount,risk_pct) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            sig["pair"],
                            sig.get("ai_score", 0),
                            "scalp",
                            sig.get("direction"),
                            "SCALP",
                            f"${approval.risk_amount}",
                            "scalp",
                            result.get("entryPrice"),
                            sig.get("sl"),
                            sig.get("tp1"),
                            result.get("volume"),
                            str(result.get("ticket", "")),
                            approval.risk_amount,
                            approval.risk_pct
                        )
                    )
            except Exception as ae:
                _r.log.warning(f"[SCALP API] Audit log failed: {ae}")
                
            return jsonify({
                "success": True, 
                "ticket": result.get("ticket"),
                "message": "Scalp order filled!"
            })
        else:
            return jsonify({"error": result.get("error", "Execution failed")}), 400
            
    except Exception as e:
        _r.log.error(f"[SCALP API] Execute error: {e}")
        return jsonify({"error": str(e)}), 500
