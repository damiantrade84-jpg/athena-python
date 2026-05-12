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
from candles_cache import extract_candles, get_candle_fetch_meta
from config import _json_safe, scan_candle_limits
from engine_c import compute_consensus, normalise_engine_a
from execution_lifecycle import run_managed_execution
from factor_scoring import make_regime_smoothing_context
from indicators import calc_atr, calc_indicators_with_normalized
from intermarket import build_scan_snapshot
from market_structure import NakedEngine, engine_b_confidence_passes
from guardian import pre_trade_check as _guardian_pre_trade
from scoring import CORR_CLUSTERS, get_pair_score_group
from sqlite_instrumentation import (
    timed_sqlite_connect,
    timed_sqlite_commit,
    timed_sqlite_execute_write,
    timed_sqlite_executemany_write,
)


def healthcheck():
    """Lightweight route for modular app wiring smoke-tests."""
    return jsonify({"ok": True, "route": request.path})


def _execution_audit_legs(result: dict, approval) -> list[dict]:
    """Return one audit leg per broker position while preserving legacy single-fill shape."""
    raw_legs = result.get("legs") if isinstance(result.get("legs"), list) else []
    if not raw_legs:
        return [
            {
                "ticket": result.get("ticket"),
                "entryPrice": result.get("entryPrice"),
                "tp": None,
                "volume": result.get("volume"),
                "riskAmount": approval.risk_amount,
                "riskPct": approval.risk_pct,
            }
        ]

    total_volume = sum(float(leg.get("volume") or 0.0) for leg in raw_legs)
    audit_legs = []
    for leg in raw_legs:
        leg_volume = float(leg.get("volume") or 0.0)
        ratio = (leg_volume / total_volume) if total_volume > 0 else 0.0
        audit_legs.append(
            {
                "ticket": leg.get("ticket"),
                "entryPrice": leg.get("entryPrice", result.get("entryPrice")),
                "tp": leg.get("tp"),
                "volume": leg_volume,
                "riskAmount": approval.risk_amount * ratio,
                "riskPct": approval.risk_pct * ratio,
            }
        )
    return audit_legs


def _execution_failure_reason(result: object, default: str = "Execution failed") -> str:
    """Extract a useful execution failure reason from broker/lifecycle output."""
    if isinstance(result, dict):
        for key in ("error", "detail", "message", "comment", "reason"):
            val = result.get(key)
            if val:
                return str(val)
        lifecycle = result.get("lifecycle")
        if isinstance(lifecycle, dict):
            phases = lifecycle.get("phases")
            if isinstance(phases, list):
                for phase in reversed(phases):
                    if not isinstance(phase, dict):
                        continue
                    phase_result = phase.get("result")
                    if isinstance(phase_result, dict):
                        nested = _execution_failure_reason(phase_result, "")
                        if nested:
                            return nested
                    if phase.get("success") is False:
                        name = phase.get("name") or "execution"
                        return f"{default}: {name} returned no error detail"
        retcode = result.get("retcode")
        if retcode is not None:
            return f"{default}: broker retcode {retcode}"
    return default


def _log_execution_failure(log, prefix: str, pair: str, venue: str, result: object) -> str:
    """Log failed execution output and return the reason sent back to operators."""
    reason = _execution_failure_reason(result)
    try:
        safe_result = _json_safe(result)
    except Exception:
        safe_result = repr(result)
    try:
        log.warning(
            f"[{prefix}] {pair or '?'} {venue or '?'} FAILED: {reason} | result={safe_result}"
        )
    except Exception:
        pass
    return reason


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


def _engine_b_atr_for_scan_levels(
    sig_a: dict | None,
    atr_candles: list,
    atr_tf: str,
    pair: dict,
    resolved_style: str,
    runtime,
) -> tuple[float, str]:
    """Resolve the ATR Engine B should use for Engine C scan execution levels."""
    tf = str(atr_tf or "H4").upper()
    atr = 0.0
    if tf == "H4":
        try:
            atr = float((sig_a or {}).get("atr") or 0.0)
        except (TypeError, ValueError):
            atr = 0.0

    if not atr or atr <= 0:
        try:
            _highs = [float(c["high"]) for c in atr_candles]
            _lows = [float(c["low"]) for c in atr_candles]
            _closes = [float(c["close"]) for c in atr_candles]
            atr_series = calc_atr(_highs, _lows, _closes, 14)
            atr = float(atr_series[-1]) if atr_series else 0.0
        except (KeyError, TypeError, ValueError):
            atr = 0.0

    cfg = getattr(runtime, "CONFIG", {}) or {}
    if (
        str((pair or {}).get("type") or "").lower() == "crypto"
        and str(cfg.get("ENGINE_B_CRYPTO_LEVELS_FEED", "bybit")).lower() == "bybit"
        and hasattr(runtime, "bybit_atr_for_levels")
    ):
        bybit_atr = runtime.bybit_atr_for_levels(pair, resolved_style)
        if bybit_atr:
            return float(bybit_atr), "bybit"
        if not bool(cfg.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
            return 0.0, "bybit_unavailable"

    return float(atr or 0.0), tf


def _engine_c_best_score(confidence_b: dict | None) -> float:
    """Rank Engine B candidates by checklist score."""
    if not isinstance(confidence_b, dict):
        return 0.0
    try:
        return float(confidence_b.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _engine_c_skip_entry(
    display: str | None,
    reason: str,
    *,
    code: str | None = None,
    detail: str | None = None,
) -> dict:
    entry = {"display": display, "reason": reason}
    if code:
        entry["code"] = code
        entry["skipCode"] = code
    if detail:
        entry["detail"] = detail
        entry["skipDetail"] = detail
    return entry


def _audit_engine_from_signal(sig: dict) -> str:
    engine = str((sig or {}).get("engine") or "").strip().lower()
    if engine in ("engine_a", "engine_b", "engine_c", "scalp"):
        return engine
    if bool((sig or {}).get("is_naked")) or (sig or {}).get("naked_data"):
        return "engine_b"
    return "engine_a"


def _quick_audit_context(sig: dict, engine_b: dict | None) -> dict:
    """Build audit fields for quick execute without treating compare data as source."""
    sig = sig or {}
    engine_b = engine_b or {}
    audit_engine = _audit_engine_from_signal(sig)

    b_factors = {}
    if "structural_verdict" in engine_b:
        bos = engine_b.get("bos_data", {}) or {}
        sweep = engine_b.get("sweep_data", {}) or {}
        seq = engine_b.get("current_swing_sequence", "RANGING")

        b_factors["Naked_BOS_Bull"] = 1.0 if bos.get("bos_bull") else 0.0
        b_factors["Naked_BOS_Bear"] = 1.0 if bos.get("bos_bear") else 0.0
        b_factors["Naked_Sweep_Bull"] = 1.0 if sweep.get("bull_sweep") else 0.0
        b_factors["Naked_Sweep_Bear"] = 1.0 if sweep.get("bear_sweep") else 0.0
        b_factors["Naked_Seq_Bull"] = 1.0 if seq == "HH_HL" else 0.0
        b_factors["Naked_Seq_Bear"] = 1.0 if seq == "LH_LL" else 0.0

    if audit_engine == "engine_b":
        score = engine_b.get("score", sig.get("confluenceScore", sig.get("score", 0)))
        max_score = engine_b.get("max_possible")
        score_pct = engine_b.get("pct")
        trend = engine_b.get("current_swing_sequence", sig.get("trendState", "RANGING"))
        regime = engine_b.get("regime", sig.get("regimeName", "RANGING"))
        edge_prob = (
            engine_b.get("ai_analysis", {}).get("edgeProbability")
            if isinstance(engine_b.get("ai_analysis"), dict)
            else None
        )
        factors = {
            "scores": b_factors,
            "weights": {},
            "disabled": [],
            "regime": regime,
        }
    else:
        score = sig.get("confluenceScore", sig.get("score", 0))
        max_score = sig.get("maxScore")
        score_pct = None
        try:
            if score is not None and max_score:
                score_pct = (float(score) / float(max_score)) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            score_pct = None
        trend = sig.get("trendState")
        regime = sig.get("regimeName") or sig.get("regime") or trend
        edge_prob = None
        factors = {
            "scores": sig.get("factor_scores"),
            "weights": sig.get("factor_weights"),
            "disabled": sig.get("disabledFactors"),
            "regime": regime,
        }
        if b_factors or engine_b:
            factors["compare_engine_b"] = {
                "score": engine_b.get("score"),
                "max_possible": engine_b.get("max_possible"),
                "pct": engine_b.get("pct"),
                "regime": engine_b.get("regime"),
                "current_swing_sequence": engine_b.get("current_swing_sequence"),
                "scores": b_factors,
            }

    return {
        "engine": audit_engine,
        "score": score,
        "trend": trend,
        "edge_prob": edge_prob,
        "regime": regime,
        "factors": factors,
        "max_score": max_score,
        "score_pct": score_pct,
    }


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


def _is_structural_engine_b_execution(sig: dict, engine_b: dict | None = None) -> bool:
    """True when execution should apply Engine B level / stale-B refresh semantics.

    Full-scan Engine A rows carry ``enginesAligned`` from the A+B merge; that metadata alone
    must not trigger Engine B execution gates.
    """
    sig = sig or {}
    engine_b = engine_b or {}
    if sig.get("is_naked"):
        return True
    if sig.get("naked_data"):
        return True
    eb = sig.get("engine_b")
    if isinstance(eb, dict) and eb:
        return True
    if engine_b:
        return True
    return False


def _signal_has_engine_b_context(sig: dict, engine_b: dict | None = None) -> bool:
    return _is_structural_engine_b_execution(sig, engine_b)


def _engine_b_context_confirmed(sig: dict, engine_b: dict | None = None) -> bool:
    sig = sig or {}
    engine_b = engine_b or {}
    if not _is_structural_engine_b_execution(sig, engine_b):
        return True
    if "enginesAligned" in sig:
        return bool(sig.get("enginesAligned"))
    nested = sig.get("engine_b")
    if not isinstance(nested, dict):
        nested = sig.get("naked_data")
    if isinstance(nested, dict) and "passed" in nested:
        return bool(nested.get("passed"))
    if "passed" in engine_b:
        return bool(engine_b.get("passed"))
    if _signal_has_engine_b_context(sig, engine_b):
        return False
    return False


def _extract_engine_b_execution_levels(
    sig: dict,
    engine_b: dict | None = None,
) -> dict | None:
    sig = sig or {}
    candidates = [
        sig.get("naked_data"),
        sig.get("engine_b"),
        engine_b,
        sig,
    ]
    for source in candidates:
        if not isinstance(source, dict):
            continue
        sl = (
            source.get("execution_sl")
            or source.get("engine_b_execution_sl")
            or source.get("recommended_stop_loss")
            or (source.get("sl") if source is sig and _signal_has_engine_b_context(sig, engine_b) else None)
        )
        tp = (
            source.get("execution_tp")
            or source.get("engine_b_execution_tp")
            or source.get("recommended_take_profit")
            or (source.get("tp1") if source is sig and _signal_has_engine_b_context(sig, engine_b) else None)
        )
        try:
            sl_f = float(sl)
            tp_f = float(tp)
        except (TypeError, ValueError):
            continue
        if sl_f > 0 and tp_f > 0:
            return {"sl": sl_f, "tp1": tp_f, "tp2": tp_f}
    return None


def _maybe_prefetch_execution_candle_fetch_meta(sig: dict, *, _r) -> None:
    """Refresh ``signal['candleFetchMeta']`` from ``fetch_candles`` + terminal metadata.

    Prevents guardian/risk staleness mismatches after long UI waits (e.g. AI review) without
    a full analyze refresh. Uses the same limits as Engine A scans.

    Controlled by CONFIG ``QUICK_EXEC_PREFETCH_CANDLE_META`` (default safe: off in python
    defaults; repo ``config.yaml`` sets true).

    Ops: exotic FX (e.g. USD/MXN) still blocks if MT5 has no recent bars — verify Market Watch /
    sessions when ``STALE_CANDLES`` persists after prefetch.
    """
    try:
        if not bool((_r.CONFIG or {}).get("QUICK_EXEC_PREFETCH_CANDLE_META", False)):
            return
        pair_disp = str(sig.get("pair") or sig.get("display") or sig.get("symbol") or "").strip()
        if not pair_disp:
            return
        pair_obj = next((p for p in (_r.ALL_PAIRS or []) if str(p.get("display", "")) == pair_disp), None)
        if not pair_obj:
            return
        limits = scan_candle_limits()
        fetch = getattr(_r, "fetch_candles", None)
        if not callable(fetch):
            return
        for tf in ("H1", "H4", "D1"):
            lim = int(limits[tf])
            fetch(pair_obj, tf, lim)
        d1_m = get_candle_fetch_meta(pair_obj, "D1", limits["D1"])
        h4_m = get_candle_fetch_meta(pair_obj, "H4", limits["H4"])
        h1_m = get_candle_fetch_meta(pair_obj, "H1", limits["H1"])
        sig["candleFetchMeta"] = {
            "D1": d1_m if isinstance(d1_m, dict) else {},
            "H4": h4_m if isinstance(h4_m, dict) else {},
            "H1": h1_m if isinstance(h1_m, dict) else {},
            "pairSource": pair_obj.get("source"),
        }
        _r.log.info(
            "[EXEC] candleFetchMeta prefetched H1=%s H4=%s D1=%s pair=%s source=%s",
            limits.get("H1"),
            limits.get("H4"),
            limits.get("D1"),
            pair_disp,
            pair_obj.get("source"),
        )
    except Exception as exc:
        try:
            _r.log.warning("[EXEC] candleFetchMeta prefetch failed: %s", exc)
        except Exception:
            pass


def _hydrate_execution_candle_quality(sig: dict, *, _r) -> None:
    """Before risk gate: refresh candles and rebuild freshness/consistency/exec gate fields.

    UI/client payloads often embed stale ``candleFreshness`` while ``candleFetchMeta`` alone was
    prefetched elsewhere; risk uses ``evaluate_execution_data_freshness``. When enabled, this pulls
    H1/H4/D1 via ``fetch_candles`` (scan limits), repopulates ``candleFetchMeta``, ``candleFreshness``
    (if ``CANDLE_FRESHNESS_ENABLED``), ``candleConsistency``, and ``dataFreshness``. If every fetch
    is empty/failed we fall back to :func:`_maybe_prefetch_execution_candle_fetch_meta` so a prior
    ``analyze_pair`` refresh path is not poisoned.

    Controlled by CONFIG ``EXECUTION_HYDRATE_CANDLE_QUALITY`` (default True in ``config.py``).
    """
    cfg = getattr(_r, "CONFIG", None) or {}
    if not bool(cfg.get("EXECUTION_HYDRATE_CANDLE_QUALITY", True)):
        _maybe_prefetch_execution_candle_fetch_meta(sig, _r=_r)
        return

    pair_disp = str(sig.get("pair") or sig.get("display") or sig.get("symbol") or "").strip()
    if not pair_disp:
        _maybe_prefetch_execution_candle_fetch_meta(sig, _r=_r)
        return

    pair_obj = next((p for p in (_r.ALL_PAIRS or []) if str(p.get("display", "")) == pair_disp), None)
    if not pair_obj:
        _maybe_prefetch_execution_candle_fetch_meta(sig, _r=_r)
        return

    fetch = getattr(_r, "fetch_candles", None)
    if not callable(fetch):
        _maybe_prefetch_execution_candle_fetch_meta(sig, _r=_r)
        return

    try:
        limits = scan_candle_limits()
        candles: dict[str, list] = {"H1": [], "H4": [], "D1": []}
        for tf in ("H1", "H4", "D1"):
            lim = int(limits[str(tf)])
            raw = fetch(pair_obj, str(tf), lim)
            extracted = extract_candles(raw)
            candles[str(tf)] = list(extracted or [])

        if not (
            candles["H1"]
            and candles["H4"]
            and candles["D1"]
        ):
            try:
                _r.log.info(
                    "[EXEC] hydrate skipped (incomplete candle fetch); pair=%s h1=%d h4=%d d1=%d",
                    pair_disp,
                    len(candles["H1"]),
                    len(candles["H4"]),
                    len(candles["D1"]),
                )
            except Exception:
                pass
            _maybe_prefetch_execution_candle_fetch_meta(sig, _r=_r)
            return

        d1_m = get_candle_fetch_meta(pair_obj, "D1", limits["D1"])
        h4_m = get_candle_fetch_meta(pair_obj, "H4", limits["H4"])
        h1_m = get_candle_fetch_meta(pair_obj, "H1", limits["H1"])
        sig["candleFetchMeta"] = {
            "D1": d1_m if isinstance(d1_m, dict) else {},
            "H4": h4_m if isinstance(h4_m, dict) else {},
            "H1": h1_m if isinstance(h1_m, dict) else {},
            "pairSource": pair_obj.get("source"),
        }

        from athena_app.services.data_freshness import (
            check_live_candle_consistency,
            evaluate_execution_data_freshness,
        )
        from athena_app.services.market_state import (
            candle_freshness_diagnostic,
            market_state_offset_hours,
            split_market_state,
        )

        time_now = datetime.now(timezone.utc).timestamp()

        candle_consistency: dict = {}
        states_by_tf: dict[str, dict] = {}

        for tf_u in ("H4", "H1", "D1"):
            tf_candles = list(candles.get(tf_u) or [])
            offset_h = market_state_offset_hours(pair_obj, tf_u)
            state = split_market_state(
                tf_candles,
                tf_u,
                pair_obj.get("display") or pair_obj.get("symbol") or "",
                time_now=time_now,
                offset_hours=offset_h,
            )
            states_by_tf[tf_u] = state
            if pair_obj.get("source") == "mt5" and pair_obj.get("type") == "forex":
                engine_a_input = list(state.get("confirmed") or [])
            else:
                engine_a_input = list(state.get("confirmed") or [])
                if state.get("forming"):
                    engine_a_input.append(state["forming"])
            engine_b_input = list(state.get("confirmed") or [])
            scanner_input = list(engine_a_input)

            consistency_paths = {
                "raw_provider": tf_candles,
                "market_state": state,
                "engine_a": engine_a_input,
                "engine_b": engine_b_input,
                "scanner": scanner_input,
                "compare": scanner_input,
            }
            cache_meta = sig["candleFetchMeta"].get(tf_u)
            if isinstance(cache_meta, dict) and cache_meta:
                consistency_paths["cache"] = cache_meta

            consistency_result = check_live_candle_consistency(
                pair_obj,
                tf_u,
                consistency_paths,
                time_now=time_now,
            )
            if consistency_result:
                candle_consistency[tf_u] = consistency_result

        sig["candleConsistency"] = candle_consistency

        if bool(cfg.get("CANDLE_FRESHNESS_ENABLED", True)):
            cf: dict = {}
            for tf_u in ("D1", "H4", "H1"):
                state = states_by_tf[tf_u]
                confirmed = list(state.get("confirmed") or [])
                forming = state.get("forming")
                series_diag = confirmed + ([forming] if forming else [])
                cf[tf_u] = candle_freshness_diagnostic(
                    pair_obj,
                    tf_u,
                    series_diag,
                    time_now=time_now,
                    source=pair_obj.get("source"),
                )
            sig["candleFreshness"] = cf

        freshness_eval = evaluate_execution_data_freshness(sig, cfg)
        sig["dataFreshness"] = freshness_eval
        if bool(cfg.get("SIGNAL_EXECUTABLE_FALSE_WHEN_FRESHNESS_BLOCKS", True)):
            if isinstance(freshness_eval, dict) and not freshness_eval.get("allowed"):
                sig["executable"] = False

        try:
            _r.log.info(
                "[EXEC] candle quality hydrated pair=%s h1=%d h4=%d d1=%d allowed=%s",
                pair_disp,
                len(candles["H1"]),
                len(candles["H4"]),
                len(candles["D1"]),
                (sig.get("dataFreshness") or {}).get("allowed"),
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            _r.log.warning("[EXEC] candle quality hydrate failed: %s", exc)
        except Exception:
            pass
        _maybe_prefetch_execution_candle_fetch_meta(sig, _r=_r)


def _apply_engine_b_execution_levels(sig: dict, engine_b: dict | None = None) -> bool:
    levels = _extract_engine_b_execution_levels(sig, engine_b)
    if not levels:
        return False
    sig["sl"] = levels["sl"]
    sig["tp1"] = levels["tp1"]
    sig["tp2"] = levels["tp2"]
    sig["level_source"] = "engine_b_execution"
    return True


def api_quick_execute():
    _r = rt()
    # ── Execution safety guards (must match api_execute) ─────────────────
    if not _r.CONFIG.get("EXECUTION_ENABLED", False):
        return jsonify(
            {"error": "Execution disabled. Set EXECUTION_ENABLED: true in config.yaml"}
        ), 403
    if _r.kill_switch():
        return jsonify({"error": "Kill-switch active — execution blocked"}), 503
    # ─────────────────────────────────────────────────────────────────────
    d = request.json
    if not d or "signal" not in d:
        return jsonify({"error": "Invalid payload"}), 400

    sig = d["signal"]
    _quick_pair = sig.get("pair") or sig.get("display") or sig.get("symbol") or ""
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
    _has_engine_b_context = _signal_has_engine_b_context(sig, engine_b)
    
    _missing_price = False
    try:
        if not sig.get("price") or float(sig.get("price")) <= 0:
            _missing_price = True
    except (TypeError, ValueError):
        _missing_price = True

    if (_sig_age > _max_age / 2 or _missing_price) and _has_engine_b_context:
        return jsonify(
            {
                "error": "ENGINE_B_REFRESH_REQUIRED: stale or incomplete Engine B signal must be refreshed by Engine B before execution",
                "pair": sig.get("pair"),
            }
        ), 409
    if _has_engine_b_context and not _engine_b_context_confirmed(sig, engine_b):
        return jsonify(
            {
                "error": "ENGINE_B_NOT_CONFIRMED: Engine B confirmation failed before execution",
                "pair": sig.get("pair"),
            }
        ), 409

    if _sig_age > _max_age / 2 or _missing_price:
        pair = sig.get("pair", "")
        _pair_obj = next((p for p in _r.ALL_PAIRS if p["display"] == pair), None)
        if _pair_obj:
            try:
                _fresh = _r.analyze_pair(_pair_obj, "neutral", style=sig["style"])
                if _fresh:
                    _orig_dir = sig.get("direction", "")
                    _is_manual_override = bool(d.get("is_manual_override"))
                    if not _is_manual_override and _fresh["direction"] != _orig_dir:
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
                    for _fresh_key in (
                        "candleFetchMeta",
                        "candleFreshness",
                        "candleConsistency",
                        "dataFreshness",
                    ):
                        if _fresh.get(_fresh_key) is not None:
                            sig[_fresh_key] = _fresh[_fresh_key]
            except Exception as _fresh_err:
                _r.log.warning(
                    f"[QUICK EXEC] {pair}: refresh failed ({_fresh_err}) - continuing with original direction"
                )

    if _has_engine_b_context and _apply_engine_b_execution_levels(sig, engine_b):
        _r.log.warning(
            f"[QUICK EXEC] {sig.get('pair')}: preserved Engine B execution levels "
            f"SL={sig.get('sl')} TP1={sig.get('tp1')}"
        )
    else:
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
                get_pair_level_atr_class=getattr(_r, "get_pair_level_atr_class", None),
                bybit_atr_for_levels=getattr(_r, "bybit_atr_for_levels", None),
            )
            lvl = recomputed["levels"]
            sig["sl"] = lvl["sl"]
            sig["tp1"] = lvl["tp1"]
            sig["tp2"] = lvl["tp2"]
            _r.log.warning(
                f"[QUICK EXEC] {sig.get('pair')}: style={recomputed['pip_mode']}, "
                f"ATR={recomputed['atr']:.6f}, SL={lvl['sl']:.6f}, TP1={lvl['tp1']:.6f}"
            )
        except (ValueError, TypeError) as _svc_err:
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
            if not account or account.get("error"):
                _msg = account.get("detail", "Bybit not connected") if account else "Bybit not connected"
                return jsonify({"error": _msg}), 400
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
            if not account or account.get("error"):
                _msg = account.get("detail", "MT5 not connected") if account else "MT5 not connected"
                return jsonify({"error": _msg}), 400
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

        _hydrate_execution_candle_quality(sig, _r=_r)

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
                    entry_price=sig.get("price"),
                    sl=sig.get("sl"),
                    tp=sig.get("tp1"),
                    volume=approval.volume,
                    risk_amount=approval.risk_amount,
                    risk_pct=approval.risk_pct,
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
            _audit = _quick_audit_context(sig, engine_b)
            _audit_ok = True
            try:
                _audit_ts = datetime.now(timezone.utc).isoformat()
                _audit_rows = []
                for _leg in _execution_audit_legs(result, approval):
                    _audit_rows.append((
                        _audit_ts,
                        pair_name,
                        _audit["score"],
                        _audit["engine"],
                        sig.get("direction"),
                        _audit["trend"],
                        "EXECUTED",
                        _audit["edge_prob"],
                        f"${_leg['riskAmount']}",
                        pip_mode or "structural",
                        _leg.get("entryPrice"),
                        sig.get("sl"),
                        _leg.get("tp") or sig.get("tp1"),
                        _leg.get("volume"),
                        _audit["regime"],
                        _leg.get("riskAmount"),
                        _leg.get("riskPct"),
                        str(_leg.get("ticket", "")),
                        result.get("feeCost"),
                        json.dumps(_audit["factors"]),
                        _audit["max_score"],
                        _audit["score_pct"],
                    ))
                with timed_sqlite_connect(
                    _r.AUDIT_DB, timeout=15.0, label="quick_execute.audit_success.connect"
                ) as con:
                    timed_sqlite_executemany_write(
                        con,
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json,"
                        "max_score,score_pct) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        _audit_rows,
                        label="quick_execute.audit_success.insert",
                    )
                    timed_sqlite_commit(con, label="quick_execute.audit_success.commit")
            except Exception as ae:
                _r.log.warning(f"[QUICK EXEC] Audit DB write failed: {ae}")
                _audit_ok = False

            if not _audit_ok:
                result["success"] = False
                result["error"] = "AUDIT_PERSISTENCE_FAILED_AFTER_FILL"
                return jsonify({"error": result["error"], "execution": _json_safe(result)}), 500

            return jsonify(
                {
                    "success": True,
                    "ticket": result.get("ticket"),
                    "message": "Executed instantly!",
                }
            )
        else:
            err = _log_execution_failure(
                _r.log, "QUICK EXEC", pair_name, _exec_venue, result
            )
            if isinstance(result, dict):
                result.setdefault("error", err)
            return jsonify({"error": err, "execution": _json_safe(result)}), 400

    except Exception as e:
        _r.log.exception(f"[QUICK EXEC] {_quick_pair or '?'} error: {e}")
        return jsonify({"error": str(e)}), 500


def api_engine_c_scan():
    """Engine C consensus scan — runs Engine A then Engine B on all pairs."""
    _r = rt()
    d = request.get_json() or {}
    asset_class = str(d.get("assetClass", "") or "").lower()
    requested_style = d.get("style", "auto")
    scan_all = asset_class in ("", "all")

    candidate_pairs = []
    for p in _r.ALL_PAIRS:
        ptype = str(p.get("type", "")).lower()
        if not p.get("enabled", True):
            continue
        if p.get("display") in _r.disabled_pairs:
            continue
        if not scan_all and ptype != asset_class:
            continue
        candidate_pairs.append(p)

    _pf_raw = d.get("pairs") if isinstance(d.get("pairs"), list) else None
    if _pf_raw is None and isinstance(d.get("symbols"), list):
        _pf_raw = d["symbols"]
    if isinstance(_pf_raw, list) and _pf_raw:

        def _pair_tag_variants(raw: object) -> set[str]:
            s = str(raw or "").strip().upper()
            if not s:
                return set()
            compact = s.replace("/", "").replace(" ", "").replace("-", "")
            return {s, compact}

        def _pair_matches_filter(p: dict) -> bool:
            for raw in _pf_raw:
                variants = _pair_tag_variants(raw)
                for key in ("display", "symbol"):
                    pv = str(p.get(key) or "").strip().upper()
                    if not pv:
                        continue
                    pvars = _pair_tag_variants(pv)
                    if variants.intersection(pvars):
                        return True
            return False

        candidate_pairs = [p for p in candidate_pairs if _pair_matches_filter(p)]

    if not candidate_pairs:
        label = "all enabled pairs" if scan_all else f"enabled pairs for {asset_class}"
        return jsonify({"error": f"No {label}"}), 404

    results = {"aligned": [], "a_only": [], "b_only": [], "conflict": [], "skipped": []}

    _scan_limits = scan_candle_limits()
    intermarket_snapshot = None
    _im_cfg = _r.CONFIG.get("INTERMARKET_CONFIRMATION", {}) or {}
    if bool(_im_cfg.get("enabled")) and bool(_im_cfg.get("full_scan_time_matrix", True)):
        try:
            _im_h4_limit = max(int(_scan_limits["H4"]), 220)
            _im_preloaded_h4 = {}
            for _pair in candidate_pairs:
                _candles = _r.fetch_candles(_pair, "H4", _im_h4_limit)
                if _candles:
                    _im_preloaded_h4[_pair["display"]] = _candles
            intermarket_snapshot = build_scan_snapshot(
                _r.ALL_PAIRS,
                disabled_pairs=_r.disabled_pairs,
                etf_pairs=getattr(_r, "ETF_PAIRS", []),
                fetch_candles=_r.fetch_candles,
                config=_r.CONFIG,
                preloaded_h4_candles=_im_preloaded_h4,
                force=True,
            )
            if intermarket_snapshot:
                _r.log.info(
                    "[ENGINE C][INTERMARKET] prewarmed snapshot: %d symbols",
                    len((intermarket_snapshot.get("universe") or {}).get("pairs", [])),
                )
        except Exception as _im_err:
            intermarket_snapshot = None
            _r.log.warning("[ENGINE C][INTERMARKET] snapshot build failed: %s", _im_err)

    engine_b = NakedEngine()
    _regime_context = make_regime_smoothing_context()
    _EC_PAIR_TIMEOUT = 30  # seconds max per pair

    for pair in candidate_pairs:
        _pair_start = time.time()
        try:
            symbol = pair.get("symbol", pair.get("display"))
            display = pair.get("display", symbol)
            ptype = pair.get("type", "")
            btc_bias = _r.current_btc_bias() if ptype == "crypto" else "neutral"
            _pair_score_group = get_pair_score_group(pair)

            engine_a_style = _r.resolve_scan_style(
                _r.normalize_style(requested_style), pair
            )

            resolved_style_b, style_profile_b = _r.naked_scan_style_profile(
                requested_style,
                score_group=_pair_score_group,
                asset_type=ptype,
            )

            _zone_tf = str(style_profile_b.get("zone_tf", "H4")).upper()
            _entry_tf = str(style_profile_b.get("entry_tf", "H1")).upper()
            _atr_tf = str(style_profile_b.get("atr_tf", _zone_tf)).upper()
            _lim = _scan_limits
            _im_h4 = (
                ((intermarket_snapshot or {}).get("seriesStore", {}) or {})
                .get(display, {})
                .get("candles")
            )
            raw_candles = {
                "D1": _r.fetch_candles(pair, "D1", _lim["D1"]),
                "H4": _im_h4 or _r.fetch_candles(pair, "H4", _lim["H4"]),
                "H1": _r.fetch_candles(pair, "H1", _lim["H1"]),
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
                results["skipped"].append(
                    _engine_c_skip_entry(
                        display,
                        "Rate limited",
                        code="rate_limited",
                        detail=f"Rate limited on {', '.join(rate_limited_tfs)}",
                    )
                )
                continue

            # Fetch candles ONCE — share between Engine A (full) and Engine B (last bar dropped).
            # Eliminates double-fetching and ensures both engines score identical data.
            # Engine A always needs real D1/H4/H1; Engine B uses style-resolved TFs.
            _all_tfs_needed = {"D1", "H4", "H1", _zone_tf, _entry_tf, _atr_tf}
            _tf_map: dict[str, list] = {}
            for tf in _all_tfs_needed:
                if tf in raw_candles:
                    raw = raw_candles.get(tf) or []
                else:
                    limit = _lim.get(tf, _lim.get("H4", 0))
                    raw = _r.fetch_candles(pair, tf, limit)
                if raw and len(raw) > 1:
                    _tf_map[tf] = raw[:-1]
                else:
                    _tf_map[tf] = raw or []
            d1 = _tf_map.get("D1", [])
            zone_candles = _tf_map.get(_zone_tf, [])
            entry_candles = _tf_map.get(_entry_tf, [])
            atr_candles = _tf_map.get(_atr_tf, zone_candles)

            # Engine A: pass full (undropped) candles — always real D1/H4/H1, not style TFs
            sig_a = _r.analyze_pair(
                pair,
                btc_bias,
                style=engine_a_style,
                regime_context=_regime_context,
                preloaded_candles=raw_candles,
                preloaded_fetch_meta=fetch_meta,
                intermarket_snapshot=intermarket_snapshot,
            )
            _engine_a_returned_none = not sig_a
            if not sig_a:
                # Preserve correct maxScore for pair type so engine_a_raw diagnostics
                # show 2.0 for forex (not the 3.0 default from normalise_engine_a).
                sig_a = {"maxScore": 2.0 if ptype == "forex" else 3.0}

            # fetch_candles already routes through CandleBuilder (WS) first,
            # then EODHD REST as fallback — no need for separate EODHD calls.

            if (time.time() - _pair_start) > _EC_PAIR_TIMEOUT:
                _r.log.warning(f"[ENGINE C] {display}: timeout after candle fetch ({_EC_PAIR_TIMEOUT}s)")
                results["skipped"].append(
                    _engine_c_skip_entry(
                        display,
                        "Timeout",
                        code="timeout",
                        detail=f"Exceeded {_EC_PAIR_TIMEOUT}s after candle fetch",
                    )
                )
                continue

            if len(zone_candles) < 10 or len(entry_candles) < 10:
                results["skipped"].append(
                    _engine_c_skip_entry(
                        display,
                        "Insufficient data",
                        code="insufficient_data",
                        detail=f"{_zone_tf}={len(zone_candles)}, {_entry_tf}={len(entry_candles)}",
                    )
                )
                continue

            current_price = float(sig_a.get("price") or entry_candles[-1]["close"])
            atr, atr_source = _engine_b_atr_for_scan_levels(
                sig_a,
                atr_candles,
                _atr_tf,
                pair,
                resolved_style_b,
                _r,
            )

            if not atr or atr <= 0:
                _atr_detail = (
                    "Bybit ATR unavailable and signal-feed fallback disabled"
                    if atr_source == "bybit_unavailable"
                    else f"ATR unavailable on {_atr_tf}"
                )
                results["skipped"].append(
                    _engine_c_skip_entry(
                        display,
                        "Zero ATR",
                        code="zero_atr",
                        detail=_atr_detail,
                    )
                )
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
                    style=resolved_style_b,
                    pair=pair,
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
            if isinstance(sig_a, dict):
                for _fresh_key in ("candleFetchMeta", "candleFreshness", "dataFreshness"):
                    if sig_a.get(_fresh_key) is not None:
                        consensus[_fresh_key] = sig_a.get(_fresh_key)
            # Reuse engine_c.normalise_engine_a() for consistent A-side diagnostics
            a_norm_result = normalise_engine_a(sig_a)
            a_direction = sig_a.get("direction")
            consensus["engine_a_raw"] = {
                # direction is None when has_signal=False so the UI shows "no signal".
                # raw_direction exposes what Engine A actually returned — distinguishes
                # "Engine A had LONG but score below Engine C floor" vs "analyze_pair returned None".
                "direction": a_direction if a_norm_result["has_signal"] else None,
                "raw_direction": a_direction,
                "score": a_norm_result["raw_score"],
                "maxScore": a_norm_result["max_score"],
                "sl": sig_a.get("sl"),
                "tp1": sig_a.get("tp1"),
                "regime": sig_a.get("regime"),
                "style": sig_a.get("style", sig_a.get("tradeStyle")),
                "cot": sig_a.get("votes", {}).get("derivatives", sig_a.get("cot_boost")),
                "carry": sig_a.get("votes", {}).get("carry", sig_a.get("carry_boost")),
                "has_signal": a_norm_result["has_signal"],
                "score_norm": a_norm_result["score_norm"],
                # True when analyze_pair returned None (data gap / scoring error vs weak signal).
                "analyze_pair_failed": bool(_engine_a_returned_none),
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
                if verdict == "NO_SIGNAL":
                    consensus.setdefault("reason", "No signal")
                    consensus.setdefault("code", "no_signal")
                    consensus.setdefault(
                        "detail", "Neither engine produced an eligible signal"
                    )
                    consensus.setdefault("skipCode", consensus["code"])
                    consensus.setdefault("skipDetail", consensus["detail"])
                results["skipped"].append(consensus)

        except Exception as e:
            _r.log.error(f"[ENGINE C] Error on {pair.get('display')}: {e}")
            results["skipped"].append(
                _engine_c_skip_entry(
                    pair.get("display"),
                    "Error",
                    code="exception",
                    detail=str(e),
                )
            )

    results["aligned"].sort(key=lambda x: x.get("conviction", 0), reverse=True)
    results["a_only"].sort(key=lambda x: x.get("conviction", 0), reverse=True)
    results["b_only"].sort(key=lambda x: x.get("conviction", 0), reverse=True)

    total = (
        len(results["aligned"])
        + len(results["a_only"])
        + len(results["b_only"])
        + len(results["conflict"])
        + len(results["skipped"])
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
    _has_engine_b_context = _signal_has_engine_b_context(sig, d.get("engine_b") or {})

    if _sig_age > _max_age / 2:
        if _has_engine_b_context:
            return jsonify(
                {
                    "error": "ENGINE_B_REFRESH_REQUIRED: stale Engine B signal must be refreshed by Engine B before execution",
                    "pair": pair,
                }
            ), 409
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
                    f"[EXEC] {pair}: live refresh failed ({_re}) — rejecting stale signal"
                )
                return jsonify(
                    {
                        "error": f"STALE_SIGNAL_REFRESH_FAILED: {pair} live refresh failed — signal rejected",
                        "pair": pair,
                    }
                ), 409

        else:
            _r.log.warning(
                f"[EXEC] {pair}: pair not found in universe — rejecting stale signal"
            )
            return jsonify(
                {
                    "error": f"STALE_SIGNAL_REFRESH_FAILED: {pair} not found in universe — signal rejected",
                    "pair": pair,
                }
            ), 409

    if _has_engine_b_context and not _engine_b_context_confirmed(sig, d.get("engine_b") or {}):
        return jsonify(
            {
                "error": "ENGINE_B_NOT_CONFIRMED: Engine B confirmation failed before execution",
                "pair": pair,
            }
        ), 409

    if _has_engine_b_context:
        _levels_applied = _apply_engine_b_execution_levels(sig, d.get("engine_b") or {})
        if not _levels_applied:
            _r.log.warning(
                f"[EXEC] {pair}: Engine B execution levels not found — cannot execute structural signal"
            )
            return jsonify(
                {
                    "error": "ENGINE_B_LEVELS_UNAVAILABLE: structural execution levels missing",
                    "pair": pair,
                }
            ), 409

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
                ), 400

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

        _hydrate_execution_candle_quality(sig, _r=_r)

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
                with timed_sqlite_connect(
                    _r.AUDIT_DB, timeout=15.0, label="execute.audit_reject.connect"
                ) as _con:
                    timed_sqlite_execute_write(
                        _con,
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
                        label="execute.audit_reject.insert",
                    )
            except Exception as _e:
                _r.log.warning(f"[EXEC] Failed to log rejection to audit_db: {_e}")

            return jsonify(
                {
                    "error": f"Risk engine rejected: {approval.reason}",
                    "approval": approval.to_dict(),
                }
            ), 422

        _ptc_ok, _ptc_reason = _guardian_pre_trade(sig, positions, account, _pos_resp)
        if not _ptc_ok:
            _r.log.warning(f"[EXEC] {pair} GUARDIAN BLOCKED: {_ptc_reason}")
            return jsonify({"error": f"Guardian: {_ptc_reason}"}), 400

        result = run_managed_execution(_exec_venue, sig, approval)

        if result.get("success"):
            _audit_ok = True
            try:
                with timed_sqlite_connect(
                    _r.AUDIT_DB, timeout=15.0, label="execute.audit_success.connect"
                ) as con:
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
                    
                    _audit_ts = datetime.now(timezone.utc).isoformat()
                    _audit_rows = []
                    for _leg in _execution_audit_legs(result, approval):
                        _audit_rows.append((
                            _audit_ts,
                            pair,
                            sig.get("confluenceScore"),
                            _audit_engine,
                            sig.get("direction"),
                            sig.get("trendState"),
                            "EXECUTED",
                            None,
                            f"${_leg['riskAmount']}",
                            sig.get("style") or "intraday",
                            _leg.get("entryPrice"),
                            sig.get("sl"),
                            _leg.get("tp") or sig.get("tp1"),
                            _leg.get("volume"),
                            sig.get("trendState"),
                            _leg.get("riskAmount"),
                            _leg.get("riskPct"),
                            str(_leg.get("ticket", "")),
                            result.get("feeCost"),
                            json.dumps(_factors),
                            result.get("signalPriceRef"),
                            result.get("slippageBps"),
                            _eng_b_data.get("max_possible") if _audit_engine == "engine_b" else sig.get("maxScore"),
                            _eng_b_data.get("pct") if _audit_engine == "engine_b" else None,
                        ))
                    timed_sqlite_executemany_write(
                        con,
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,risk_amount,risk_pct,ticket,fee_cost,factors_json,"
                        "signal_price_ref,slippage_bps,max_score,score_pct) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        _audit_rows,
                        label="execute.audit_success.insert",
                    )
                    timed_sqlite_commit(con, label="execute.audit_success.commit")

            except Exception as ae:
                _r.log.warning(f"Audit DB write failed: {ae}")
                _audit_ok = False

            if not _audit_ok:
                result["success"] = False
                result["error"] = "AUDIT_PERSISTENCE_FAILED_AFTER_FILL"
            else:
                executed_signals.add(sig_id)
                _r.log.info(
                    f"[EXEC] {pair} EXECUTED: ticket={result.get('ticket')}, volume={result.get('volume')}"
                )

        if not result.get("success"):
            err = _log_execution_failure(_r.log, "EXEC", pair, _exec_venue, result)
            if isinstance(result, dict):
                result.setdefault("error", err)
            if result.get("error") == "AUDIT_PERSISTENCE_FAILED_AFTER_FILL":
                return jsonify(result), 500

        return jsonify(result)

    except Exception as e:
        _r.log.exception(f"[EXEC] {pair or '?'} execution error: {e}")

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
    from scalp_engine import displays_for_scalp_scan

    try:
        _r = rt()
        _disabled = getattr(_r, "disabled_pairs", None) or set()
        pairs = displays_for_scalp_scan(_r.ACTIVE_PAIRS, disabled_displays=_disabled)
        return jsonify({"pairs": pairs, "count": len(pairs)})
    except Exception as e:
        rt().log.error(f"[SCALP API] scalp-pairs error: {e}")
        return jsonify({"error": str(e)}), 500


def api_scalp_scan():
    """Engine D scalp scan — M15 zones + M5 entry triggers (MT5 non-crypto, Binance/crypto path for USDT pairs)."""
    from scalp_engine import displays_for_scalp_scan, run_scalp_scan

    _r = rt()
    d = request.get_json() or {}
    requested_pairs = d.get("pairs")

    _disabled = getattr(_r, "disabled_pairs", None) or set()
    if not requested_pairs or requested_pairs == "all":
        pairs = displays_for_scalp_scan(_r.ACTIVE_PAIRS, disabled_displays=_disabled)
    elif isinstance(requested_pairs, list):
        pairs = requested_pairs
    else:
        return jsonify({"error": "Invalid pairs list"}), 400
        
    try:
        results = run_scalp_scan(pairs)
        out = dict(results)
        out["pairs"] = list(pairs)
        out["pair_count"] = len(pairs)
        _db = getattr(rt(), "AUDIT_DB", "") or ""
        _sig_list = out.get("signals") or []
        if _db and _sig_list:
            try:
                from conductor import conductor_orchestrate, extract_conductor_microstructure, reset_scan_results

                reset_scan_results("scalp")
                for _s in _sig_list[:30]:
                    _vd, _sr = extract_conductor_microstructure(_s)
                    conductor_orchestrate(
                        _s,
                        _s.get("regime", "UNKNOWN"),
                        _db,
                        volume_divergence=_vd,
                        stop_run=_sr,
                    )
            except Exception as _ce:
                rt().log.warning(f"[CONDUCTOR] scalp scan orchestration failed: {_ce}")
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

        # ── Rebase scalp levels to current live price ────────────────────────
        # VP-based SL/TP are calculated at scan time.  By execution time the
        # price may have drifted enough to invert or invalidate the levels.
        # Recompute using the current broker mid-price before risk_check runs.
        # If VP is so stale that TP ends up on the wrong side of entry, block
        # execution and tell the user to rescan.
        _rebase_error = None
        try:
            from scalp_engine import calculate_scalp_levels, _guess_asset_type
            _bid = float(symbol_info.get("bid") or 0)
            _ask = float(symbol_info.get("ask") or 0)
            _live_px = (_bid + _ask) / 2 if _bid > 0 and _ask > 0 else _bid or _ask
            _scan_px  = float(sig.get("price") or 0)
            _vp = {
                "poc": float(sig.get("vp_poc") or 0),
                "vah": float(sig.get("vp_vah") or 0),
                "val": float(sig.get("vp_val") or 0),
                "lvn_levels": [],
            }
            if _live_px > 0 and _vp["poc"] > 0 and _vp["vah"] > 0 and _vp["val"] > 0:
                _asset_type = _guess_asset_type(pair_key)
                _rebased = calculate_scalp_levels(
                    sig.get("direction", "LONG"),
                    _live_px,
                    _vp,
                    sig.get("zone_type", "trend_continuation"),
                    symbol_info,
                    _asset_type,
                )
                drift_pct = abs(_live_px - _scan_px) / _scan_px * 100 if _scan_px else 0
                if _rebased.get("rr_below_min"):
                    # VP structure invalidated (TP inverted) or RR too low — do not execute
                    _rebase_error = (
                        f"VP structure invalidated by price drift ({drift_pct:.2f}% since scan). "
                        f"Rescan required."
                    )
                    _r.log.warning(f"[SCALP EXEC] {pair_key} BLOCKED: {_rebase_error}")
                else:
                    _r.log.info(
                        f"[SCALP EXEC] {pair_key} levels rebased: "
                        f"price {_scan_px:.5f}→{_live_px:.5f} ({drift_pct:.2f}% drift), "
                        f"sl {sig.get('sl')}→{_rebased['sl']}, "
                        f"tp1 {sig.get('tp1')}→{_rebased['tp1']}"
                    )
                    sig = dict(sig)   # don't mutate caller's dict
                    sig["price"] = _rebased["entry"]
                    sig["sl"]    = _rebased["sl"]
                    sig["tp1"]   = _rebased["tp1"]
                    if _rebased.get("tp2"):
                        sig["tp2"] = _rebased["tp2"]
        except Exception as _re:
            _r.log.warning(f"[SCALP EXEC] Level rebase failed ({_re}), using scan-time levels")

        if _rebase_error:
            return jsonify({"error": _rebase_error}), 400

        _hydrate_execution_candle_quality(sig, _r=_r)

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
            _audit_ok = True
            try:
                with timed_sqlite_connect(
                    _r.AUDIT_DB, timeout=15.0, label="scalp_execute.audit_success.connect"
                ) as con:
                    timed_sqlite_execute_write(
                        con,
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
                        ),
                        label="scalp_execute.audit_success.insert",
                    )
            except Exception as ae:
                _r.log.warning(f"[SCALP API] Audit log failed: {ae}")
                _audit_ok = False

            if not _audit_ok:
                result["success"] = False
                result["error"] = "AUDIT_PERSISTENCE_FAILED_AFTER_FILL"
                return jsonify({"error": result["error"], "execution": _json_safe(result)}), 500
                
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
