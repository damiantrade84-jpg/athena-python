"""Single ASE inference path (v2.1 §8)."""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Sequence

import numpy as np

from athena_ase.artifacts.loader import ArtifactBundle, load_artifact_bundle, verify_manifest_hashes
from athena_ase.contracts import ASESignal, error_signal, flat_signal
from athena_ase.features.build import (
    FEATURE_SCHEMA_CORE,
    FEATURE_SCHEMA_ENRICHED,
    CATEGORICAL_FEATURES,
    FeatureBuildContext,
    build_features_for_candidate,
    categorical_code,
    cot_asset_for_symbol,
)
from athena_ase.gates.demo_only import assert_demo
from athena_ase.horizon import Horizon, K_TP
from athena_ase.inference.decision import (
    apply_decision_rule,
    legacy_expected_r_strength,
    signal_strength as compute_signal_strength,
)
from athena_ase.inference.monitor import DECISION_TIME_KEY, apply_watch_max_cap, evaluate_monitor
from athena_ase.instruments import Instrument
from athena_ase.levels.brackets import compute_brackets
from athena_ase.registry.promotion import is_watch_max, set_watch_max
from athena_ase.settings import (
    monitor_max_live_rows,
    payoff_realized_cap,
    payoff_realized_cap_enabled,
    payoff_use_realized_exit_r,
    payoff_win_quantile,
    arbitration_strength_floor,
)
from athena_ase.inference.realized_payoff import (
    journal_calibration_snapshot,
    realized_exit_payoff,
)
from athena_ase.inference.routing import route_for_feeds
from athena_ase.signals.arbitrate import Candidate
from athena_ase.signals.common import live_bars_stale, load_bar_series

log = logging.getLogger("ase.predict")

ENGINE_VERSION = "2.1.0"


def _direction_label(value: int | str | None) -> str:
    if isinstance(value, str):
        up = value.strip().upper()
        if up in ("LONG", "SHORT", "NONE"):
            return up
    if value in (1, "1", "+1"):
        return "LONG"
    if value in (-1, "-1"):
        return "SHORT"
    return "NONE"


def _sig_value(signals: list[dict[str, Any]], name: str) -> float:
    for s in signals:
        if str(s.get("name")) == name:
            direction = float(s.get("direction") or 0)
            strength = float(s.get("rawStrength") or 0)
            return direction * strength
    return 0.0


def _sig_context_value(
    signals: list[dict[str, Any]], name: str, field: str
) -> float:
    for signal in signals:
        if str(signal.get("name")) != name:
            continue
        try:
            return float(signal.get(field))
        except (TypeError, ValueError):
            return float("nan")
    return float("nan")


def _predict_proba(model: Any, features: np.ndarray) -> float:
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(features.reshape(1, -1))[0]
        return float(proba[-1]) if len(proba) else 0.5
    if hasattr(model, "decision_function"):
        score = float(model.decision_function(features.reshape(1, -1))[0])
        return 1.0 / (1.0 + math.exp(-score))
    pred = model.predict(features.reshape(1, -1))
    return float(pred[0])


def _calibrate(calibrator: Any | None, raw_p: float) -> float:
    if calibrator is None:
        return raw_p
    try:
        return float(calibrator.predict(np.array([raw_p]))[0])
    except Exception:
        return raw_p


def _predict_quantiles(heads: dict[str, Any] | None, features: np.ndarray, target: str) -> dict[str, float]:
    out: dict[str, float] = {}
    if not heads:
        return out
    block = heads.get(target) or {}
    for q_label, reg in block.items():
        try:
            out[q_label] = float(reg.predict(features.reshape(1, -1))[0])
        except Exception:
            continue
    keys = sorted(out.keys())
    vals = sorted(out[k] for k in keys)
    for k, v in zip(keys, vals):
        out[k] = v
    return out


def _decision_status(
    *,
    expected_net_r: float,
    p_cal: float,
    thr_family: float,
    core_ok: bool,
) -> str:
    return apply_decision_rule(
        p_cal=p_cal,
        expected_net_r=expected_net_r,
        thr_family=thr_family,
        data_quality={"coreOk": core_ok},
    )


def _expected_net_r(
    p_cal: float,
    mfe_q: dict[str, float],
    mae_q: dict[str, float],
    *,
    family: str | None = None,
    horizon: Horizon | None = None,
    realized_expectancy: float | None = None,
) -> float:
    if payoff_use_realized_exit_r() and family and horizon:
        rp = realized_exit_payoff(family, horizon)
        if rp is not None:
            modeled = p_cal * rp.e_win + (1.0 - p_cal) * rp.e_loss
            if payoff_realized_cap_enabled() and realized_expectancy is not None:
                try:
                    cap = float(realized_expectancy) * payoff_realized_cap()
                    return min(modeled, cap)
                except (TypeError, ValueError):
                    return modeled
            return modeled

    win_q = payoff_win_quantile()
    e_win = min(_q(mfe_q, win_q, _q(mfe_q, "q50", 0.5)), K_TP)
    e_loss = -max(_q(mae_q, "q50", 0.25), 0.25)
    modeled = p_cal * e_win + (1.0 - p_cal) * e_loss
    if payoff_realized_cap_enabled() and realized_expectancy is not None:
        try:
            cap = float(realized_expectancy) * payoff_realized_cap()
            return min(modeled, cap)
        except (TypeError, ValueError):
            return modeled
    return modeled


def _layer1_confluence(signals: list[dict[str, Any]], direction: int) -> int:
    floor = arbitration_strength_floor()
    secondary = frozenset({"carry", "xsec", "meanrev"})
    return sum(
        1
        for s in signals
        if str(s.get("name")) in secondary
        and int(s.get("direction") or 0) == direction
        and float(s.get("rawStrength") or 0) >= floor
    )


def _q(quantiles: dict[str, float], key: str, default: float) -> float:
    if key in quantiles:
        return float(quantiles[key])
    return default


def _row_to_vector(
    row: dict[str, Any],
    schema: tuple[str, ...],
    zero_fill: frozenset[str] = frozenset(),
) -> np.ndarray:
    vec = np.empty(len(schema), dtype=float)
    for j, name in enumerate(schema):
        val = row.get(name)
        if name in CATEGORICAL_FEATURES:
            vec[j] = categorical_code(val)
        elif isinstance(val, (int, float)):
            vec[j] = float(val)
        else:
            vec[j] = float("nan")
        if vec[j] != vec[j] and name in zero_fill:
            # Column was entirely NaN at train time and 0.0-filled there;
            # apply the identical fill or HGB split routing diverges.
            vec[j] = 0.0
    return vec


def _build_context(
    candidate: Candidate,
    instrument: Instrument,
    *,
    commodity_macro_enabled: bool = True,
) -> FeatureBuildContext:
    benchmark_symbol = instrument.benchmark
    cot_asset = cot_asset_for_symbol(instrument.symbol)
    if instrument.family == "commodity" and not commodity_macro_enabled:
        # Preserve old artifact parity: every legacy commodity bundle was
        # trained with XAUUSD as benchmark and without XAUZAR COT coverage.
        benchmark_symbol = "XAUUSD"
        if instrument.symbol == "XAUZAR":
            cot_asset = ""
    return FeatureBuildContext(
        symbol=instrument.symbol,
        family=instrument.family,
        subclass=instrument.subclass,
        horizon=candidate.horizon,
        decision_time_ms=candidate.decision_time_ms,
        direction=candidate.direction,
        agreement_count=candidate.agreement_count,
        conflict_flag=candidate.conflict_flag,
        sig_tsmom=_sig_value(candidate.signals, "tsmom"),
        sig_carry=_sig_value(candidate.signals, "carry"),
        sig_xsec=_sig_value(candidate.signals, "xsec"),
        sig_mr=_sig_value(candidate.signals, "meanrev"),
        sig_aggregate=float(candidate.aggregate_strength) * float(candidate.direction),
        conflict_score=float(candidate.conflict_score),
        layer1_confluence=_layer1_confluence(candidate.signals, candidate.direction),
        xsec_pct=_sig_context_value(candidate.signals, "xsec", "percentile"),
        family_dispersion=_sig_context_value(candidate.signals, "xsec", "dispersion"),
        benchmark_symbol=benchmark_symbol,
        cot_asset=cot_asset,
        commodity_macro_enabled=commodity_macro_enabled,
    )


def predict_one(
    candidate: Candidate,
    store: Any,
    instrument: Instrument,
    *,
    bundle: ArtifactBundle | None = None,
    monitor_reference: dict[str, np.ndarray] | None = None,
    live_feature_rows: list[dict[str, float]] | None = None,
    feature_audit_rows: list[dict[str, Any]] | None = None,
    live: bool = True,
) -> ASESignal:
    family = instrument.family
    horizon: Horizon = candidate.horizon
    gate = assert_demo()
    if not gate.ok:
        return error_signal(
            instrument=candidate.instrument,
            family=family,
            horizon=horizon,
            reason="demo_gate_failed",
            gate_result=gate.to_dict(),
        )

    bundle = bundle or load_artifact_bundle(family, horizon)
    if bundle is None:
        return flat_signal(
            instrument=candidate.instrument,
            family=family,
            horizon=horizon,
            model_version="none",
            gate_result=gate.to_dict(),
            data_quality={
                "coreOk": False,
                "route": "none",
                "missingFeeds": [],
                "blocker": "model_not_trained",
                "artifactsPresent": False,
                "deployment": "OPERATIONAL",
            },
            model_health={
                "artifactHash": "",
                "trainedAt": "",
                "brier": None,
                "driftScore": 0.0,
                "gateResult": gate.to_dict(),
                "blocker": "model_not_trained",
                "errorReason": "artifact_missing",
            },
            primary_signals=list(candidate.signals),
        )

    hash_errors = verify_manifest_hashes(bundle.root, bundle.manifest)
    if hash_errors:
        return error_signal(
            instrument=candidate.instrument,
            family=family,
            horizon=horizon,
            reason=f"artifact_integrity:{hash_errors[0]}",
            gate_result=gate.to_dict(),
            model_version=bundle.version,
        )

    enriched_schema = tuple((bundle.manifest.feature_schemas or {}).get("enriched") or ())
    commodity_macro_enabled = (
        family != "commodity" or "usd_index_ret_21" in enriched_schema
    )
    ctx = _build_context(
        candidate,
        instrument,
        commodity_macro_enabled=commodity_macro_enabled,
    )
    has_enriched_model = bundle.model_enriched is not None
    row_probe = build_features_for_candidate(
        ctx, enriched=has_enriched_model, store=store
    )
    routing = route_for_feeds(
        core_missing=ctx.missing_feeds,
        enriched_missing=ctx.enriched_missing_feeds,
    )
    route = "enriched" if has_enriched_model and routing["route"] == "enriched" else "core"
    row = (
        row_probe
        if has_enriched_model and route == "enriched"
        else build_features_for_candidate(ctx, enriched=False, store=store)
    )
    schema: tuple[str, ...] = FEATURE_SCHEMA_ENRICHED if route == "enriched" else FEATURE_SCHEMA_CORE
    route_schema = (bundle.manifest.feature_schemas or {}).get(route)
    if route_schema:
        schema = tuple(route_schema)
    elif bundle.feature_names:
        schema = tuple(bundle.feature_names)
    zero_fill = frozenset((bundle.zero_fill_features or {}).get(route) or ())
    features = _row_to_vector(row, schema, zero_fill)
    if feature_audit_rows is not None:
        feature_audit_rows.append(
            {
                "instrument": candidate.instrument,
                "decisionTimeMs": candidate.decision_time_ms,
                "route": route,
                "schema": list(schema),
                "values": [float(value) if math.isfinite(float(value)) else None for value in features],
            }
        )

    core_ok = bool(routing.get("coreOk"))
    missing_feeds = list(dict.fromkeys(ctx.missing_feeds + ctx.enriched_missing_feeds))
    data_quality = {
        "coreOk": core_ok,
        "route": route,
        "missingFeeds": missing_feeds,
    }
    if ctx.optional_missing_feeds:
        data_quality["optionalMissingFeeds"] = list(ctx.optional_missing_feeds)
    if not core_ok:
        data_quality["blocker"] = "missing_core_feeds"
        return flat_signal(
            instrument=candidate.instrument,
            family=family,
            horizon=horizon,
            model_version=bundle.version,
            gate_result=gate.to_dict(),
            data_quality=data_quality,
            model_health={
                "artifactHash": bundle.artifact_hash,
                "trainedAt": bundle.manifest.trained_at,
                "brier": None,
                "driftScore": 0.0,
                "gateResult": gate.to_dict(),
                "blocker": "missing_core_feeds",
            },
            primary_signals=list(candidate.signals),
        )

    model = bundle.model_enriched if route == "enriched" else bundle.model_core
    calibrator = bundle.calibrator_enriched if route == "enriched" else bundle.calibrator
    quantile_bundle = (
        bundle.quantile_heads_enriched if route == "enriched" else bundle.quantile_heads
    )
    if model is None:
        model = bundle.model_core
        calibrator = bundle.calibrator
        quantile_bundle = bundle.quantile_heads
        route = "core"
    if model is None:
        return error_signal(
            instrument=candidate.instrument,
            family=family,
            horizon=horizon,
            reason="model_missing",
            gate_result=gate.to_dict(),
            model_version=bundle.version,
        )

    raw_p = _predict_proba(model, features)
    p_cal = _calibrate(calibrator, raw_p)
    if (
        not math.isfinite(raw_p)
        or not math.isfinite(p_cal)
        or not 0.0 <= raw_p <= 1.0
        or not 0.0 <= p_cal <= 1.0
    ):
        return error_signal(
            instrument=candidate.instrument,
            family=family,
            horizon=horizon,
            reason="model_probability_invalid",
            gate_result=gate.to_dict(),
            model_version=bundle.version,
        )
    return_q = _predict_quantiles(quantile_bundle, features, "net_R")
    mae_q = _predict_quantiles(quantile_bundle, features, "MAE_R")
    mfe_q = _predict_quantiles(quantile_bundle, features, "MFE_R")
    hold_q = _predict_quantiles(quantile_bundle, features, "hold_bars")

    expected_r = _expected_net_r(
        p_cal,
        mfe_q,
        mae_q,
        family=family,
        horizon=horizon,
        realized_expectancy=(bundle.manifest.eval_summary or {}).get("expectancy"),
    )
    if not math.isfinite(expected_r):
        return error_signal(
            instrument=candidate.instrument,
            family=family,
            horizon=horizon,
            reason="model_expectancy_nonfinite",
            gate_result=gate.to_dict(),
            model_version=bundle.version,
        )
    status = _decision_status(
        expected_net_r=expected_r,
        p_cal=p_cal,
        thr_family=bundle.thr_family,
        core_ok=bool(data_quality.get("coreOk")),
    )

    # Model-quality gate: an artifact whose own eval-set expectancy at its
    # selected threshold is non-positive has demonstrated no edge — its TRADE
    # decisions are capped to WATCH (fail-closed, per-family/horizon). The
    # signal stays visible; only auto-execution eligibility is removed.
    trade_cap_reason: str | None = None
    if status == "TRADE":
        eval_expectancy = (bundle.manifest.eval_summary or {}).get("expectancy")
        if eval_expectancy is not None:
            try:
                if float(eval_expectancy) <= 0.0:
                    status = "WATCH"
                    trade_cap_reason = "model_eval_expectancy_nonpositive"
            except (TypeError, ValueError):
                status = "WATCH"
                trade_cap_reason = "model_eval_expectancy_unreadable"

    entry_ref = math.exp(candidate.entry_log)
    brackets = compute_brackets(
        direction=candidate.direction,
        entry_reference=entry_ref,
        sigma_bar=candidate.sigma_bar,
        horizon=horizon,
        mae_q=mae_q,
        mfe_q=mfe_q,
    )
    if status == "TRADE" and not brackets.rr_ok:
        status = "WATCH"

    feat_row = {k: float(v) for k, v in row.items() if isinstance(v, (int, float))}
    feat_row[DECISION_TIME_KEY] = float(candidate.decision_time_ms)
    if live_feature_rows is not None:
        live_feature_rows.append(feat_row)
        monitor_rows = live_feature_rows
    else:
        monitor_rows = [feat_row]

    ref = monitor_reference
    if ref is None and bundle.monitor_reference:
        ref = bundle.monitor_reference

    # Live-only side effects: the realized-payoff snapshot reads the live trade
    # journal and set_watch_max mutates the promotion registry. Backtest/parity
    # callers pass live=False so historical replays neither contaminate nor get
    # contaminated by live state.
    cal_snap = journal_calibration_snapshot(family, horizon) if live else None
    monitor = evaluate_monitor(
        reference_features=ref,
        live_feature_rows=monitor_rows,
        realized_win_rate=cal_snap.realized_win_rate if cal_snap else None,
        mean_p_cal=cal_snap.mean_p_cal if cal_snap else None,
    )
    if live and monitor.watch_max:
        set_watch_max(family, True)
    if live and (is_watch_max(family) or monitor.watch_max):
        status = apply_watch_max_cap(status, True)

    signal_strength = compute_signal_strength(expected_r, p_cal)
    conviction_detail = {
        "probEdge": round(max(0.0, min(1.0, 2.0 * (p_cal - 0.5))), 4),
        "payoff": round(max(0.0, min(1.0, expected_r)), 4),
        "method": "sqrt(probEdge*payoff)",
        "legacyExpectedRStrength": legacy_expected_r_strength(expected_r),
    }

    return ASESignal(
        engineVersion=ENGINE_VERSION,
        modelFamily=family,
        modelVersion=bundle.version,
        horizon=horizon,
        decisionStatus=status,  # type: ignore[arg-type]
        direction=_direction_label(candidate.direction),  # type: ignore[arg-type]
        expectedNetR=expected_r,
        expectedNetBps=expected_r * 10000.0,
        probabilityPositive=p_cal,
        decisionMargin=abs(p_cal - 0.5),
        signalStrength=signal_strength,
        returnQ=return_q,
        maeQ=mae_q,
        mfeQ=mfe_q,
        holdQ=hold_q,
        entryReference=brackets.entry_reference,
        entryZone=brackets.entry_zone,
        sl=brackets.sl,
        tp1=brackets.tp1,
        tp2=brackets.tp2,
        maxHoldBars=brackets.max_hold_bars,
        primarySignals=list(candidate.signals),
        predictionDiagnostics={
            "rawP": raw_p,
            "thrFamily": bundle.thr_family,
            "conviction": conviction_detail,
            **({"tradeCapReason": trade_cap_reason} if trade_cap_reason else {}),
        },
        dataQuality={**data_quality, "deployment": "OPERATIONAL"},
        modelHealth={
            "artifactHash": bundle.artifact_hash,
            "trainedAt": bundle.manifest.trained_at,
            "brier": (bundle.manifest.validation_report or {}).get("calibration", {}).get("brier"),
            "driftScore": monitor.drift_score,
            "gateResult": gate.to_dict(),
            "monitor": monitor.to_dict(),
            **({"tradeCapReason": trade_cap_reason} if trade_cap_reason else {}),
        },
        instrument=candidate.instrument,
        display=instrument.display,
        decisionTimeMs=candidate.decision_time_ms,
    )


def predict_batch(
    candidates: Sequence[Candidate],
    store: Any,
    instruments: dict[str, Instrument],
    *,
    monitor_reference: dict[str, np.ndarray] | None = None,
    feature_audit_rows: list[dict[str, Any]] | None = None,
    live: bool = True,
) -> list[ASESignal]:
    """Single inference path for scan, shadow, backtest parity, and chart review."""
    bundle_cache: dict[tuple[str, Horizon], ArtifactBundle | None] = {}
    live_buffers: dict[tuple[str, Horizon], list[dict[str, float]]] = {}
    max_live = monitor_max_live_rows()
    out: list[ASESignal] = []

    for cand in candidates:
        inst = instruments.get(cand.instrument)
        if inst is None:
            out.append(
                error_signal(
                    instrument=cand.instrument,
                    family=cand.family,
                    horizon=cand.horizon,
                    reason="unknown_instrument",
                )
            )
            continue
        key = (inst.family, cand.horizon)
        if key not in bundle_cache:
            bundle_cache[key] = load_artifact_bundle(inst.family, cand.horizon)
        bundle = bundle_cache[key]
        live_buf = live_buffers.setdefault(key, [])
        ref = monitor_reference or (bundle.monitor_reference if bundle else None)
        # Per-candidate isolation: one bad model/row must degrade to an ERROR
        # signal, never kill the whole batch (and with it the scan).
        try:
            sig = predict_one(
                cand,
                store,
                inst,
                bundle=bundle,
                monitor_reference=ref,
                live_feature_rows=live_buf,
                feature_audit_rows=feature_audit_rows,
                live=live,
            )
        except Exception as exc:
            log.warning("ASE predict failed for %s: %s", cand.instrument, exc)
            sig = error_signal(
                instrument=cand.instrument,
                family=cand.family,
                horizon=cand.horizon,
                reason=f"predict_failed:{exc}",
                model_version=bundle.version if bundle else "none",
            )
        if len(live_buf) > max_live:
            del live_buf[: len(live_buf) - max_live]
        out.append(sig)
    return out


def predict_no_candidate(
    instrument: Instrument,
    horizon: Horizon,
    *,
    store: Any | None = None,
    decision_time_ms: int | None = None,
) -> ASESignal:
    gate = assert_demo()
    if not gate.ok:
        return error_signal(
            instrument=instrument.symbol,
            family=instrument.family,
            horizon=horizon,
            reason="demo_gate_failed",
            gate_result=gate.to_dict(),
        )
    bundle = load_artifact_bundle(instrument.family, horizon)
    version = bundle.version if bundle else "none"
    artifacts_present = bundle is not None

    now_ms = decision_time_ms or int(time.time() * 1000)
    ptis_ok = False
    bars_stale = False
    last_bar_age_hours: float | None = None
    if store is not None:
        lookback_ms = 180 * 24 * 3600 * 1000
        series = load_bar_series(
            store,
            instrument.symbol,
            horizon,
            now_ms - lookback_ms,
            now_ms,
        )
        ptis_ok = series is not None and len(series.close_log) > 0
        if ptis_ok and series is not None:
            bars_stale = live_bars_stale(series, horizon, now_ms)
            last_bar_age_hours = round(
                (now_ms - int(series.value_time[-1])) / 3_600_000.0, 1
            )

    blocker = "no_layer1_candidate"
    if not ptis_ok:
        blocker = "ptis_bars_missing"
    elif bars_stale:
        blocker = "stale_ptis_bars"

    data_quality: dict[str, Any] = {
        "coreOk": ptis_ok and not bars_stale,
        "route": "core",
        "missingFeeds": [] if ptis_ok else ["eodhd_bars"],
        "blocker": blocker,
        "ptisOk": ptis_ok,
        "artifactsPresent": artifacts_present,
        "deployment": "OPERATIONAL",
    }
    if last_bar_age_hours is not None:
        data_quality["lastBarAgeHours"] = last_bar_age_hours
    model_health: dict[str, Any] = {
        "artifactHash": bundle.artifact_hash if bundle else "",
        "trainedAt": bundle.manifest.trained_at if bundle else "",
        "brier": None,
        "driftScore": 0.0,
        "gateResult": gate.to_dict(),
        "blocker": blocker,
    }
    if not artifacts_present:
        model_health["errorReason"] = "artifact_missing"

    return flat_signal(
        instrument=instrument.symbol,
        family=instrument.family,
        horizon=horizon,
        model_version=version,
        gate_result=gate.to_dict(),
        data_quality=data_quality,
        model_health=model_health,
        decision_time_ms=now_ms,
    )
