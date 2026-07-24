"""Execution and Engine C HTTP handlers (registered from athena.py)."""

from __future__ import annotations

import json
import math
import sqlite3
import time
import traceback
import uuid
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    as_completed,
)
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

from athena_app.api.routes_execution import (
    merge_scalp_sizing_override,
    normalize_pip_mode,
    parse_execution_volume_args,
)
from athena_app.repositories.audit_repo import insert_manual_error
from athena_app.services.candle_service import recompute_levels_for_style
from athena_app.services.crypto_signal_feed import (
    fetch_crypto_signal_candles,
    resolve_crypto_signal_feed,
)
from athena_runtime import (
    abort_signal_execution,
    begin_signal_execution,
    complete_signal_execution,
    is_signal_duplicate,
    rt,
)
from candles_cache import (
    _annotate_fetch_meta_with_bar_freshness,
    extract_candles,
    get_candle_fetch_meta,
)
from config import _json_safe, get_optimal_workers, scan_candle_limits
from engine_c import compute_consensus, normalise_engine_a
from execution_lifecycle import _demo_broker_execution_requested, run_managed_execution
from factor_scoring import make_regime_smoothing_context
from indicators import calc_atr, calc_indicators_with_normalized
from intermarket import build_scan_snapshot
from market_structure import (
    NakedEngine,
    _synthesize_tp_from_sl,
    engine_b_confidence_passes,
    engine_b_low_volatility_gate,
    engine_b_live_trigger_kwargs,
)
from guardian import pre_trade_check as _guardian_pre_trade
from scoring import get_pair_score_group
from exit_mode_apply import apply_engine_a_exit_mode
from exit_policy import engine_b_scaleout_execution_supported
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


def _resolve_ai_review_for_audit(sig: dict, audit_db: str | None) -> tuple[str | None, str | None]:
    """Best-effort (grade, review_id) of the latest real AI review for journaling.

    Audit metadata only — never gates, blocks, or alters execution. Prefers the
    grade carried on the signal payload, then falls back to the most recent
    stored review for the symbol within 24h. Any failure returns (None, None).
    """
    try:
        grade = sig.get("ai_grade") or sig.get("aiGrade")
        review_id = sig.get("ai_review_id") or sig.get("review_id")
        if grade:
            return str(grade), (str(review_id) if review_id else None)
        symbol = sig.get("pair") or sig.get("symbol")
        if not audit_db or not symbol:
            return None, None
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with sqlite3.connect(audit_db, timeout=5.0) as con:
            row = con.execute(
                "SELECT grade, review_id FROM bulk_ai_reviews"
                " WHERE (pair=? OR symbol=?) AND created_at>=? AND is_safe_default=0"
                " ORDER BY created_at DESC LIMIT 1",
                (symbol, symbol, cutoff),
            ).fetchone()
            if row and row[0]:
                return str(row[0]), (str(row[1]) if row[1] else None)
            row = con.execute(
                "SELECT ai_review_json, review_id FROM ai_chart_reviews"
                " WHERE symbol=? AND created_at>=? AND parse_success=1"
                " ORDER BY created_at DESC LIMIT 1",
                (symbol, cutoff),
            ).fetchone()
            if row and row[0]:
                rev = json.loads(row[0])
                g = rev.get("grade") or rev.get("verdict")
                if g:
                    return str(g), (str(row[1]) if row[1] else None)
    except Exception:
        pass
    return None, None


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
        bybit_atr = runtime.bybit_atr_for_levels(pair, resolved_style, atr_tf=tf)
        if bybit_atr:
            return float(bybit_atr), "bybit"
        if not bool(cfg.get("ENGINE_B_CRYPTO_LEVELS_SIGNAL_FEED_FALLBACK", False)):
            return 0.0, "bybit_unavailable"

    return float(atr or 0.0), tf


def _fetch_engine_c_signal_candles(runtime, pair: dict, tf: str, limit: int):
    """Fetch Engine C scan candles honouring the Engine A/B crypto signal feed.

    Mirrors scanner._fetch_ab_crypto_signal_candles / the api_scan_naked A5 fix
    so Engine C scores crypto on the same venue (ENGINE_AB_CRYPTO_SIGNAL_FEED,
    default bybit) as the standalone Engine A/B scans, instead of falling
    through to fetch_candles (Binance for source=binance pairs). Non-crypto
    pairs pass through to runtime.fetch_candles unchanged.

    Returns (candles, meta); meta is None on the passthrough path.
    """
    cfg = getattr(runtime, "CONFIG", {}) or {}
    if (
        str((pair or {}).get("type") or "").lower() != "crypto"
        or resolve_crypto_signal_feed("AB", cfg) == "binance"
    ):
        return runtime.fetch_candles(pair, tf, limit), None

    result = fetch_crypto_signal_candles(
        pair,
        tf,
        limit,
        engine="AB",
        config=cfg,
        default_fetch=lambda pair_arg, tf_arg, limit_arg: runtime.fetch_candles(
            pair_arg, tf_arg, limit_arg
        ),
        bybit_fetch=getattr(runtime, "fetch_bybit_klines", None),
        bybit_paginated_fetch=getattr(runtime, "fetch_bybit_klines_paginated", None),
    )
    return result.candles, result.meta


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
        try:
            from ai_context import derive_engine_b_score_pct

            score_pct = derive_engine_b_score_pct(engine_b)
        except Exception:
            score_pct = engine_b.get("pct", engine_b.get("gate_pct"))
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

    factors["quote"] = {
        "priceSource": sig.get("priceSource"),
        "quoteSource": sig.get("quoteSource"),
        "quoteTs": sig.get("quoteTs"),
        "quoteAgeSec": sig.get("quoteAgeSec"),
        "signalPrice": sig.get("price"),
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
        "asset_class": sig.get("type") or sig.get("asset_class"),
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


def _engine_d_execution_block_reason(
    sig: dict,
    *,
    review_id: str | None = None,
    audit_db: str | None = None,
    cfg: dict | None = None,
) -> str | None:
    """Return the fail-closed reason for non-executable Engine D candidates."""
    from ai_scalp_review.execute_gate import engine_d_execution_block_reason

    return engine_d_execution_block_reason(
        sig,
        review_id=review_id,
        audit_db=audit_db,
        cfg=cfg or {},
    )


def _engine_d_paper_demo_execution_block_reason(
    sig: dict,
    *,
    review_id: str | None = None,
    audit_db: str | None = None,
    cfg: dict | None = None,
    require_ai_review: bool | None = None,
) -> str | None:
    """Return the paper/demo scalp block reason while allowing advisory A/B demotions."""
    from ai_scalp_review.execute_gate import engine_d_paper_demo_execution_block_reason

    return engine_d_paper_demo_execution_block_reason(
        sig,
        review_id=review_id,
        audit_db=audit_db,
        cfg=cfg or {},
        require_ai_review=require_ai_review,
    )


def _payload_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
    return None


def _payload_require_ai_review(payload: dict | None) -> bool | None:
    payload = payload or {}
    for key in ("require_ai_review", "requireAiReview"):
        if key in payload:
            return _payload_bool(payload.get(key))
    return None


def _requested_scalp_execution_mode(payload: dict | None) -> str:
    mode = str((payload or {}).get("execution_mode") or (payload or {}).get("executionMode") or "").strip().lower()
    return mode if mode in ("paper", "demo", "live") else ""


def _scalp_demo_execution_requested(venue: str, cfg: dict | None) -> bool:
    try:
        if str((cfg or {}).get("EXECUTOR_MODE") or "").strip().lower() == "demo":
            return True
    except Exception:
        pass
    return _demo_broker_execution_requested(venue)


def _engine_a_trade_gate_block_reason(
    sig: dict,
    pair_obj: dict | None = None,
    config: dict | None = None,
) -> str | None:
    """Fail-closed Engine A trade-eligibility gate for manual execution paths."""
    from engine_a_trade_gate import engine_a_trade_gate_block_reason

    return engine_a_trade_gate_block_reason(sig, pair_obj, config=config or rt().CONFIG)


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
    eng = str(sig.get("engine") or sig.get("source_engine") or "").strip().lower()
    verdict = str(sig.get("verdict") or "").strip().upper()
    engine_a_row = eng in ("a", "engine_a", "scalp", "scalp_vp", "engine_d")
    b_executable_verdict = verdict in (
        "ALIGNED",
        "B_ONLY",
        "B_ONLY_SCORED",
        "B_ONLY_VISION_CONFIRMED",
        "B_OVERRIDE_CONFLICT",
    )
    eb = sig.get("engine_b")
    if isinstance(eb, dict) and eb:
        if engine_a_row and not b_executable_verdict:
            return False
        return True
    if engine_b:
        return True
    return False


def _engine_b_pre_risk_broker_price(sig: dict, venue: str, cfg: dict) -> tuple[str | None, dict]:
    """Refresh the broker quote before risk; also validate structural Engine B drift."""
    structural_engine_b = _is_structural_engine_b_execution(sig)
    direction = str(sig.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return "INVALID_DIRECTION", {}
    try:
        signal_price = float(sig.get("price") or sig.get("entry") or 0)
    except (TypeError, ValueError):
        signal_price = 0.0
    if signal_price <= 0:
        return "SIGNAL_PRICE_MISSING", {}

    broker_drift_price = None
    broker_spread = None
    try:
        if venue == "bybit":
            from bybit_executor import (
                _bybit_execution_side_price,
                _bybit_max_tick_age_sec,
                _bybit_ticker_age_seconds,
                _fetch_bybit_ticker,
                _get_exchange,
                bybit_map_symbol,
            )

            exchange = _get_exchange()
            symbol = bybit_map_symbol(sig.get("pair", "")) or bybit_map_symbol(sig.get("symbol", ""))
            if exchange is None or not symbol:
                return "BROKER_QUOTE_UNAVAILABLE", {}
            ticker = exchange.fetch_ticker(symbol)
            broker_price = _bybit_execution_side_price(ticker, direction)
            try:
                _bid = float(ticker.get("bid") or 0)
                _ask = float(ticker.get("ask") or 0)
                if _bid > 0 and _ask >= _bid:
                    broker_spread = _ask - _bid
            except (AttributeError, TypeError, ValueError):
                pass
            broker_quote_age = _bybit_ticker_age_seconds(ticker)
            if broker_quote_age is None:
                raw_symbol = symbol.split(":")[0].replace("/", "")
                rest_tick = _fetch_bybit_ticker(raw_symbol, category="linear")
                if isinstance(rest_tick, dict) and rest_tick.get("timestamp"):
                    broker_quote_age = _bybit_ticker_age_seconds(
                        {"timestamp": rest_tick["timestamp"]}
                    )
            broker_quote_age_limit = _bybit_max_tick_age_sec()
        else:
            from mt5_executor import (
                _get_mt5,
                _mt5_max_tick_age_sec,
                _mt5_tick_age_seconds,
                mt5_connect,
                mt5_map_symbol,
            )

            mt5 = _get_mt5()
            symbol = mt5_map_symbol(sig.get("pair") or sig.get("display") or sig.get("symbol") or "")
            if mt5 is None or not symbol or not mt5_connect():
                return "BROKER_QUOTE_UNAVAILABLE", {}
            tick = mt5.symbol_info_tick(symbol)
            broker_price = (tick.ask if direction == "LONG" else tick.bid) if tick else None
            # MT5 scan quotes are recorded as bid/ask midpoints.  Drift must
            # compare like-for-like so the spread is handled by the dedicated
            # execution-spread gate rather than misclassified as price movement.
            if tick:
                try:
                    bid = float(tick.bid)
                    ask = float(tick.ask)
                    if bid > 0 and ask >= bid:
                        broker_drift_price = (bid + ask) / 2.0
                        broker_spread = ask - bid
                except (AttributeError, TypeError, ValueError):
                    pass
            broker_quote_age = _mt5_tick_age_seconds(tick) if tick else None
            broker_quote_age_limit = _mt5_max_tick_age_sec()
    except Exception as exc:
        return f"BROKER_QUOTE_ERROR:{type(exc).__name__}", {}

    try:
        broker_price_f = float(broker_price)
    except (TypeError, ValueError):
        broker_price_f = 0.0
    if broker_price_f <= 0:
        return "BROKER_QUOTE_INVALID", {}
    try:
        broker_drift_price_f = float(broker_drift_price or broker_price_f)
    except (TypeError, ValueError):
        broker_drift_price_f = broker_price_f
    if broker_drift_price_f <= 0:
        return "BROKER_QUOTE_INVALID", {}
    if broker_quote_age is None:
        return "BROKER_TICK_TIMESTAMP_MISSING", {}
    if broker_quote_age_limit is not None and broker_quote_age > broker_quote_age_limit:
        return "BROKER_TICK_STALE", {
            "tickAgeSec": round(float(broker_quote_age), 3),
            "tickAgeLimitSec": float(broker_quote_age_limit),
        }

    broker_quote_ts = time.time() - max(0.0, float(broker_quote_age))
    sig["brokerPreflightPrice"] = broker_price_f
    sig["quoteTimestamp"] = broker_quote_ts
    sig["quoteAgeSec"] = max(0.0, float(broker_quote_age))
    sig.pop("quoteAgeEval", None)
    sig["quoteFreshness"] = {
        "source": f"{venue}_broker_preflight",
        "timestamp": broker_quote_ts,
        "ageSec": round(max(0.0, float(broker_quote_age)), 3),
        "fresh": True,
    }
    if broker_spread is not None and broker_spread > 0:
        sig["quoteSpread"] = broker_spread
    if not structural_engine_b:
        # Engine A keeps its analyzed entry and levels.  The executors retain
        # responsibility for their existing price-drift/rebase checks; only
        # quote freshness is renewed here immediately before risk sizing.
        return None, {
            "signalPrice": signal_price,
            "brokerPrice": broker_price_f,
            "brokerDriftReferencePrice": broker_drift_price_f,
            "tickAgeSec": round(max(0.0, float(broker_quote_age)), 3),
        }

    asset_type = str(sig.get("type") or sig.get("asset_type") or "").lower()
    threshold_type = "etf" if asset_type == "etf_bond" else asset_type
    pct_cfg = cfg.get("MAX_SIGNAL_DRIFT_PCT") or {}
    atr_cfg = cfg.get("MAX_SIGNAL_DRIFT_ATR_MULT") or {}
    try:
        pct_limit = float(pct_cfg.get(threshold_type))
    except (AttributeError, TypeError, ValueError):
        pct_limit = 0.0
    nested = sig.get("naked_data") if isinstance(sig.get("naked_data"), dict) else sig.get("engine_b")
    nested = nested if isinstance(nested, dict) else {}
    try:
        atr = float(sig.get("atr") or nested.get("atr") or 0)
        atr_mult = float(atr_cfg.get(threshold_type))
    except (AttributeError, TypeError, ValueError):
        atr = 0.0
        atr_mult = 0.0
    if pct_limit <= 0 or atr <= 0 or atr_mult <= 0:
        return "ENGINE_B_DRIFT_LIMIT_UNAVAILABLE", {}

    pct_drift = abs(broker_drift_price_f - signal_price) / signal_price
    atr_drift = abs(broker_drift_price_f - signal_price) / atr
    if pct_drift > pct_limit or atr_drift > atr_mult:
        return "ENGINE_B_BROKER_DRIFT_TOO_LARGE", {
            "signalPrice": signal_price,
            "brokerPrice": broker_price_f,
            "brokerDriftReferencePrice": broker_drift_price_f,
            "driftPct": round(pct_drift, 6),
            "driftAtr": round(atr_drift, 4),
            "maxDriftPct": pct_limit,
            "maxDriftAtr": atr_mult,
        }

    sig.setdefault("signalPriceRef", signal_price)
    sig["price"] = broker_price_f
    sig["entry"] = broker_price_f
    return None, {
        "signalPrice": signal_price,
        "brokerPrice": broker_price_f,
        "brokerDriftReferencePrice": broker_drift_price_f,
        "driftPct": round(pct_drift, 6),
        "driftAtr": round(atr_drift, 4),
        "tickAgeSec": round(max(0.0, float(broker_quote_age)), 3),
    }


def _engine_b_rr_gate_leg(sig: dict, rr_target: float | None = None) -> str:
    """Return tp1 or tp2 — whichever price is used for the RR gate target."""
    try:
        tp1 = float(sig.get("tp1") or 0)
        tp2 = float(sig.get("tp2") or tp1)
    except (TypeError, ValueError):
        return "tp1"
    if rr_target is None or tp1 <= 0:
        return "tp1"
    if tp2 > 0 and tp2 != tp1:
        if math.isclose(rr_target, tp2, rel_tol=1e-9, abs_tol=1e-12):
            return "tp2"
    return "tp1"


def _engine_b_tp_already_synthetic(sig: dict, *, rr_target: float | None = None) -> bool:
    """True when the RR gate target leg is already a synthetic fallback TP."""
    leg = _engine_b_rr_gate_leg(sig, rr_target)
    source_keys = ("tp2_source",) if leg == "tp2" else ("tp1_source", "tp_source")
    for source in (sig, sig.get("engine_b_status"), sig.get("engine_b"), sig.get("naked_data")):
        if not isinstance(source, dict):
            continue
        for key in source_keys:
            if str(source.get(key) or "").strip().lower() == "fallback_rr":
                return True
    return False


def _engine_b_profile_thresholds_from_cfg(
    cfg: dict,
    *,
    style: str,
    score_group: str | None,
) -> tuple[float | None, float | None]:
    """Config-only style/group min_rr lookup when runtime profile hook is unavailable."""
    style_key = str(style or "intraday").lower()
    defaults = {
        "scalp": {"min_rr": 1.0, "fallback_rr": 1.4},
        "intraday": {"min_rr": 1.2, "fallback_rr": 1.8},
        "swing": {"min_rr": 1.6, "fallback_rr": 2.2},
    }
    base = dict(defaults.get(style_key, defaults["intraday"]))
    naked = (cfg.get("NAKED_ENGINE") or {}) if isinstance(cfg, dict) else {}
    style_profiles = naked.get("style_profiles") or {}
    if isinstance(style_profiles.get(style_key), dict):
        base.update(style_profiles[style_key])
    if score_group:
        group_overrides = (naked.get("score_group_overrides") or {}).get(score_group) or {}
        if isinstance(group_overrides.get(style_key), dict):
            base.update(group_overrides[style_key])
    try:
        min_rr = float(base.get("min_rr")) if base.get("min_rr") is not None else None
    except (TypeError, ValueError):
        min_rr = None
    try:
        fallback_rr = float(base.get("fallback_rr")) if base.get("fallback_rr") is not None else None
    except (TypeError, ValueError):
        fallback_rr = None
    return min_rr, fallback_rr


def _resolve_engine_b_rr_thresholds(
    sig: dict,
    cfg: dict | None = None,
) -> tuple[float | None, float | None]:
    min_rr = None
    fallback_rr = None
    for source in (sig, sig.get("engine_b_status"), sig.get("engine_b"), sig.get("naked_data")):
        if not isinstance(source, dict):
            continue
        for key in (
            "engine_b_min_rr",
            "min_rr",
            "required_rr",
            "engine_b_rr_required",
            "rr_required",
        ):
            if min_rr is None and source.get(key) is not None:
                try:
                    min_rr = float(source.get(key))
                except (TypeError, ValueError):
                    pass
        for key in ("fallback_rr", "engine_b_fallback_rr"):
            if fallback_rr is None and source.get(key) is not None:
                try:
                    fallback_rr = float(source.get(key))
                except (TypeError, ValueError):
                    pass

    style = str(sig.get("style") or "intraday").lower()
    asset_type = str(sig.get("type") or sig.get("asset_type") or "forex").lower()
    score_group = None
    if sig.get("pair"):
        score_group = get_pair_score_group(
            {"display": sig.get("pair"), "type": asset_type}
        )
    profile_fn = None
    try:
        profile_fn = getattr(rt(), "naked_scan_style_profile", None)
    except RuntimeError:
        pass
    if callable(profile_fn):
        try:
            _, profile = profile_fn(
                style,
                score_group=score_group,
                asset_type=asset_type,
                symbol=str(sig.get("pair") or ""),
            )
            if min_rr is None and profile.get("min_rr") is not None:
                min_rr = float(profile.get("min_rr"))
            if fallback_rr is None and profile.get("fallback_rr") is not None:
                fallback_rr = float(profile.get("fallback_rr"))
        except Exception:
            pass
    if min_rr is None or fallback_rr is None:
        cfg_min, cfg_fallback = _engine_b_profile_thresholds_from_cfg(
            cfg or {},
            style=style,
            score_group=score_group,
        )
        if min_rr is None:
            min_rr = cfg_min
        if fallback_rr is None:
            fallback_rr = cfg_fallback

    try:
        exec_floor = float((cfg or {}).get("ENGINE_C_EXEC_MIN_RR", 1.0) or 1.0)
    except (TypeError, ValueError):
        exec_floor = 1.0
    if min_rr is None:
        min_rr = exec_floor if exec_floor > 0 else None
    elif exec_floor > 0:
        min_rr = max(min_rr, exec_floor)
    if fallback_rr is None or fallback_rr <= 0:
        fallback_rr = 1.8
    return min_rr, fallback_rr


def _engine_b_rr_reconcile_applies(sig: dict) -> bool:
    """True when broker-entry RR reconciliation should run before risk_check."""
    if str(sig.get("level_source") or "").strip().lower() == "engine_b_execution":
        return True
    return _is_structural_engine_b_execution(sig)


def _engine_b_reconcile_rr_target(sig: dict, entry: float) -> tuple[float, float | None]:
    """Mirror risk_engine RR target selection for reconcile (tp + optional plan floor)."""
    try:
        tp1 = float(sig.get("tp1") or 0)
    except (TypeError, ValueError):
        tp1 = 0.0
    asset_type = str(sig.get("type") or sig.get("asset_type") or "forex").lower()
    try:
        from risk_engine import _engine_b_resolver_plan_for_risk

        resolver_plan = _engine_b_resolver_plan_for_risk(sig, entry, asset_type)
    except Exception:
        resolver_plan = None
    if resolver_plan and resolver_plan[0] == "scale_out":
        return float(resolver_plan[2]), float(resolver_plan[1])
    if resolver_plan and resolver_plan[0] == "single_structural_tp1":
        return float(resolver_plan[2]), float(resolver_plan[1])
    return tp1, None


def _geometric_rr(entry: float, sl: float, tp: float) -> float | None:
    risk_abs = abs(entry - sl)
    if risk_abs <= 0:
        return None
    return abs(tp - entry) / risk_abs


def _directional_rr(
    entry: float, sl: float, tp: float, direction: str
) -> float | None:
    """Direction-aware reward/risk from a (filled) entry price.

    Returns ``None`` when the levels are invalid for the direction (zero or
    negative risk/reward distance) so callers can fail closed.
    """
    try:
        entry = float(entry)
        sl = float(sl)
        tp = float(tp)
    except (TypeError, ValueError):
        return None
    direction = str(direction or "").upper()
    if direction == "LONG":
        risk = entry - sl
        reward = tp - entry
    elif direction == "SHORT":
        risk = sl - entry
        reward = entry - tp
    else:
        return None
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _signal_min_rr(sig: dict) -> float | None:
    """Signal-stamped minimum RR (first explicit positive value wins)."""
    for source in (sig, sig.get("engine_b_status"), sig.get("engine_b"), sig.get("naked_data")):
        if not isinstance(source, dict):
            continue
        for key in (
            "engine_b_min_rr",
            "min_rr",
            "required_rr",
            "engine_b_rr_required",
            "rr_required",
            "minRr",
            "minRR",
        ):
            raw = source.get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


_DISPLACEMENT_GUARD_DEFAULTS = {
    "ENABLED": True,
    # Max chase distance from the trigger level, normalized by structure ATR.
    "MAX_DISPLACEMENT_ATR": 1.5,
    # Max fraction of the expected trigger->TP1 path already consumed.
    "MAX_EXCURSION_FRACTION": 0.60,
    # Optional absolute floors; signal-stamped min RR wins when present.
    "MIN_RR_AT_ENTRY": None,
    "MIN_RR_AT_FILL": None,
}


def _displacement_guard_cfg(cfg: dict | None) -> dict:
    merged = dict(_DISPLACEMENT_GUARD_DEFAULTS)
    section = (cfg or {}).get("DISPLACEMENT_GUARD")
    if isinstance(section, dict):
        for key in merged:
            if section.get(key) is not None:
                merged[key] = section[key]
    return merged


_SETUP_LEVEL_KEYS = ("setupLevel", "setup_level", "setupPrice", "setup_price")
_TRIGGER_LEVEL_KEYS = ("triggerLevel", "trigger_level", "triggerPrice", "trigger_price")


def _first_positive_level(sig: dict, keys) -> float | None:
    for source in (sig, sig.get("engine_b"), sig.get("naked_data")):
        if not isinstance(source, dict):
            continue
        for key in keys:
            try:
                value = float(source.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


def _setup_zone_mid(sig: dict) -> float | None:
    for source in (sig, sig.get("engine_b"), sig.get("naked_data")):
        if not isinstance(source, dict):
            continue
        zone = source.get("setupZone") or source.get("setup_zone")
        if isinstance(zone, (list, tuple)) and len(zone) == 2:
            try:
                lo = float(zone[0])
                hi = float(zone[1])
            except (TypeError, ValueError):
                continue
            if lo > 0 and hi > 0:
                return (lo + hi) / 2.0
    return None


def _displacement_guard_block_reason(sig: dict, cfg: dict | None) -> tuple[str | None, dict]:
    """Pre-order chase/displacement protection shared by both execution venues.

    Thresholds are normalized per instrument via structure ATR and the current
    spread — no fixed pip thresholds. Missing ATR/spread inputs skip that
    sub-check and record why (advisory data); a missing executable price fails
    closed. Returns ``(block_reason_or_None, diagnostics)``.
    """
    guard = _displacement_guard_cfg(cfg)
    diag: dict = {"enabled": bool(guard.get("ENABLED")), "skipped": {}}
    if not diag["enabled"]:
        diag["skipped"]["guard"] = "disabled via DISPLACEMENT_GUARD.ENABLED"
        return None, diag
    direction = str(sig.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        diag["skipped"]["guard"] = "direction unresolved — directional gates upstream own this"
        return None, diag
    try:
        current = float(
            sig.get("brokerPreflightPrice") or sig.get("price") or sig.get("entry") or 0
        )
    except (TypeError, ValueError):
        current = 0.0
    if current <= 0:
        return "CHASE_PROTECTION_PRICE_MISSING", diag
    diag["referencePrice"] = current

    setup_ref = _first_positive_level(sig, _SETUP_LEVEL_KEYS)
    if setup_ref is None:
        setup_ref = _setup_zone_mid(sig)
    trigger_ref = _first_positive_level(sig, _TRIGGER_LEVEL_KEYS)
    scan_ref = None
    try:
        scan_ref = float(sig.get("signalPriceRef") or 0) or None
    except (TypeError, ValueError):
        scan_ref = None
    if setup_ref is None:
        setup_ref = scan_ref
    if trigger_ref is None:
        trigger_ref = scan_ref
    if setup_ref:
        diag["distanceFromSetupAtFill"] = round(abs(current - setup_ref), 8)
    if trigger_ref:
        diag["distanceFromTriggerAtFill"] = round(abs(current - trigger_ref), 8)

    chase_ref = trigger_ref or setup_ref
    displacement = 0.0
    if chase_ref:
        displacement = (current - chase_ref) if direction == "LONG" else (chase_ref - current)

    nested = sig.get("naked_data") if isinstance(sig.get("naked_data"), dict) else sig.get("engine_b")
    try:
        atr = float(sig.get("atr") or (nested or {}).get("atr") or 0)
    except (TypeError, ValueError):
        atr = 0.0
    if chase_ref and atr > 0:
        dist_atr = max(0.0, displacement) / atr
        diag["distanceInAtr"] = round(dist_atr, 4)
        diag["maxDisplacementAtr"] = float(guard["MAX_DISPLACEMENT_ATR"])
        if dist_atr > float(guard["MAX_DISPLACEMENT_ATR"]):
            return "CHASE_PROTECTION_ATR_DISPLACEMENT", diag
    else:
        diag["skipped"]["atrDisplacement"] = "missing trigger/setup reference or ATR"

    try:
        spread = float(
            sig.get("quoteSpread") or sig.get("currentSpread") or sig.get("spread") or 0
        )
    except (TypeError, ValueError):
        spread = 0.0
    if chase_ref and spread > 0:
        diag["distanceInSpreads"] = round(max(0.0, displacement) / spread, 2)
    else:
        diag["skipped"]["spreadDisplacement"] = "missing trigger/setup reference or current spread"

    try:
        tp1 = float(sig.get("tp1") or 0)
    except (TypeError, ValueError):
        tp1 = 0.0
    if chase_ref and tp1 > 0:
        original_path = (tp1 - chase_ref) if direction == "LONG" else (chase_ref - tp1)
        if original_path > 0:
            fraction = max(0.0, displacement) / original_path
            diag["expectedExcursionFraction"] = round(fraction, 4)
            diag["maxExcursionFraction"] = float(guard["MAX_EXCURSION_FRACTION"])
            if fraction > float(guard["MAX_EXCURSION_FRACTION"]):
                return "CHASE_PROTECTION_EXCURSION_CONSUMED", diag
        else:
            diag["skipped"]["excursionFraction"] = "tp1 not beyond trigger/setup reference"
    else:
        diag["skipped"]["excursionFraction"] = "missing trigger/setup reference or tp1"

    try:
        sl = float(sig.get("sl") or 0)
    except (TypeError, ValueError):
        sl = 0.0
    min_rr = _signal_min_rr(sig)
    if min_rr is None and guard.get("MIN_RR_AT_ENTRY") is not None:
        try:
            min_rr = float(guard["MIN_RR_AT_ENTRY"])
        except (TypeError, ValueError):
            min_rr = None
    if tp1 > 0 and sl > 0 and min_rr:
        rr = _directional_rr(current, sl, tp1, direction)
        if rr is not None:
            diag["rrAtEntry"] = round(rr, 4)
            diag["minRrAtEntry"] = min_rr
        if rr is None or rr + 1e-12 < min_rr:
            return "CHASE_PROTECTION_RR_BELOW_MIN", diag
    else:
        diag["skipped"]["rrAtEntry"] = "missing sl/tp1 or no minimum RR floor"

    return None, diag


def _post_fill_rr_violation(
    sig: dict, rr_at_fill: float | None, cfg: dict | None
) -> str | None:
    """Post-fill RR violation reason computed from the TRUE fill price.

    Zero/invalid risk distance fails closed; when the fill RR falls below the
    signal's minimum RR (or the configured DISPLACEMENT_GUARD.MIN_RR_AT_FILL
    floor) the caller follows the existing post-fill violation path
    (emergency close + alert). No floor configured -> record only.
    """
    if rr_at_fill is None:
        return "ACTUAL_RR_INVALID_AT_FILL: zero/invalid risk distance at fill"
    min_rr = _signal_min_rr(sig)
    if min_rr is None:
        floor = _displacement_guard_cfg(cfg).get("MIN_RR_AT_FILL")
        try:
            min_rr = float(floor) if floor is not None else None
        except (TypeError, ValueError):
            min_rr = None
    if min_rr is not None and min_rr > 0 and rr_at_fill + 1e-12 < min_rr:
        return f"ACTUAL_RR_BELOW_MIN_AT_FILL: {rr_at_fill:.4f} < min {min_rr}"
    return None


def _trigger_lifecycle_block_reason(sig: dict) -> str | None:
    """Block execution when a trigger is EXPIRED/INVALIDATED or past expiry.

    Reads the stamped fields (``triggerState``/``triggerExpiry``, plus the
    snake_case/``triggerExpiresAt`` aliases) and, when the signal carries a
    ``signalId``, the live tracker record via
    ``timeframe_policy.get_trigger_record``. Backward compatible: signals
    without any trigger fields or tracked record are unaffected. An expiry
    that cannot be parsed is treated as expired (fail-closed, mirroring the
    un-attestable broker-tick rule).
    """
    sig = sig or {}
    state = str(sig.get("triggerState") or sig.get("trigger_state") or "").strip().upper()
    expires_raw = (
        sig.get("triggerExpiry")
        or sig.get("trigger_expiry")
        or sig.get("triggerExpiresAt")
        or sig.get("trigger_expires_at")
    )
    if state in ("EXPIRED", "INVALIDATED"):
        return f"TRIGGER_LIFECYCLE_{state}"
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return "TRIGGER_LIFECYCLE_EXPIRED"
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expires_at:
            return "TRIGGER_LIFECYCLE_EXPIRED"
    if not state and not expires_raw and sig.get("signalId"):
        # No stamped fields: consult the live tracker so a trigger that
        # expired/was invalidated after the scan is still fail-closed.
        try:
            from timeframe_policy import get_trigger_record

            record = get_trigger_record(sig.get("signalId"))
        except Exception:
            record = None
        if record is not None:
            record_state = str(getattr(record.state, "value", record.state) or "").upper()
            if record_state in ("EXPIRED", "INVALIDATED"):
                return f"TRIGGER_LIFECYCLE_{record_state}"
            record_expiry = getattr(record, "expires_at", None)
            if record_expiry is not None:
                if record_expiry.tzinfo is None:
                    record_expiry = record_expiry.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) >= record_expiry:
                    return "TRIGGER_LIFECYCLE_EXPIRED"
    return None


def _reconcile_engine_b_rr_after_broker_entry(sig: dict, cfg: dict) -> str | None:
    """Resynthesize TP when broker entry rebase drops geometric RR below min_rr."""
    if not _engine_b_rr_reconcile_applies(sig):
        return None
    try:
        from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled

        if tp_sl_rr_gates_disabled(cfg, signal=sig):
            return None
    except Exception:
        pass
    if not bool(cfg.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP", False)):
        return None

    direction = str(sig.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return "INVALID_DIRECTION"
    try:
        entry = float(sig.get("price") or sig.get("entry") or 0)
        sl = float(sig.get("sl") or 0)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or sl <= 0:
        return None

    rr_target, plan_floor = _engine_b_reconcile_rr_target(sig, entry)
    if rr_target <= 0:
        return None

    if _engine_b_tp_already_synthetic(sig, rr_target=rr_target):
        return None

    min_rr, fallback_rr = _resolve_engine_b_rr_thresholds(sig, cfg)
    if min_rr is None or min_rr <= 0 or fallback_rr is None or fallback_rr <= 0:
        return None
    if plan_floor is not None and plan_floor > 0:
        min_rr = max(min_rr, plan_floor)

    rr_geom = _geometric_rr(entry, sl, rr_target)
    if rr_geom is None or rr_geom + 1e-12 >= min_rr:
        return None

    synth_tp, _target_rr = _synthesize_tp_from_sl(
        entry, sl, direction, fallback_rr, min_rr
    )
    if synth_tp is None:
        return "ENGINE_B_RR_BELOW_MINIMUM_AFTER_REBASE"

    asset_type = str(sig.get("type") or sig.get("asset_type") or "").lower()
    from risk_engine import validate_tp_exchange_bounds

    bounds_ok, _bounds_err = validate_tp_exchange_bounds(
        entry, synth_tp, asset_type, cfg
    )
    if not bounds_ok:
        return "ENGINE_B_RR_BELOW_MINIMUM_AFTER_REBASE"
    if not _engine_b_execution_tp_side_ok(sig, synth_tp, entry=entry):
        return "ENGINE_B_RR_BELOW_MINIMUM_AFTER_REBASE"

    new_rr = _geometric_rr(entry, sl, synth_tp)
    if new_rr is None or new_rr + 1e-12 < min_rr:
        return "ENGINE_B_RR_BELOW_MINIMUM_AFTER_REBASE"

    sig["tp1"] = synth_tp
    sig["tp2"] = synth_tp
    if str(sig.get("engine_b_execution_plan") or "").lower() == "scale_out":
        sig["engine_b_execution_plan"] = "single_structural_tp1"
        sig["engine_b_execution_plan_reason"] = "broker_rebase_rr_resynth"
    sig["engine_b_rr_required"] = min_rr
    sig["engine_b_rr_passed"] = True
    sig["engine_b_broker_rebase_rr_resynth"] = True
    sig["engine_b_broker_rebase_rr_before"] = round(rr_geom, 4)
    sig["engine_b_broker_rebase_rr_after"] = round(new_rr, 4)
    try:
        rt().log.warning(
            f"[EXEC] {sig.get('pair')}: broker entry rebase dropped RR to "
            f"{rr_geom:.3f}; resynthesized TP1={synth_tp:.6f} (RR {new_rr:.3f})"
        )
    except RuntimeError:
        pass
    return None


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


def _engine_b_execution_levels_marked_invalid(source: dict | None) -> bool:
    if not isinstance(source, dict):
        return False
    if source.get("execution_levels_valid") is False:
        return True
    return source.get("execution_level_reject_reason") in (
        "max_sl_exceeded",
        "tp_wrong_side",
        "levels_missing",
    )


def _engine_b_execution_tp_side_ok(
    sig: dict,
    tp: float,
    entry: float | None = None,
) -> bool:
    direction = str(sig.get("direction") or "").upper()
    try:
        px = float(
            entry if entry is not None else sig.get("price") or sig.get("entry") or 0
        )
        tp_f = float(tp)
    except (TypeError, ValueError):
        return False
    if px <= 0 or tp_f <= 0 or direction not in ("LONG", "SHORT"):
        return True
    if direction == "LONG":
        return tp_f > px
    return tp_f < px


def _apply_scale_out_tp_split(sig: dict, source: dict | None, levels: dict) -> None:
    """Upgrade collapsed tp1/tp2 to the resolver's dual-TP scale-out plan.

    Gated on the shared crypto venue capability the Bybit executor honours.
    Fail-closed: any missing, malformed, or mis-ordered field leaves the
    collapsed levels untouched. 2026-07-10 audit — without this, the scale-out
    plan the RR/space gates approved and the backtest models never reached the
    executors (they always saw tp1 == tp2 and fell back to single-TP).
    """
    if not isinstance(source, dict) or not isinstance(levels, dict):
        return
    try:
        if not engine_b_scaleout_execution_supported(
            sig.get("type") or sig.get("asset_type"), rt().CONFIG
        ):
            return
    except Exception:
        return
    strategy_raw = str(
        source.get("exit_strategy")
        or source.get("engine_b_exit_strategy")
        or sig.get("engine_b_exit_strategy")
        or ""
    )
    try:
        import exit_policy

        is_scale_out = (
            exit_policy.normalize_exit_strategy(strategy_raw)
            == exit_policy.EXIT_STRATEGY_SCALE_OUT
        )
    except Exception:
        is_scale_out = strategy_raw == "scale_out_structural_tp1_fallback_tp2"
    if not is_scale_out:
        return
    tp1_raw = source.get("execution_tp1")
    if tp1_raw is None:
        tp1_raw = source.get("engine_b_execution_tp1")
    tp2_raw = source.get("execution_tp2")
    if tp2_raw is None:
        tp2_raw = source.get("engine_b_execution_tp2")
    try:
        tp1 = float(tp1_raw)
        tp2 = float(tp2_raw)
    except (TypeError, ValueError):
        return
    if tp1 <= 0 or tp2 <= 0 or tp1 == tp2:
        return
    if not _engine_b_execution_tp_side_ok(sig, tp1):
        return
    if not _engine_b_execution_tp_side_ok(sig, tp2):
        return
    direction = str(sig.get("direction") or "").upper()
    if direction == "LONG":
        if not tp2 > tp1:
            return
    elif direction == "SHORT":
        if not tp2 < tp1:
            return
    else:
        return
    levels["tp1"] = tp1
    levels["tp2"] = tp2
    levels["execution_plan"] = "scale_out"


def _engine_b_tp1_rr_floor(sig: dict, cfg: dict) -> float:
    raw = cfg.get("ENGINE_B_TP1_MIN_RR", 0.3)
    if isinstance(raw, dict):
        raw = raw.get(str(sig.get("style") or "").lower(), raw.get("default", 0.3))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.3


def _collapse_engine_b_single_target_plan(sig: dict, source: dict | None, levels: dict) -> None:
    """Collapse non-crypto canonical scale-out payloads to validated TP1 only."""
    if not isinstance(source, dict) or not isinstance(levels, dict):
        return
    cfg = rt().CONFIG
    if engine_b_scaleout_execution_supported(sig.get("type") or sig.get("asset_type"), cfg):
        return
    if not bool(cfg.get("ENGINE_B_SINGLE_TARGET_STRUCTURAL_FALLBACK_ENABLED", False)):
        return
    plan = str(source.get("execution_plan") or source.get("engine_b_execution_plan") or sig.get("engine_b_execution_plan") or "").strip().lower()
    strategy = str(source.get("exit_strategy") or source.get("engine_b_exit_strategy") or "").strip().lower()
    direct_single = plan == "single_structural_tp1"
    stale_scale_out = strategy == "scale_out_structural_tp1_fallback_tp2"
    if not direct_single and not stale_scale_out:
        return
    if direct_single:
        if source.get("single_target_valid") is not True:
            return
        raw_tp = source.get("single_target_execution_tp")
        raw_rr = source.get("rr_actual", source.get("execution_rr1"))
        raw_floor = source.get("single_target_rr_floor", source.get("rr_required"))
    else:
        if source.get("structural_tp_valid") is not True or str(source.get("tp1_source") or "").lower() != "structural":
            return
        raw_tp = source.get("execution_tp1")
        raw_rr = source.get("execution_rr1")
        raw_floor = _engine_b_tp1_rr_floor(sig, cfg)
    try:
        tp, rr, floor = float(raw_tp), float(raw_rr), float(raw_floor)
    except (TypeError, ValueError):
        return
    if tp <= 0 or not _engine_b_execution_tp_side_ok(sig, tp) or rr + 1e-12 < floor:
        return
    levels["tp1"] = tp
    levels["tp2"] = tp
    levels["execution_plan"] = "single_structural_tp1"
    levels["execution_plan_reason"] = "non_crypto_structural_tp1" if direct_single else "stale_scale_out_collapsed"
    levels["rr_required"] = floor
    levels["rr_passed"] = True


def _stamp_engine_b_execution_plan(sig: dict, source: dict | None, levels: dict) -> None:
    """Carry resolver execution-plan metadata through execution and refresh."""
    if not isinstance(source, dict) or not isinstance(levels, dict):
        return
    plan = levels.get("execution_plan") or source.get("execution_plan")
    if plan:
        sig["engine_b_execution_plan"] = plan
    for key in (
        "execution_plan_reason",
        "rr_required",
        "rr_passed",
        "execution_sl",
        "execution_tp",
        "execution_tp1",
        "execution_tp2",
        "single_target_rr_floor",
        "single_target_execution_tp",
        "single_target_valid",
    ):
        value = levels.get(key, source.get(key))
        if value is not None:
            sig[f"engine_b_{key}"] = value


def _extract_engine_b_execution_levels(
    sig: dict,
    engine_b: dict | None = None,
) -> dict | None:
    sig = sig or {}
    has_b_context = _signal_has_engine_b_context(sig, engine_b)
    eb_status = (
        sig.get("engine_b_status")
        if isinstance(sig.get("engine_b_status"), dict)
        else None
    )

    def _levels_from(
        source: dict,
        *,
        allow_recommended: bool,
        allow_signal_levels: bool = False,
        block_stale_overlay: bool = False,
    ) -> dict | None:
        if block_stale_overlay and _engine_b_execution_levels_marked_invalid(eb_status):
            sl = eb_status.get("execution_sl") if eb_status else None
            tp = eb_status.get("execution_tp") if eb_status else None
        else:
            sl = source.get("execution_sl")
            if sl is None:
                sl = source.get("engine_b_execution_sl")
            tp = source.get("execution_tp")
            if tp is None:
                tp = source.get("engine_b_execution_tp")
        if allow_recommended and not _engine_b_execution_levels_marked_invalid(source):
            if sl is None:
                sl = source.get("recommended_stop_loss")
            if tp is None:
                tp = source.get("recommended_take_profit")
        if allow_signal_levels and (sl is None or tp is None):
            if sl is None:
                sl = source.get("sl")
            if tp is None:
                tp = source.get("tp1")
        try:
            sl_f = float(sl)
            tp_f = float(tp)
        except (TypeError, ValueError):
            return None
        if sl_f > 0 and tp_f > 0:
            if not _engine_b_execution_tp_side_ok(sig, tp_f):
                return None
            levels = {"sl": sl_f, "tp1": tp_f, "tp2": tp_f}
            # Config-gated scale-out upgrade (no-op unless
            # ENGINE_B_LIVE_SCALE_OUT_ENABLED and the source carries a valid
            # dual-TP plan) — see _apply_scale_out_tp_split.
            _apply_scale_out_tp_split(sig, source, levels)
            _collapse_engine_b_single_target_plan(sig, source, levels)
            _stamp_engine_b_execution_plan(sig, source, levels)
            return levels
        return None

    candidates: list[tuple[str, dict | None, bool, bool, bool]] = []
    if isinstance(engine_b, dict):
        candidates.append(("engine_b_param", engine_b, True, False, False))
    if isinstance(eb_status, dict):
        candidates.append(("engine_b_status", eb_status, True, False, False))
    naked_data = sig.get("naked_data")
    if isinstance(naked_data, dict):
        candidates.append(("naked_data", naked_data, True, False, False))
    candidates.append(("sig_overlay", sig, False, False, True))
    nested_engine_b = sig.get("engine_b")
    if isinstance(nested_engine_b, dict) and nested_engine_b is not engine_b:
        candidates.append(("sig.engine_b", nested_engine_b, True, False, False))
    if has_b_context:
        candidates.append(("sig_recommended", sig, True, True, True))

    for label, source, allow_recommended, allow_signal_levels, block_stale in candidates:
        if not isinstance(source, dict):
            continue
        if label in ("sig_overlay", "naked_data", "sig_recommended"):
            if sig.get("engine_b_confidence_passed") is False:
                continue
            if sig.get("engine_b_levels_gate_passed") is False:
                continue
        levels = _levels_from(
            source,
            allow_recommended=allow_recommended,
            allow_signal_levels=allow_signal_levels,
            block_stale_overlay=block_stale,
        )
        if levels:
            return levels
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
        from athena_app.services.market_state import market_state_offset_hours

        time_now = datetime.now(timezone.utc).timestamp()
        meta_by_tf: dict[str, dict] = {}
        for tf in ("H1", "H4", "D1"):
            lim = int(limits[tf])
            raw = fetch(pair_obj, tf, lim)
            tf_candles = list(extract_candles(raw) or [])
            offset_h = market_state_offset_hours(pair_obj, tf)
            base_meta = get_candle_fetch_meta(pair_obj, tf, lim) or {}
            meta_by_tf[tf] = _annotate_fetch_meta_with_bar_freshness(
                dict(base_meta) if isinstance(base_meta, dict) else {},
                tf_candles,
                tf,
                now=time_now,
                offset_hours=offset_h,
                live_feed=True,
                pair=pair_obj,
            )
        sig["candleFetchMeta"] = {
            "D1": meta_by_tf.get("D1") or {},
            "H4": meta_by_tf.get("H4") or {},
            "H1": meta_by_tf.get("H1") or {},
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
    _refresh_atr_freshness_at_execution(sig, cfg)
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
        # Route crypto pairs through the same Bybit/Binance resolver the scoring
        # path uses (_fetch_ab_crypto_signal_candles) so execution freshness is
        # evaluated on the same provider the signal was scored on. Non-crypto and
        # binance-feed pairs fall through to fetch_candles unchanged.
        crypto_signal_fetch = getattr(_r, "_fetch_ab_crypto_signal_candles", None)
        for tf in ("H1", "H4", "D1"):
            lim = int(limits[str(tf)])
            if callable(crypto_signal_fetch):
                raw, _sig_meta = crypto_signal_fetch(pair_obj, str(tf), lim, engine="A")
            else:
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

        from athena_app.services.market_state import market_state_offset_hours

        time_now = datetime.now(timezone.utc).timestamp()

        meta_by_tf: dict[str, dict] = {}
        for tf_u in ("D1", "H4", "H1"):
            tf_candles = list(candles.get(tf_u) or [])
            offset_h = market_state_offset_hours(pair_obj, tf_u)
            base_meta = get_candle_fetch_meta(pair_obj, tf_u, limits[tf_u]) or {}
            meta_by_tf[tf_u] = _annotate_fetch_meta_with_bar_freshness(
                dict(base_meta) if isinstance(base_meta, dict) else {},
                tf_candles,
                tf_u,
                now=time_now,
                offset_hours=offset_h,
                live_feed=True,
                pair=pair_obj,
            )
        sig["candleFetchMeta"] = {
            "D1": meta_by_tf.get("D1") or {},
            "H4": meta_by_tf.get("H4") or {},
            "H1": meta_by_tf.get("H1") or {},
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


def _refresh_atr_freshness_at_execution(sig: dict, cfg: dict) -> None:
    """Re-evaluate ATR freshness against execution time and active policy.

    Scan payloads persist ``atrFreshness`` as a point-in-time result.  Risk
    checks run later, so use the recorded ATR candle timestamp to update its
    age and evaluate the currently loaded policy immediately before execution.
    Missing or malformed timestamps become an unknown age, preserving the
    existing fail-closed behaviour when the ATR gate is enabled.
    """
    diagnostics_raw = sig.get("atrDiagnostics")
    if not isinstance(diagnostics_raw, dict):
        return

    from atr_diagnostics import derive_candle_age_seconds, evaluate_freshness_from_config

    diagnostics = dict(diagnostics_raw)
    atr_last_ts = diagnostics.get("atr_candle_last_ts")
    if atr_last_ts is None:
        diagnostics["atr_age_seconds"] = None
    else:
        _, age = derive_candle_age_seconds([{"time": atr_last_ts}])
        diagnostics["atr_age_seconds"] = (
            round(float(age), 3) if age is not None else None
        )

    sig["atrDiagnostics"] = diagnostics
    sig["atrFreshness"] = evaluate_freshness_from_config(
        diagnostics,
        cfg.get("ATR_FRESHNESS") if isinstance(cfg.get("ATR_FRESHNESS"), dict) else None,
    )


def _sync_engine_b_execution_price(sig: dict, engine_b: dict | None = None) -> None:
    """Fill missing entry/price from Engine B checklist payloads before risk sizing."""
    sig = sig or {}
    try:
        current = float(sig.get("price") or sig.get("entry") or 0)
    except (TypeError, ValueError):
        current = 0.0
    if current > 0:
        return
    for source in (
        sig.get("engine_b_status"),
        engine_b,
        sig.get("naked_data"),
        sig.get("engine_b"),
    ):
        if not isinstance(source, dict):
            continue
        for key in ("current_price", "price", "entry"):
            try:
                px = float(source.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if px > 0:
                sig["price"] = px
                sig["entry"] = px
                return


def _engine_b_execution_sl_exceeds_max(sig: dict, sl: float) -> bool:
    try:
        from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled

        if tp_sl_rr_gates_disabled(rt().CONFIG, signal=sig):
            return False
    except Exception:
        pass
    try:
        entry = float(sig.get("price") or sig.get("entry") or 0)
        sl_f = float(sl)
    except (TypeError, ValueError):
        return True
    if entry <= 0 or sl_f <= 0:
        return True
    from risk_engine import resolve_max_sl_pct

    asset_type = str(sig.get("type") or sig.get("asset_type") or "").lower()
    max_sl_pct, _ = resolve_max_sl_pct(sig, asset_type, rt().CONFIG)
    sl_pct = abs(entry - sl_f) / abs(entry)
    return sl_pct > float(max_sl_pct) + 1e-12


def _engine_b_levels_apply_error_response(sig: dict) -> tuple[dict, int]:
    if sig.get("_engine_b_apply_error") == "MAX_SL_EXCEEDED":
        return (
            {
                "error": "Risk Blocked: MAX_SL_EXCEEDED",
                "pair": sig.get("pair"),
            },
            400,
        )
    if sig.get("_engine_b_apply_error") == "INVALID_TP_SIDE":
        return (
            {
                "error": "ENGINE_B_LEVELS_UNAVAILABLE: Engine B take-profit on wrong side of entry",
                "pair": sig.get("pair"),
            },
            409,
        )
    if sig.get("_engine_b_apply_error") == "INVALID_TP_BOUNDS":
        return (
            {
                "error": "ENGINE_B_LEVELS_UNAVAILABLE: Engine B take-profit outside exchange/distance bounds",
                "pair": sig.get("pair"),
            },
            409,
        )
    return (
        {
            "error": "ENGINE_B_LEVELS_UNAVAILABLE: Engine B execution levels missing or invalid",
            "pair": sig.get("pair"),
        },
        409,
    )


def _apply_engine_b_execution_levels(sig: dict, engine_b: dict | None = None) -> bool:
    _sync_engine_b_execution_price(sig, engine_b)
    levels = _extract_engine_b_execution_levels(sig, engine_b)
    if not levels:
        return False
    _tp_sl_rr_relaxed = False
    try:
        from tp_sl_rr_gate_policy import tp_sl_rr_gates_disabled

        _tp_sl_rr_relaxed = tp_sl_rr_gates_disabled(rt().CONFIG, signal=sig)
    except Exception:
        _tp_sl_rr_relaxed = False
    if not _tp_sl_rr_relaxed and (
        not _engine_b_execution_tp_side_ok(sig, levels["tp1"])
        or not (
            levels["tp2"] == levels["tp1"]
            or _engine_b_execution_tp_side_ok(sig, levels["tp2"])
        )
    ):
        sig["_engine_b_apply_error"] = "INVALID_TP_SIDE"
        return False
    if not _tp_sl_rr_relaxed:
        try:
            from risk_engine import validate_tp_exchange_bounds

            _entry_px = float(sig.get("price") or sig.get("entry") or 0)
            _asset = str(sig.get("type") or sig.get("asset_type") or "").lower()
            _bounds_ok, _bounds_err = validate_tp_exchange_bounds(
                _entry_px, levels["tp1"], _asset, rt().CONFIG
            )
            # Scale-out plans carry a farther runner TP2 — it must clear exchange
            # bounds too (it is the level the position TP is actually parked at).
            if _bounds_ok and levels["tp2"] != levels["tp1"]:
                _bounds_ok, _bounds_err = validate_tp_exchange_bounds(
                    _entry_px, levels["tp2"], _asset, rt().CONFIG
                )
        except Exception:
            _bounds_ok, _bounds_err = True, None
        if not _bounds_ok:
            sig["_engine_b_apply_error"] = "INVALID_TP_BOUNDS"
            return False
    if _engine_b_execution_sl_exceeds_max(sig, levels["sl"]):
        sig["_engine_b_apply_error"] = "MAX_SL_EXCEEDED"
        return False
    sig.pop("_engine_b_apply_error", None)
    sig["sl"] = levels["sl"]
    sig["tp1"] = levels["tp1"]
    sig["tp2"] = levels["tp2"]
    if levels.get("execution_plan"):
        sig["engine_b_execution_plan"] = levels["execution_plan"]
    if levels.get("execution_plan_reason"):
        sig["engine_b_execution_plan_reason"] = levels["execution_plan_reason"]
    if levels.get("rr_required") is not None:
        sig["engine_b_rr_required"] = levels["rr_required"]
    if levels.get("rr_passed") is not None:
        sig["engine_b_rr_passed"] = levels["rr_passed"]
    sig["level_source"] = "engine_b_execution"
    return True


def _mt5_direction_preflight_error(
    sig: dict,
    symbol_info: dict,
    *,
    log_prefix: str = "[QUICK EXEC]",
) -> tuple[dict, int] | None:
    """Fail closed when MT5 symbol trade mode blocks the signal direction."""
    direction = str(sig.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    from mt5_executor import mt5_trade_block_for_direction

    block_reason, trade_state = mt5_trade_block_for_direction(symbol_info, direction)
    if not block_reason:
        return None
    pair_name = sig.get("pair") or sig.get("display") or sig.get("symbol") or "?"
    try:
        rt().log.warning(
            "%s %s: MT5 direction blocked (%s): %s",
            log_prefix,
            pair_name,
            block_reason,
            trade_state,
        )
    except RuntimeError:
        pass
    detail_by_reason = {
        "symbol_long_only": "Broker symbol is long-only; SHORT orders are not permitted.",
        "symbol_short_only": "Broker symbol is short-only; LONG orders are not permitted.",
        "symbol_close_only_no_new_positions": "Broker symbol is close-only; new positions are not permitted.",
        "symbol_trade_disabled": "Broker symbol trading is disabled.",
    }
    return (
        {
            "error": f"MT5_TRADE_DISABLED:{block_reason}",
            "pair": pair_name,
            "direction": direction,
            "detail": detail_by_reason.get(block_reason, block_reason),
            "tradeState": trade_state,
        },
        409,
    )


def _refresh_engine_b_execution_context(
    sig: dict,
    engine_b: dict | None,
    runtime,
    pip_mode: str | None,
) -> tuple[dict | None, str | None]:
    """Re-run Engine B before execution when a B signal aged during a scan.

    Engine B signals cannot be refreshed with Engine A's generic scorer because
    the stop/target and pass flag come from the structural checklist.
    """
    _trigger_block = _trigger_lifecycle_block_reason(sig)
    if _trigger_block:
        return None, _trigger_block

    refresh_fn = getattr(runtime, "compute_naked_analysis", None)
    if not callable(refresh_fn):
        return None, "ENGINE_B_REFRESH_REQUIRED: Engine B refresh function unavailable"

    seed = dict(sig or {})
    signal_engine_name = str(
        sig.get("engine") or sig.get("engine_source") or ""
    ).lower()
    top_level_engine_b_owned = bool(
        sig.get("is_naked") or signal_engine_name in {"b", "engine_b"}
    )
    policy_source = next(
        (
            candidate
            for candidate in (
                engine_b,
                sig.get("engine_b"),
                sig.get("naked_data"),
            )
            if isinstance(candidate, dict) and candidate.get("timeframePolicyHash")
        ),
        None,
    )
    if policy_source is None:
        if top_level_engine_b_owned:
            policy_source = sig
    if isinstance(policy_source, dict):
        for policy_key in (
            "timeframePolicyHash",
            "currentSpeedClass",
            "liveSpeedClass",
            "liquidityClass",
            "speedPercentile",
            "executionTf",
            "m5Role",
        ):
            if policy_source.get(policy_key) is not None:
                seed[policy_key] = policy_source[policy_key]
    if pip_mode:
        seed["style"] = pip_mode

    try:
        refreshed, _pair_obj, err = refresh_fn(seed, force_ai=False, execution_mode=True)
    except Exception as exc:
        return None, f"ENGINE_B_REFRESH_FAILED: {exc}"

    if err or not isinstance(refreshed, dict) or not refreshed:
        return None, f"ENGINE_B_REFRESH_FAILED: {err or 'no refreshed Engine B context'}"

    original_direction = str(sig.get("direction") or "").upper()
    refreshed_direction = str(refreshed.get("direction") or original_direction).upper()
    if original_direction and refreshed_direction and refreshed_direction != original_direction:
        return (
            None,
            f"ENGINE_B_SIGNAL_FLIPPED: {sig.get('pair')} is now {refreshed_direction} (was {original_direction})",
        )

    original_policy_hash = str(
        (policy_source or {}).get("timeframePolicyHash") or ""
    )
    refreshed_policy_hash = str(refreshed.get("timeframePolicyHash") or "")
    if original_policy_hash and refreshed_policy_hash != original_policy_hash:
        return None, "ENGINE_B_TF_POLICY_CHANGED: refreshed timeframe policy differs from scan"

    from timeframe_policy import policy_mode_is_authoritative

    if policy_mode_is_authoritative(None, getattr(runtime, "CONFIG", {}) or {}):
        readiness = str(refreshed.get("entryReadiness") or "UNAVAILABLE").upper()
        if readiness != "READY":
            detail = str(
                refreshed.get("entryReadinessReason")
                or "execution timeframe is not aligned"
            )
            return None, f"ENGINE_B_ENTRY_NOT_READY: {readiness}: {detail}"

    if not bool(refreshed.get("passed")):
        return None, "ENGINE_B_NOT_CONFIRMED: refreshed Engine B confirmation failed before execution"

    try:
        current_price = float(refreshed.get("current_price") or refreshed.get("price") or 0.0)
    except (TypeError, ValueError):
        current_price = 0.0
    if current_price <= 0:
        return None, "ENGINE_B_REFRESH_FAILED: refreshed Engine B context has no executable price"

    sl = (
        refreshed.get("execution_sl")
        or refreshed.get("final_stop_loss")
        or refreshed.get("recommended_stop_loss")
    )
    tp = (
        refreshed.get("execution_tp")
        or refreshed.get("final_take_profit")
        or refreshed.get("recommended_take_profit")
    )
    try:
        sl = float(sl)
        tp = float(tp)
    except (TypeError, ValueError):
        return None, "ENGINE_B_REFRESH_FAILED: refreshed Engine B context has no executable levels"
    if sl <= 0 or tp <= 0:
        return None, "ENGINE_B_REFRESH_FAILED: refreshed Engine B context has invalid executable levels"

    refreshed.setdefault("execution_sl", sl)
    refreshed.setdefault("execution_tp", tp)
    refreshed.setdefault("current_price", current_price)
    now_iso = datetime.now(timezone.utc).isoformat()

    if sig.get("signalPriceRef") is None:
        try:
            _orig = float(sig.get("price") or sig.get("entry") or 0)
            if _orig > 0:
                sig["signalPriceRef"] = _orig
        except (TypeError, ValueError):
            pass

    sig["is_naked"] = True
    sig["naked_data"] = refreshed
    sig["engine_b"] = refreshed
    if top_level_engine_b_owned:
        for policy_key in (
            "timeframePolicyHash",
            "entryReadiness",
            "entryReadinessReason",
            "executionTfActual",
            "executionTfConsumed",
            "executionTimingStatus",
            "executionTimingAligned",
            "executionTimingDirection",
            "executionTimingSource",
            "executionCandleTime",
        ):
            if refreshed.get(policy_key) is not None:
                sig[policy_key] = refreshed[policy_key]
    sig["price"] = current_price
    sig["entry"] = current_price
    sig["timestamp"] = now_iso
    sig["direction"] = refreshed_direction or original_direction
    sig["atr"] = refreshed.get("atr", sig.get("atr", 0))
    sig["confluenceScore"] = refreshed.get("score", sig.get("confluenceScore"))
    sig["maxScore"] = refreshed.get("max_possible", sig.get("maxScore"))
    sig["sl"] = sl
    sig["tp1"] = tp
    sig["tp2"] = tp
    if refreshed.get("exit_strategy"):
        sig["engine_b_exit_strategy"] = refreshed.get("exit_strategy")
    # Config-gated scale-out upgrade from the refreshed resolver payload
    # (no-op unless ENGINE_B_LIVE_SCALE_OUT_ENABLED and the plan is valid).
    _refresh_levels = {"sl": sl, "tp1": tp, "tp2": tp}
    _apply_scale_out_tp_split(sig, refreshed, _refresh_levels)
    _collapse_engine_b_single_target_plan(sig, refreshed, _refresh_levels)
    _stamp_engine_b_execution_plan(sig, refreshed, _refresh_levels)
    sig["tp1"] = _refresh_levels["tp1"]
    sig["tp2"] = _refresh_levels["tp2"]
    return refreshed, None


def _should_refresh_engine_b_before_execute(
    sig: dict,
    *,
    sig_age: float,
    max_age: float,
    missing_price: bool,
) -> bool:
    """Refresh Engine B before execute when policy timing must be revalidated."""
    if (
        sig.get("timeframePolicyVersion")
        or sig.get("executionTf")
        or sig.get("execution_tf_policy")
    ):
        return True
    if missing_price:
        return True
    if sig_age > max_age / 2:
        return True
    return str(sig.get("type") or "").lower() == "crypto"


def _engine_a_v3_live_entry_market_state(runtime, pair_obj: dict, entry_tf: str) -> dict | None:
    """Fetch the live lower-TF entry market state for the V3 execute-time refresh.

    The V3 active-entry gate and the pre-scoring freshness gate both demote a setup
    to WATCH when the configured live entry TF (e.g. M30 for intraday forex) has no
    forming (active) bar. MT5 can transiently omit the in-progress bar at a bucket
    boundary, so a single cold ``analyze_pair`` re-fetch at execute time can
    false-block a setup that was TRADE at scan time
    (ENGINE_A_V3_REFRESH_SCAN_TIER_NOT_TRADE). Give the terminal a brief moment to
    register a tick and refetch, mirroring the scan/Engine C preload. Returns the
    market-state dict (confirmed/forming/is_live), or ``None`` when the TF cannot be
    resolved so the caller falls back to the standard cold path.
    """
    from athena_app.services.market_state import get_tf_market_state

    try:
        limit = int(scan_candle_limits().get(entry_tf, 500))
    except Exception:
        limit = 500
    try:
        sleep_ms = int((getattr(runtime, "CONFIG", {}) or {}).get("MT5_H4_FETCH_RETRY_SLEEP_MS", 400) or 0)
    except Exception:
        sleep_ms = 400
    state: dict | None = None
    for attempt in range(3):  # initial fetch + up to two retries to recover a forming bar
        try:
            raw = runtime.fetch_candles(pair_obj, entry_tf, limit)
        except Exception:
            raw = None
        if not isinstance(raw, list):
            raw = []
        state = get_tf_market_state(pair_obj, entry_tf, candles=raw)
        if isinstance(state, dict) and state.get("forming"):
            return state
        if attempt < 2 and sleep_ms > 0:
            time.sleep(min(sleep_ms, 2000) / 1000.0)
    return state if isinstance(state, dict) else None


def _refresh_engine_a_v3_execution_context(
    sig: dict,
    runtime,
) -> str | None:
    from engine_a_v3.execution import (
        is_engine_a_v3_signal,
        merge_refreshed_signal,
        verify_refreshed_signal,
    )

    if not is_engine_a_v3_signal(sig):
        return None
    _trigger_block = _trigger_lifecycle_block_reason(sig)
    if _trigger_block:
        return _trigger_block
    pair_key = sig.get("pair") or sig.get("display") or sig.get("symbol") or ""
    pair_obj = next(
        (
            pair
            for pair in runtime.ALL_PAIRS
            if pair.get("display") == pair_key or pair.get("symbol") == pair_key
        ),
        None,
    )
    if pair_obj is None:
        return f"ENGINE_A_V3_REFRESH_PAIR_NOT_FOUND: {pair_key}"
    _style = str(sig.get("horizon") or sig.get("style") or "")
    # Preload the live lower-TF entry state (with the forming bar) so the execute-time
    # re-scan reproduces the scan's active-entry gate instead of false-blocking on a
    # cold fetch that momentarily omits the in-progress bar. Structure TFs (D1/H4/H1)
    # stay confirmed-only and are fetched by analyze_pair as before.
    _preloaded_state = None
    try:
        from engine_a_v3.timeframes import resolve_live_v3_entry_timeframe

        _entry_tf = resolve_live_v3_entry_timeframe(
            pair_obj.get("type", ""),
            _style,
            source=pair_obj.get("source"),
        )
        if _entry_tf and _entry_tf not in {"D1", "H4", "H1"}:
            _entry_state = _engine_a_v3_live_entry_market_state(runtime, pair_obj, _entry_tf)
            if isinstance(_entry_state, dict):
                _preloaded_state = {_entry_tf: _entry_state}
    except Exception:
        _preloaded_state = None
    try:
        from timeframe_policy import speed_state_from_policy_payload

        _execution_speed_state = (
            speed_state_from_policy_payload(sig)
            if sig.get("timeframePolicyHash")
            else None
        )
        _analysis_kwargs = {
            "style": _style,
            "preloaded_market_state": _preloaded_state,
        }
        if _execution_speed_state is not None:
            _analysis_kwargs["timeframe_speed_state"] = _execution_speed_state
        refreshed = runtime.analyze_pair(
            pair_obj,
            "neutral",
            **_analysis_kwargs,
        )
    except Exception as exc:
        return f"ENGINE_A_V3_REFRESH_FAILED: {exc}"
    original_policy_hash = str(sig.get("timeframePolicyHash") or "")
    refreshed_policy_hash = str((refreshed or {}).get("timeframePolicyHash") or "")
    if original_policy_hash and refreshed_policy_hash != original_policy_hash:
        return "ENGINE_A_V3_TF_POLICY_CHANGED: refreshed timeframe policy differs from scan"
    from timeframe_policy import policy_mode_is_authoritative

    if policy_mode_is_authoritative(None, getattr(runtime, "CONFIG", {}) or {}):
        readiness = str((refreshed or {}).get("entryReadiness") or "UNAVAILABLE").upper()
        if readiness != "READY":
            detail = str(
                (refreshed or {}).get("entryReadinessReason")
                or "execution timeframe is not aligned"
            )
            return f"ENGINE_A_V3_ENTRY_NOT_READY: {readiness}: {detail}"
    allowed, reason = verify_refreshed_signal(sig, refreshed)
    if not allowed:
        return reason or "ENGINE_A_V3_REFRESH_REJECTED"
    merged = merge_refreshed_signal(sig, refreshed)
    sig.clear()
    sig.update(merged)
    return None


def _engine_a_v3_demo_attestation_error(
    sig: dict,
    *,
    account: dict,
    venue: str,
    config: dict,
) -> str | None:
    from engine_a_v3.execution import attest_demo_execution, is_engine_a_v3_signal

    if not is_engine_a_v3_signal(sig):
        return None
    attestation = attest_demo_execution(
        executor_mode=config.get("EXECUTOR_MODE"),
        venue=venue,
        mt5_trade_mode=account.get("trade_mode") if venue == "mt5" else None,
        bybit_demo=account.get("demo") if venue == "bybit" else None,
    )
    if attestation.allowed:
        sig["demoExecutionAttestation"] = {
            "allowed": True,
            "venue": attestation.venue,
            "reason": attestation.reason,
        }
        return None
    return f"ENGINE_A_V3_DEMO_ATTESTATION_FAILED: {attestation.reason}"


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
    from engine_a_v3.execution import is_engine_a_v3_signal

    _is_engine_a_v3 = is_engine_a_v3_signal(sig)
    if _is_engine_a_v3 and level_override:
        return jsonify(
            {"error": "ENGINE_A_V3_LEVEL_OVERRIDE_FORBIDDEN", "pair": _quick_pair}
        ), 422

    pip_mode = normalize_pip_mode(d.get("pip_mode"))
    _volume_mode, _exec_context, _sizing_override = parse_execution_volume_args(d)
    if _is_engine_a_v3:
        _v3_horizon = str(sig.get("horizon") or "")
        if d.get("pip_mode") and pip_mode != _v3_horizon:
            return jsonify(
                {"error": "ENGINE_A_V3_HORIZON_OVERRIDE_FORBIDDEN", "pair": _quick_pair}
            ), 422
        sig["style"] = _v3_horizon
        _v3_refresh_error = _refresh_engine_a_v3_execution_context(sig, _r)
        if _v3_refresh_error:
            return jsonify({"error": _v3_refresh_error, "pair": _quick_pair}), 409
    else:
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
    _has_engine_b_context = (
        False if _is_engine_a_v3 else _signal_has_engine_b_context(sig, engine_b)
    )
    
    _missing_price = False
    try:
        if not sig.get("price") or float(sig.get("price")) <= 0:
            _missing_price = True
    except (TypeError, ValueError):
        _missing_price = True

    if _should_refresh_engine_b_before_execute(
        sig, sig_age=_sig_age, max_age=_max_age, missing_price=_missing_price
    ) and _has_engine_b_context:
        refreshed_engine_b, refresh_err = _refresh_engine_b_execution_context(
            sig, engine_b, _r, pip_mode
        )
        if refresh_err:
            return jsonify({"error": refresh_err, "pair": sig.get("pair")}), 409
        engine_b = refreshed_engine_b or engine_b
        _sig_age = 0
        _missing_price = False
    if _has_engine_b_context and not _engine_b_context_confirmed(sig, engine_b):
        return jsonify(
            {
                "error": "ENGINE_B_NOT_CONFIRMED: Engine B confirmation failed before execution",
                "pair": sig.get("pair"),
            }
        ), 409

    if (not _is_engine_a_v3) and (_sig_age > _max_age / 2 or _missing_price):
        pair = sig.get("pair", "")
        _pair_obj = next((p for p in _r.ALL_PAIRS if p["display"] == pair), None)
        if not _pair_obj:
            # Fail closed like /api/execute: a stale signal that cannot be
            # refreshed must never proceed with original client data.
            _r.log.warning(
                f"[QUICK EXEC] {pair}: pair not found in universe — rejecting stale signal"
            )
            return jsonify(
                {
                    "error": f"STALE_SIGNAL_REFRESH_FAILED: {pair} not found in universe — signal rejected",
                    "pair": pair,
                }
            ), 409
        if _pair_obj:
            try:
                _fresh = _r.analyze_pair(_pair_obj, "neutral", style=sig["style"])
                if _fresh:
                    _orig_dir = sig.get("direction", "")
                    _is_manual_override = bool(d.get("is_manual_override"))
                    _fresh_dir = _fresh.get("direction")
                    if _fresh_dir and _fresh_dir != _orig_dir:
                        if _is_manual_override:
                            _r.log.warning(
                                f"[QUICK EXEC] {pair}: manual override — direction updated "
                                f"{_orig_dir} -> {_fresh_dir}"
                            )
                            sig["direction"] = _fresh_dir
                            for _lvl_key in ("sl", "tp1", "tp2", "entry"):
                                if _fresh.get(_lvl_key) is not None:
                                    sig[_lvl_key] = _fresh[_lvl_key]
                        else:
                            return jsonify(
                                {
                                    "error": f"SIGNAL_FLIPPED: {pair} is now {_fresh_dir} (was {_orig_dir})",
                                    "newDirection": _fresh_dir,
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
                # Fail closed (parity with /api/execute): refresh failure on a
                # stale signal rejects instead of executing original client data.
                _r.log.warning(
                    f"[QUICK EXEC] {pair}: live refresh failed ({_fresh_err}) — rejecting stale signal"
                )
                return jsonify(
                    {
                        "error": f"STALE_SIGNAL_REFRESH_FAILED: {pair} live refresh failed — signal rejected",
                        "pair": pair,
                    }
                ), 409

    if str(sig.get("type") or "").lower() != "crypto":
        from mt5_executor import mt5_get_symbol_info

        _preflight_sym = mt5_get_symbol_info(sig.get("display") or sig.get("pair"))
        if _preflight_sym and not _preflight_sym.get("error"):
            _mt5_dir_block = _mt5_direction_preflight_error(sig, _preflight_sym)
            if _mt5_dir_block:
                return jsonify(_mt5_dir_block[0]), _mt5_dir_block[1]

    if _is_engine_a_v3:
        sig["level_source"] = "engine_a_v3_refresh"
    elif _has_engine_b_context:
        if _apply_engine_b_execution_levels(sig, engine_b):
            _r.log.warning(
                f"[QUICK EXEC] {sig.get('pair')}: preserved Engine B execution levels "
                f"SL={sig.get('sl')} TP1={sig.get('tp1')}"
            )
        else:
            body, status = _engine_b_levels_apply_error_response(sig)
            return jsonify(body), status
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

    if level_override and not _is_engine_a_v3:
        _override_err = _apply_level_override(sig, level_override)
        if _override_err:
            return jsonify({"error": f"Invalid AI level override: {_override_err}"}), 400
        _r.log.warning(
            f"[QUICK EXEC] {sig.get('pair')}: applied {sig.get('level_source')} levels "
            f"for style={sig.get('style')} SL={sig.get('sl')} TP1={sig.get('tp1')}"
        )

    is_crypto = sig.get("type") == "crypto"

    _pair_obj = next(
        (p for p in _r.ALL_PAIRS if p.get("display") == _quick_pair or p.get("symbol") == _quick_pair),
        None,
    )
    _ea_block = (
        None
        if _is_engine_a_v3
        else _engine_a_trade_gate_block_reason(sig, _pair_obj, config=_r.CONFIG)
    )
    if _ea_block:
        return jsonify({"error": _ea_block, "pair": sig.get("pair")}), 422

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
            _mt5_dir_block = _mt5_direction_preflight_error(sig, symbol_info)
            if _mt5_dir_block:
                return jsonify(_mt5_dir_block[0]), _mt5_dir_block[1]
            _exec_venue = "mt5"

        _demo_error = _engine_a_v3_demo_attestation_error(
            sig,
            account=account,
            venue=_exec_venue,
            config=_r.CONFIG,
        )
        if _demo_error:
            return jsonify({"error": _demo_error, "pair": sig.get("pair")}), 403

        _hydrate_execution_candle_quality(sig, _r=_r)

        # Engine A exit-mode selector (Plan 2): resolve mode + advisable-pip clamp
        # BEFORE risk_check, so the clamped SL/TP is sized and gated normally.
        # No-op for non-Engine-A signals (exit_mode stays unset -> monitor trails).
        if not _is_engine_a_v3:
            apply_engine_a_exit_mode(
                sig, _audit_engine_from_signal(sig), symbol_info, _r.CONFIG, level_override
            )

        _drift_error, _drift_diag = _engine_b_pre_risk_broker_price(
            sig, _exec_venue, _r.CONFIG
        )
        if _drift_error:
            return jsonify({"error": _drift_error, "brokerDrift": _drift_diag}), 409
        if _drift_diag:
            sig["brokerDriftPreRisk"] = _drift_diag

        _rr_reconcile_error = _reconcile_engine_b_rr_after_broker_entry(sig, _r.CONFIG)
        if _rr_reconcile_error:
            return jsonify({"error": _rr_reconcile_error, "pair": sig.get("pair")}), 409

        # Phase 5a: shared pre-order chase/displacement protection (both venues).
        _chase_error, _chase_diag = _displacement_guard_block_reason(sig, _r.CONFIG)
        sig["displacementGuard"] = _chase_diag
        if _chase_error:
            return jsonify({"error": _chase_error, "displacementGuard": _chase_diag}), 409

        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_r.kill_switch(),
            sizing_override=_sizing_override,
            account_domain=account.get("risk_domain"),
            volume_mode=_volume_mode,
            execution_context=_exec_context,
        )

        pair_name = sig.get("pair", sig.get("symbol", "N/A"))
        if not approval.approved:
            _r.log.warning(f"[QUICK EXEC] {pair_name} REJECTED: {approval.reason}")
            err_msg = f"Risk Blocked: {approval.reason}"
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
                _ai_grade, _ai_review_id = _resolve_ai_review_for_audit(sig, _r.AUDIT_DB)
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
                        _audit["asset_class"],
                        _leg.get("riskAmount"),
                        _leg.get("riskPct"),
                        str(_leg.get("ticket", "")),
                        result.get("feeCost"),
                        json.dumps(_audit["factors"]),
                        _audit["max_score"],
                        _audit["score_pct"],
                        sig.get("exit_mode"),
                        _ai_grade,
                        _ai_review_id,
                    ))
                with timed_sqlite_connect(
                    _r.AUDIT_DB, timeout=15.0, label="quick_execute.audit_success.connect"
                ) as con:
                    timed_sqlite_executemany_write(
                        con,
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,asset_class,risk_amount,risk_pct,ticket,fee_cost,factors_json,"
                        "max_score,score_pct,exit_mode,ai_review_grade,ai_review_id) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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

    # BTC bias is scan-wide (a pure function of BTC D1 candles) — compute it
    # once up front instead of once per crypto pair inside the loop.
    _btc_bias_crypto = "neutral"
    if any(str(p.get("type", "")).lower() == "crypto" for p in candidate_pairs):
        try:
            _btc_bias_crypto = _r.current_btc_bias()
        except Exception as _bias_err:
            _r.log.warning(
                "[ENGINE C] BTC bias unavailable, using neutral: %s", _bias_err
            )

    def _scan_pair_engines(pair):
        """Fetch candles and run the Engine A / Engine B legs for one pair.

        Worker-thread safe: no Flask request access, no shared scan-state
        mutation (NakedEngine registry context is threading.local, mirroring
        scanner.py's pooled use). Returns ("skip", skip_entry) or
        ("ok", payload); consensus scoring stays on the request thread.
        """
        _pair_start = time.time()
        try:
            symbol = pair.get("symbol", pair.get("display"))
            display = pair.get("display", symbol)
            ptype = pair.get("type", "")
            btc_bias = _btc_bias_crypto if ptype == "crypto" else "neutral"
            _pair_score_group = get_pair_score_group(pair)

            engine_a_style = _r.resolve_scan_style(
                _r.normalize_style(requested_style), pair
            )

            resolved_style_b, style_profile_b = _r.naked_scan_style_profile(
                requested_style,
                score_group=_pair_score_group,
                asset_type=ptype,
                symbol=pair.get("display") or pair.get("symbol") or "",
            )
            _dxy_h4_closes_b = None
            if ptype == "forex" and (
                bool(_r.CONFIG.get("ENGINE_B_DXY_MACRO_GATE_ENABLED", False))
                or bool(style_profile_b.get("macro_required", False))
            ):
                _dxy_fetch = getattr(_r, "get_dxy_h4_closes", None)
                if callable(_dxy_fetch):
                    try:
                        _dxy_h4_closes_b = _dxy_fetch()
                    except Exception as _dxy_err:
                        _r.log.warning(
                            "[ENGINE C] %s DXY history unavailable: %s",
                            display,
                            _dxy_err,
                        )

            _zone_tf = str(style_profile_b.get("zone_tf", "H4")).upper()
            _entry_tf = str(style_profile_b.get("entry_tf", "H1")).upper()
            _atr_tf = str(style_profile_b.get("atr_tf", _zone_tf)).upper()
            from engine_a_v3.timeframes import resolve_live_v3_entry_timeframe

            _a_entry_tf = resolve_live_v3_entry_timeframe(
                ptype,
                engine_a_style,
                source=pair.get("source"),
            )
            _lim = _scan_limits
            _im_h4 = (
                ((intermarket_snapshot or {}).get("seriesStore", {}) or {})
                .get(display, {})
                .get("candles")
            )
            # Crypto candles route through the same A/B signal-feed resolver as
            # scanner.py and api_scan_naked (ENGINE_AB_CRYPTO_SIGNAL_FEED, default
            # bybit) so Engine C scores the same venue as standalone A/B scans.
            # The intermarket H4 preload comes from fetch_candles (Binance for
            # crypto), so it is only reused when the Bybit feed is not active
            # (mirrors scanner._fetch_scan_h4_candles).
            _crypto_bybit_signal_feed = (
                str(ptype or "").lower() == "crypto"
                and resolve_crypto_signal_feed("AB", _r.CONFIG or {}) == "bybit"
            )
            _d1_res, _d1_meta = _fetch_engine_c_signal_candles(
                _r, pair, "D1", _lim["D1"]
            )
            if _im_h4 and not _crypto_bybit_signal_feed:
                _h4_res, _h4_meta = _im_h4, None
            else:
                _h4_res, _h4_meta = _fetch_engine_c_signal_candles(
                    _r, pair, "H4", _lim["H4"]
                )
            _h1_res, _h1_meta = _fetch_engine_c_signal_candles(
                _r, pair, "H1", _lim["H1"]
            )
            raw_candles = {
                "D1": _d1_res,
                "H4": _h4_res,
                "H1": _h1_res,
            }
            fetch_meta = {
                "D1": _d1_meta or get_candle_fetch_meta(pair, "D1", _lim["D1"]),
                "H4": _h4_meta or get_candle_fetch_meta(pair, "H4", _lim["H4"]),
                "H1": _h1_meta or get_candle_fetch_meta(pair, "H1", _lim["H1"]),
            }
            _live_lower_tfs = {
                tf
                for tf in (_a_entry_tf, _entry_tf)
                if tf and tf not in {"D1", "H4", "H1"}
            }
            for tf in _live_lower_tfs:
                raw, meta = _fetch_engine_c_signal_candles(_r, pair, tf, _lim[tf])
                raw_candles[tf] = raw
                fetch_meta[tf] = meta or get_candle_fetch_meta(pair, tf, _lim[tf])
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
                return "skip", _engine_c_skip_entry(
                    display,
                    "Rate limited",
                    code="rate_limited",
                    detail=f"Rate limited on {', '.join(rate_limited_tfs)}",
                )

            # Fetch candles ONCE — Engine A uses analyze_pair (confirmed inside);
            # Engine B uses bucket-aware confirmed series (scanner parity).
            _all_tfs_needed = {
                "D1", "H4", "H1", _zone_tf, _entry_tf, _atr_tf, _a_entry_tf
            } - {None}
            from athena_app.services.engine_b_market_state import (
                engine_b_confirmed_candles_from_raw,
            )

            _tf_map: dict[str, list] = {}
            _lower_state_map: dict[str, dict] = {}
            for tf in _all_tfs_needed:
                if tf in raw_candles:
                    raw = raw_candles.get(tf) or []
                else:
                    limit = _lim.get(tf, _lim.get("H4", 0))
                    raw, _ = _fetch_engine_c_signal_candles(_r, pair, tf, limit)
                if tf in _live_lower_tfs:
                    from athena_app.services.market_state import (
                        market_state_offset_hours,
                        split_market_state,
                    )

                    state = split_market_state(
                        list(raw or []),
                        tf,
                        pair.get("display") or pair.get("symbol") or "",
                        offset_hours=market_state_offset_hours(pair, tf),
                    )
                    _lower_state_map[tf] = state
                    active = list(state.get("confirmed") or [])
                    if state.get("forming"):
                        active.append(state["forming"])
                    _tf_map[tf] = active
                else:
                    _tf_map[tf] = engine_b_confirmed_candles_from_raw(pair, tf, raw)
            d1 = _tf_map.get("D1", [])
            h4 = _tf_map.get("H4", [])
            h1 = _tf_map.get("H1", [])
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
                preloaded_market_state=_lower_state_map,
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
                return "skip", _engine_c_skip_entry(
                    display,
                    "Timeout",
                    code="timeout",
                    detail=f"Exceeded {_EC_PAIR_TIMEOUT}s after candle fetch",
                )

            if len(zone_candles) < 10 or len(entry_candles) < 10:
                return "skip", _engine_c_skip_entry(
                    display,
                    "Insufficient data",
                    code="insufficient_data",
                    detail=f"{_zone_tf}={len(zone_candles)}, {_entry_tf}={len(entry_candles)}",
                )

            if _entry_tf in _live_lower_tfs:
                from athena_app.services.market_state import candle_freshness_diagnostic

                _entry_fresh = candle_freshness_diagnostic(
                    pair,
                    _entry_tf,
                    entry_candles,
                    source=pair.get("source"),
                )
                if str(_entry_fresh.get("stalenessSeverity") or "missing") != "fresh":
                    return "skip", _engine_c_skip_entry(
                        display,
                        "Stale entry candles",
                        code="stale_entry_tf",
                        detail=f"{_entry_tf}:{_entry_fresh.get('stalenessSeverity') or 'missing'}",
                    )

            from athena_app.services.engine_b_market_state import engine_b_live_gate_quote

            try:
                with _r.live_prices_lock:
                    _live_prices = dict(_r.live_prices)
                current_price, _live_quote_diag = engine_b_live_gate_quote(
                    pair,
                    _live_prices,
                    _r.CONFIG,
                )
            except (AttributeError, ValueError) as _quote_err:
                return "skip", _engine_c_skip_entry(
                    display,
                    "Fresh live quote unavailable",
                    code="live_quote_freshness_gate",
                    detail=str(_quote_err),
                )
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
                return "skip", _engine_c_skip_entry(
                    display,
                    "Zero ATR",
                    code="zero_atr",
                    detail=_atr_detail,
                )

            try:
                _atr_series_b = calc_atr(
                    [float(c["high"]) for c in atr_candles],
                    [float(c["low"]) for c in atr_candles],
                    [float(c["close"]) for c in atr_candles],
                    14,
                )
            except (KeyError, TypeError, ValueError):
                _atr_series_b = []
            _volatility_ok_b, _volatility_diag_b = engine_b_low_volatility_gate(
                atr,
                _atr_series_b,
                config_map=_r.CONFIG,
            )
            if not _volatility_ok_b:
                return "skip", _engine_c_skip_entry(
                    display,
                    "Low volatility",
                    code="volatility_gate",
                    detail=str(_volatility_diag_b),
                )

            _ec_d1_snap = {}
            _ec_h4_snap = {}
            try:
                from market_structure import resolve_engine_b_h4_snap

                _ec_d1_snap = (calc_indicators_with_normalized(d1, ptype) or {}).get("snap") or {}
                _ec_h4_snap = resolve_engine_b_h4_snap(h4, ptype)
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
                    h4 or [],
                    h1 or [],
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
                    dxy_h4_closes=_dxy_h4_closes_b,
                    **engine_b_live_trigger_kwargs(style_profile_b, _tf_map),
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

            return "ok", {
                "symbol": symbol,
                "display": display,
                "ptype": ptype,
                "pair_score_group": _pair_score_group,
                "engine_a_style": engine_a_style,
                "resolved_style_b": resolved_style_b,
                "zone_tf": _zone_tf,
                "entry_tf": _entry_tf,
                "atr_tf": _atr_tf,
                "sig_a": sig_a,
                "engine_a_returned_none": _engine_a_returned_none,
                "sig_b_best": sig_b_best,
                "conf_b_best": conf_b_best,
                "raw_sig_b": raw_sig_b,
                "raw_conf_b": raw_conf_b,
                "raw_b_direction": raw_b_direction,
                "raw_min_score_scaled": raw_min_score_scaled,
                "raw_gate_ok": raw_gate_ok,
                "current_price": current_price,
                "atr": atr,
                "regime_label": regime_label,
            }
        except Exception as e:
            _r.log.error(f"[ENGINE C] Error on {pair.get('display')}: {e}")
            return "skip", _engine_c_skip_entry(
                pair.get("display"),
                "Error",
                code="exception",
                detail=str(e),
            )

    # Per-pair fetch/analysis is REST-bound (Binance/Bybit/EODHD/MT5), which is
    # what made sequential crypto scans slow. Run pairs on a worker pool like
    # scanner.py does; consensus scoring and classification below stay on the
    # request thread and preserve original candidate order.
    _max_workers = min(
        len(candidate_pairs),
        get_optimal_workers(
            configured_max=int((_r.CONFIG or {}).get("SCAN_MAX_WORKERS", 8) or 8),
            conservative=True,
        ),
    )
    outcomes: list = [None] * len(candidate_pairs)
    if _max_workers <= 1:
        for _idx, _pair_item in enumerate(candidate_pairs):
            outcomes[_idx] = _scan_pair_engines(_pair_item)
    else:
        with ThreadPoolExecutor(max_workers=_max_workers) as _pool:
            _futures = {
                _pool.submit(_scan_pair_engines, _pair_item): _idx
                for _idx, _pair_item in enumerate(candidate_pairs)
            }
            for _fut in as_completed(_futures):
                outcomes[_futures[_fut]] = _fut.result()

    for pair, _outcome in zip(candidate_pairs, outcomes):
        _kind, _payload = _outcome
        if _kind == "skip":
            results["skipped"].append(_payload)
            continue
        try:
            symbol = _payload["symbol"]
            display = _payload["display"]
            ptype = _payload["ptype"]
            _pair_score_group = _payload["pair_score_group"]
            engine_a_style = _payload["engine_a_style"]
            resolved_style_b = _payload["resolved_style_b"]
            _zone_tf = _payload["zone_tf"]
            _entry_tf = _payload["entry_tf"]
            _atr_tf = _payload["atr_tf"]
            sig_a = _payload["sig_a"]
            _engine_a_returned_none = _payload["engine_a_returned_none"]
            sig_b_best = _payload["sig_b_best"]
            conf_b_best = _payload["conf_b_best"]
            raw_sig_b = _payload["raw_sig_b"]
            raw_conf_b = _payload["raw_conf_b"]
            raw_b_direction = _payload["raw_b_direction"]
            raw_min_score_scaled = _payload["raw_min_score_scaled"]
            raw_gate_ok = _payload["raw_gate_ok"]
            current_price = _payload["current_price"]
            atr = _payload["atr"]
            regime_label = _payload["regime_label"]

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
            # F2: surface ATR provenance on the consensus row so the consensus
            # decision can be audited end-to-end. Inherits from Engine A's
            # atrDiagnostics; sl_method already records which engine's level won.
            # Observability-only — does not influence verdict, sl, tp, or rr.
            _a_atr_diag = (
                sig_a.get("atrDiagnostics") if isinstance(sig_a, dict) else None
            )
            _b_atr_diag = (
                (sig_b_best or {}).get("atrDiagnostics")
                if isinstance(sig_b_best, dict)
                else None
            )
            consensus["atrDiagnostics"] = {
                "atr_value": round(atr, 6),
                "atr_tf": (
                    (_a_atr_diag or {}).get("atr_tf")
                    if isinstance(_a_atr_diag, dict)
                    else None
                ),
                "atr_source": (
                    (_a_atr_diag or {}).get("atr_source")
                    if isinstance(_a_atr_diag, dict)
                    else None
                ),
                "atr_source_engine": "engine_c",
                "atr_candle_last_ts": (
                    (_a_atr_diag or {}).get("atr_candle_last_ts")
                    if isinstance(_a_atr_diag, dict)
                    else None
                ),
                "atr_age_seconds": (
                    (_a_atr_diag or {}).get("atr_age_seconds")
                    if isinstance(_a_atr_diag, dict)
                    else None
                ),
                "engine_a_atr_diagnostics": _a_atr_diag if isinstance(_a_atr_diag, dict) else None,
                "engine_b_atr_diagnostics": _b_atr_diag if isinstance(_b_atr_diag, dict) else None,
                "sl_method": consensus.get("sl_method"),
                "tp_method": consensus.get("tp_method"),
            }
            try:
                from atr_diagnostics import evaluate_freshness_from_config
                consensus["atrFreshness"] = evaluate_freshness_from_config(
                    consensus["atrDiagnostics"],
                    (_r.CONFIG or {}).get("ATR_FRESHNESS"),
                )
            except Exception:
                pass
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
    from engine_a_v3.execution import is_engine_a_v3_signal

    _is_engine_a_v3 = is_engine_a_v3_signal(sig)

    pair = sig.get("pair", "")

    force = d.get("force", False) and _r.test_mode()
    if _is_engine_a_v3 and d.get("force"):
        return jsonify({"error": "ENGINE_A_V3_FORCE_FORBIDDEN", "pair": pair}), 422
    if _is_engine_a_v3 and level_override:
        return jsonify(
            {"error": "ENGINE_A_V3_LEVEL_OVERRIDE_FORBIDDEN", "pair": pair}
        ), 422
    if _is_engine_a_v3:
        _v3_refresh_error = _refresh_engine_a_v3_execution_context(sig, _r)
        if _v3_refresh_error:
            return jsonify({"error": _v3_refresh_error, "pair": pair}), 409

    sig_id = (
        str(sig.get("signalId"))
        if _is_engine_a_v3
        else f"{pair}_{sig.get('direction')}_{sig.get('timestamp', '')}"
    )

    if not force and is_signal_duplicate(sig_id):
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
    _has_engine_b_context = (
        False
        if _is_engine_a_v3
        else _signal_has_engine_b_context(sig, d.get("engine_b") or {})
    )

    _missing_price = False
    try:
        if not sig.get("price") or float(sig.get("price")) <= 0:
            _missing_price = True
    except (TypeError, ValueError):
        _missing_price = True

    if (not _is_engine_a_v3) and _has_engine_b_context:
        if _should_refresh_engine_b_before_execute(
            sig, sig_age=_sig_age, max_age=_max_age, missing_price=_missing_price
        ):
            refreshed_engine_b, refresh_err = _refresh_engine_b_execution_context(
                sig, d.get("engine_b") or {}, _r, normalize_pip_mode(sig.get("style"))
            )
            if refresh_err:
                return jsonify({"error": refresh_err, "pair": pair}), 409
            d["engine_b"] = refreshed_engine_b or d.get("engine_b") or {}
            _sig_age = 0
            _missing_price = False
    elif (not _is_engine_a_v3) and _sig_age > _max_age / 2:
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

    if _is_engine_a_v3:
        sig["level_source"] = "engine_a_v3_refresh"
    elif _has_engine_b_context:
        _levels_applied = _apply_engine_b_execution_levels(sig, d.get("engine_b") or {})
        if not _levels_applied:
            _r.log.warning(
                f"[EXEC] {pair}: Engine B execution levels not found — cannot execute structural signal"
            )
            body, status = _engine_b_levels_apply_error_response(sig)
            return jsonify(body), status

    if level_override and not _is_engine_a_v3:
        _override_err = _apply_level_override(sig, level_override)
        if _override_err:
            return jsonify({"error": f"Invalid AI level override: {_override_err}"}), 400
        _r.log.warning(
            f"[EXEC] {pair}: applied {sig.get('level_source')} levels "
            f"for style={sig.get('style')} SL={sig.get('sl')} TP1={sig.get('tp1')}"
        )

    _pair_obj = next(
        (p for p in _r.ALL_PAIRS if p.get("display") == pair or p.get("symbol") == pair),
        None,
    )
    _ea_block = (
        None
        if _is_engine_a_v3
        else _engine_a_trade_gate_block_reason(sig, _pair_obj, config=_r.CONFIG)
    )
    if _ea_block:
        return jsonify({"error": _ea_block, "pair": pair}), 422

    _exec_claimed = False
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

            _mt5_dir_block = _mt5_direction_preflight_error(
                sig, symbol_info, log_prefix="[EXEC]"
            )
            if _mt5_dir_block:
                return jsonify(_mt5_dir_block[0]), _mt5_dir_block[1]

            _exec_venue = "mt5"

        _demo_error = _engine_a_v3_demo_attestation_error(
            sig,
            account=account,
            venue=_exec_venue,
            config=_r.CONFIG,
        )
        if _demo_error:
            return jsonify({"error": _demo_error, "pair": pair}), 403

        _volume_mode, _exec_context, _payload_sizing = parse_execution_volume_args(d)
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
                _sizing_override = _payload_sizing
        _hydrate_execution_candle_quality(sig, _r=_r)

        # Engine A exit-mode selector (Plan 2): resolve mode + advisable-pip clamp
        # before risk_check (audit H1 — same as quick_execute path).
        if not _is_engine_a_v3:
            apply_engine_a_exit_mode(
                sig, _audit_engine_from_signal(sig), symbol_info, _r.CONFIG, level_override
            )

        _drift_error, _drift_diag = _engine_b_pre_risk_broker_price(
            sig, _exec_venue, _r.CONFIG
        )
        if _drift_error:
            return jsonify({"error": _drift_error, "brokerDrift": _drift_diag}), 409
        if _drift_diag:
            sig["brokerDriftPreRisk"] = _drift_diag

        _rr_reconcile_error = _reconcile_engine_b_rr_after_broker_entry(sig, _r.CONFIG)
        if _rr_reconcile_error:
            return jsonify({"error": _rr_reconcile_error, "pair": sig.get("pair")}), 409

        # Phase 5a: shared pre-order chase/displacement protection (both venues).
        _chase_error, _chase_diag = _displacement_guard_block_reason(sig, _r.CONFIG)
        sig["displacementGuard"] = _chase_diag
        if _chase_error:
            return jsonify({"error": _chase_error, "displacementGuard": _chase_diag}), 409

        approval = risk_check(
            signal=sig,
            account_balance=account["balance"],
            account_equity=account["equity"],
            open_positions=positions,
            symbol_info=symbol_info,
            kill_switch=_r.kill_switch(),
            sizing_override=_sizing_override,
            account_domain=account.get("risk_domain"),
            volume_mode=_volume_mode,
            execution_context=_exec_context,
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

        if not force and not begin_signal_execution(sig_id):
            return jsonify({"error": "DUPLICATE: This signal has already been executed"}), 409
        _exec_claimed = not force

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
                    _ai_grade, _ai_review_id = _resolve_ai_review_for_audit(sig, _r.AUDIT_DB)
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
                            sig.get("type") or sig.get("asset_class"),
                            _leg.get("riskAmount"),
                            _leg.get("riskPct"),
                            str(_leg.get("ticket", "")),
                            result.get("feeCost"),
                            json.dumps(_factors),
                            result.get("signalPriceRef"),
                            result.get("slippageBps"),
                            _eng_b_data.get("max_possible") if _audit_engine == "engine_b" else sig.get("maxScore"),
                            _eng_b_data.get("pct") if _audit_engine == "engine_b" else None,
                            sig.get("exit_mode"),
                            _ai_grade,
                            _ai_review_id,
                        ))
                    timed_sqlite_executemany_write(
                        con,
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,trend,grade,edge_prob,risk,style,"
                        "entry_price,sl,tp,volume,regime,asset_class,risk_amount,risk_pct,ticket,fee_cost,factors_json,"
                        "signal_price_ref,slippage_bps,max_score,score_pct,exit_mode,ai_review_grade,ai_review_id) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                if not force:
                    complete_signal_execution(sig_id)
                _r.log.info(
                    f"[EXEC] {pair} EXECUTED: ticket={result.get('ticket')}, volume={result.get('volume')}"
                )
        elif not force:
            abort_signal_execution(sig_id)

        if not result.get("success"):
            err = _log_execution_failure(_r.log, "EXEC", pair, _exec_venue, result)
            if isinstance(result, dict):
                result.setdefault("error", err)
            if result.get("error") == "AUDIT_PERSISTENCE_FAILED_AFTER_FILL":
                return jsonify(result), 500

        return jsonify(result)

    except Exception as e:
        if _exec_claimed:
            abort_signal_execution(sig_id)
        _r.log.exception(f"[EXEC] {pair or '?'} execution error: {e}")

        return jsonify({"error": "Execution failed — check logs"}), 500


def api_risk_preview():
    """Preview broker volume and dollar risk before manual execution."""
    _r = rt()
    d = request.get_json() or {}
    pair = d.get("pair") or d.get("display") or d.get("symbol") or ""
    if not pair:
        return jsonify({"error": "Missing pair/symbol"}), 400

    asset_type = str(d.get("type") or "").lower()
    if not asset_type:
        _pair_obj = next(
            (p for p in _r.ALL_PAIRS if p.get("display") == pair or p.get("symbol") == pair),
            None,
        )
        asset_type = str((_pair_obj or {}).get("type") or "forex").lower()

    try:
        entry = float(d.get("entry") or d.get("price") or 0)
        sl = float(d.get("sl") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid entry or sl"}), 400
    if entry <= 0 or sl <= 0:
        return jsonify({"error": "Missing entry or sl"}), 400

    _volume_mode, _exec_context, _sizing_override = parse_execution_volume_args(d)
    signal = {
        "pair": pair,
        "display": d.get("display") or pair,
        "symbol": d.get("symbol") or pair,
        "direction": d.get("direction") or "LONG",
        "price": entry,
        "entry": entry,
        "sl": sl,
        "type": asset_type,
        "confluenceScore": d.get("confluenceScore"),
        "maxScore": d.get("maxScore"),
        "combinedConviction": d.get("combinedConviction"),
        "executionConvictionEffective": d.get("executionConvictionEffective"),
    }

    try:
        if asset_type == "crypto":
            from bybit_executor import bybit_get_account, bybit_get_symbol_info

            account = bybit_get_account()
            if not account or account.get("error"):
                return jsonify({"error": "Bybit not connected"}), 503
            symbol_info = bybit_get_symbol_info(pair)
        else:
            from mt5_executor import mt5_get_account, mt5_get_symbol_info

            account = mt5_get_account()
            if not account or account.get("error"):
                return jsonify({"error": "MT5 not connected"}), 503
            symbol_info = mt5_get_symbol_info(d.get("display") or pair)

        if not symbol_info or symbol_info.get("error"):
            return jsonify({"error": "Symbol not on broker"}), 400

        from risk_engine import preview_execution_volume

        preview = preview_execution_volume(
            signal=signal,
            account_balance=account["balance"],
            symbol_info=symbol_info,
            volume_mode=_volume_mode,
            execution_context=_exec_context,
            sizing_override=_sizing_override,
        )
        preview["pair"] = pair
        preview["asset_type"] = asset_type
        return jsonify(preview)
    except Exception as e:
        _r.log.warning(f"[RISK PREVIEW] {pair}: {e}")
        return jsonify({"error": str(e)}), 500


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
    if "/api/scalp-paper-execute" not in rules:
        app.add_url_rule(
            "/api/scalp-paper-execute",
            "api_scalp_paper_execute",
            api_scalp_paper_execute,
            methods=["POST"],
        )
    if "/api/risk-preview" not in rules:
        app.add_url_rule(
            "/api/risk-preview",
            "api_risk_preview",
            api_risk_preview,
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
        return jsonify(_json_safe(out))
    except Exception as e:
        rt().log.error(f"[SCALP API] Scan error: {e}")
        return jsonify({"error": str(e)}), 500


def api_scalp_paper_execute():
    """Paper-only Engine D scalp execution logger.

    This path never calls broker account/order APIs. It revalidates the setup
    against a fresh scalp scan, runs deterministic risk and guardian checks,
    then writes an audit-only paper ticket.
    """
    _r = rt()
    d = request.get_json() or {}
    cfg = _r.CONFIG or {}
    paper_cfg = cfg.get("PAPER_SOAK") if isinstance(cfg.get("PAPER_SOAK"), dict) else {}

    if bool(cfg.get("REAL_ORDERS_ALLOWED", False)) or bool(paper_cfg.get("REAL_ORDERS_ALLOWED", False)):
        return jsonify({"success": False, "error": "REAL_ORDERS_ALLOWED is true - paper endpoint blocked"}), 403
    if not bool(paper_cfg.get("ENABLED", False)):
        return jsonify({"success": False, "error": "PAPER_SOAK.ENABLED is false - paper mode is off"}), 403

    symbol = str(d.get("symbol") or "").strip()
    client_signal = d.get("signal") if isinstance(d.get("signal"), dict) else None
    if not symbol:
        return jsonify({"success": False, "error": "Missing symbol"}), 400

    try:
        from scalp_engine import rebase_scalp_signal_levels, run_scalp_scan
        from risk_engine import risk_check

        scan = run_scalp_scan([symbol])
        raw_signals = scan.get("signals", []) or []
        if not raw_signals:
            reason = scan.get("reason") or "No valid scalp setup found"
            skipped = scan.get("skipped", []) or []
            if skipped:
                reason = skipped[0].get("reason", reason)
            return jsonify(
                {
                    "success": False,
                    "error": reason,
                    "skipped": skipped,
                    "fresh_scan": {
                        "signals": [],
                        "skipped": skipped,
                        "session": scan.get("session"),
                        "reason": scan.get("reason"),
                    },
                }
            ), 200

        if client_signal:
            requested_symbol = str(
                client_signal.get("symbol")
                or client_signal.get("pair")
                or client_signal.get("display")
                or ""
            ).strip()
            requested_direction = str(client_signal.get("direction") or "").upper()
            if requested_symbol and requested_symbol != symbol:
                return jsonify({"success": False, "error": "Signal symbol mismatch"}), 200
            if requested_direction not in ("LONG", "SHORT"):
                return jsonify({"success": False, "error": "Signal direction missing"}), 200
            signal = next(
                (
                    s
                    for s in raw_signals
                    if str(s.get("pair") or "").strip() == symbol
                    and str(s.get("direction") or "").upper() == requested_direction
                ),
                None,
            )
            if signal is None:
                fresh_dirs = sorted(
                    {
                        str((s.get("direction") or "")).upper()
                        for s in raw_signals
                        if s.get("direction")
                    }
                )
                if fresh_dirs:
                    return jsonify(
                        {
                            "success": False,
                            "error": (
                                f"SIGNAL_FLIPPED: {symbol} is now {'/'.join(fresh_dirs)} "
                                f"(was {requested_direction})"
                            ),
                            "newDirection": fresh_dirs[0] if len(fresh_dirs) == 1 else fresh_dirs,
                        }
                    ), 200
                return jsonify(
                    {
                        "success": False,
                        "error": f"SETUP_INVALIDATED: {symbol} {requested_direction} is no longer a valid scalp setup",
                    }
                ), 200
        else:
            signal = raw_signals[0]

        signal, _rebase_error = rebase_scalp_signal_levels(signal, symbol, {})
        if _rebase_error:
            return jsonify({"success": False, "error": _rebase_error, "fresh_scan": scan}), 400

        review_id = str(d.get("review_id") or d.get("ai_review_id") or "").strip() or None
        block_reason = _engine_d_paper_demo_execution_block_reason(
            signal,
            review_id=review_id,
            audit_db=getattr(_r, "AUDIT_DB", None),
            cfg=cfg,
            require_ai_review=_payload_require_ai_review(d),
        )
        if block_reason:
            return jsonify(
                {
                    "success": False,
                    "error": block_reason,
                    "skipped": [
                        {
                            "pair": signal.get("pair") or symbol,
                            "reason": block_reason,
                            "gate_result": signal.get("gate_result"),
                            "ai_grade": signal.get("ai_grade"),
                            "ai_score": signal.get("ai_score"),
                            "fail_reasons": signal.get("fail_reasons", []),
                        }
                    ],
                    "fresh_scan": scan,
                }
            ), 200

        _volume_mode, _exec_context, _ = parse_execution_volume_args(d)
        _sizing_override = merge_scalp_sizing_override(_volume_mode, d, signal)
        _hydrate_execution_candle_quality(signal, _r=_r)

        paper_balance = float(paper_cfg.get("ACCOUNT_BALANCE", 10000.0) or 10000.0)
        approval = risk_check(
            signal=signal,
            account_balance=paper_balance,
            account_equity=paper_balance,
            open_positions=[],
            symbol_info=None,
            kill_switch=_r.kill_switch(),
            sizing_override=_sizing_override,
            account_domain="paper",
            volume_mode=_volume_mode,
            execution_context=_exec_context,
        )
        if not approval.approved:
            return jsonify(
                {
                    "success": False,
                    "error": f"Risk engine rejected: {approval.reason}",
                    "approval": approval.to_dict(),
                }
            ), 409

        account = {"balance": paper_balance, "equity": paper_balance, "risk_domain": "paper"}
        positions_resp = {"positions": []}
        _ptc_ok, _ptc_reason = _guardian_pre_trade(signal, [], account, positions_resp)
        if not _ptc_ok:
            return jsonify({"success": False, "error": f"Guardian: {_ptc_reason}"}), 409

        ticket = f"paper-scalp-{uuid.uuid4().hex[:12]}"
        try:
            _factors_json = json.dumps(
                {
                    "paper_execute": True,
                    "vp_poc": signal.get("vp_poc"),
                    "vp_vah": signal.get("vp_vah"),
                    "vp_val": signal.get("vp_val"),
                    "ai_score": signal.get("ai_score"),
                    "soft_warnings": signal.get("soft_warnings", []),
                    "candidate_status": signal.get("candidate_status"),
                }
            )
            with timed_sqlite_connect(
                _r.AUDIT_DB, timeout=15.0, label="scalp_paper_execute.audit.connect"
            ) as con:
                timed_sqlite_execute_write(
                    con,
                    "INSERT INTO audit_log("
                    "ts,pair,score,max_score,engine,direction,grade,risk,style,"
                    "entry_price,sl,tp,tp_partial,tp2,volume,ticket,risk_amount,risk_pct,"
                    "asset_class,regime,factors_json"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        signal.get("pair") or symbol,
                        signal.get("confluenceScore") or signal.get("ai_score"),
                        1.0,
                        "scalp",
                        signal.get("direction"),
                        "SCALP-PAPER-EXECUTE",
                        f"${approval.risk_amount}",
                        "paper",
                        signal.get("price") or signal.get("entry"),
                        signal.get("sl"),
                        signal.get("tp1") or signal.get("tp"),
                        signal.get("tp_partial"),
                        signal.get("tp2"),
                        getattr(approval, "volume", None),
                        ticket,
                        approval.risk_amount,
                        approval.risk_pct,
                        signal.get("type"),
                        signal.get("market_state"),
                        _factors_json,
                    ),
                    label="scalp_paper_execute.audit.insert",
                )
        except Exception as audit_exc:
            _r.log.warning(f"[SCALP PAPER EXEC] audit_log insert failed: {audit_exc}")
            return jsonify({"success": False, "error": f"AUDIT_INSERT_FAILED: {audit_exc}"}), 500

        return jsonify(
            {
                "success": True,
                "ticket": ticket,
                "volume": getattr(approval, "volume", None),
                "entry_price": signal.get("price") or signal.get("entry"),
                "approval": approval.to_dict(),
                "message": "Scalp paper execution logged - no broker order placed",
            }
        )
    except Exception as e:
        _r.log.error(f"[SCALP PAPER EXEC] Execute error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def api_scalp_execute():
    """Execute a scalp signal after passing risk check (MT5 or Bybit for crypto)."""
    _r = rt()
    d = request.get_json() or {}
    sig = d.get("signal")
    _volume_mode, _exec_context, _ = parse_execution_volume_args(d)
    _sizing_override = merge_scalp_sizing_override(_volume_mode, d, sig or {})

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

        from scalp_engine import rebase_scalp_signal_levels

        sig, _rebase_error = rebase_scalp_signal_levels(sig, pair_key, symbol_info)
        if _rebase_error:
            return jsonify({"error": _rebase_error}), 400

        review_id = str(d.get("review_id") or d.get("ai_review_id") or "").strip() or None
        from ai_scalp_review.execute_gate import engine_d_signal_hard_block

        hard_block_reason = engine_d_signal_hard_block(sig)
        if _requested_scalp_execution_mode(d) == "paper":
            return jsonify({"error": "USE_SCALP_PAPER_EXECUTE", "success": False}), 400
        if _scalp_demo_execution_requested(_exec_venue, _r.CONFIG):
            _block_reason = _engine_d_paper_demo_execution_block_reason(
                sig,
                review_id=review_id,
                audit_db=getattr(_r, "AUDIT_DB", None),
                cfg=_r.CONFIG,
                require_ai_review=_payload_require_ai_review(d),
            )
        else:
            _block_reason = _engine_d_execution_block_reason(
                sig,
                review_id=review_id,
                audit_db=getattr(_r, "AUDIT_DB", None),
                cfg=_r.CONFIG,
            )
        if _block_reason:
            body = {"error": _block_reason, "success": False}
            if hard_block_reason:
                body["hardBlockReason"] = hard_block_reason
            return jsonify(body), 400

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
            account_domain=account.get("risk_domain"),
            volume_mode=_volume_mode,
            execution_context=_exec_context,
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
                        "INSERT INTO audit_log(ts,pair,score,engine,direction,grade,risk,style,entry_price,sl,tp,volume,ticket,risk_amount,risk_pct,asset_class) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                            approval.risk_pct,
                            sig.get("type") or sig.get("asset_class"),
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
