"""Flask route for read-only AI chart review."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from flask import Response, jsonify, request, stream_with_context
from config import get_ai_review_provider, normalize_ai_review_provider, normalize_chart_review_primary_engine

from ai_streaming import is_streaming_enabled, sse_done, sse_format

from athena_ai.ai_review_payload_builder import (
    build_strategy_layer,
    render_strategy_block_for_prompt,
)

from ai_review.concordance import compute_engine_a_ai_concordance, compute_engine_b_ai_concordance
from ai_review.context_diagnostics import (
    build_context_diagnostics,
    sanitize_ai_review_missing_context,
)
from ai_review.engine_a_context import (
    assemble_engine_a_context,
    build_engine_b_summary_for_strategy,
)
from ai_review.engine_a_verdict import build_engine_a_verdict_comparison
from ai_review.engine_b_context import (
    assemble_engine_b_context,
    build_engine_b_summary_from_context,
)
from ai_review.engine_b_verdict import build_engine_b_verdict_comparison
from ai_review.freshness import classify_atr_freshness
from ai_review.normalizer import normalize_chart_review_response
from ai_review.payload_schema import build_payload
from ai_review.persistence import (
    ensure_schema,
    find_recent_review_by_hash,
    record_review,
)
from ai_review.prompt_builder import build_chart_review_prompt
from ai_review.provider_meta import (
    ProviderChartReviewError,
    apply_parse_fallback,
    provider_error_response,
    provider_meta_from_persisted,
)
from ai_review.engine_b_structure_contract import has_engine_b_structure_evidence
from ai_review.gate_hygiene import normalize_review_gates
from ai_review.review_geometry import attach_review_geometry
from ai_review.symbol_parity import evaluate_symbol_parity
from ai_review.watch_log import log_watch_transition, resolve_watch_transition
from ai_review.timestamp_contract import evaluate_review_freshness
from ai_review.providers.router import run_chart_review
from ai_review.summary import build_ai_review_summary
from ai_review.timeframe_routing import resolve_timeframe_route
from ai_review.validation import decode_screenshot_bytes, validate_request

log = logging.getLogger("sentinel.ai_chart_review")

_TF_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
}

_ALLOWED_REQUEST_KEYS = frozenset(
    {"symbol", "timeframe", "provider", "screenshot_base64", "screenshot_meta"}
)


def _reject_extra_request_keys(data: dict[str, Any]) -> str | None:
    extra = sorted(set(data.keys()) - _ALLOWED_REQUEST_KEYS)
    if extra:
        return f"unexpected request keys: {', '.join(extra)}"
    return None


def _resolve_provider_name(
    data: dict[str, Any],
    cfg: dict[str, Any],
    root_cfg: dict[str, Any] | None = None,
) -> str:
    provider = data.get("provider")
    if provider in (None, "", "default", "none"):
        provider = None
    return get_ai_review_provider(root_cfg or cfg, requested=provider)


def _review_type_for_primary(primary_engine: str) -> str:
    return "engine_b" if primary_engine == "B" else "engine_a"


def _chart_review_type_label(primary_engine: str) -> str:
    return "engine_b_chart" if primary_engine == "B" else "engine_a_chart"


def _resolve_primary_engine(
    screenshot_meta: dict[str, Any] | None,
    cfg: dict[str, Any],
) -> str:
    meta = screenshot_meta if isinstance(screenshot_meta, dict) else {}
    for key in ("primary_engine", "signal_engine"):
        raw = meta.get(key)
        if raw not in (None, ""):
            return normalize_chart_review_primary_engine(raw)
    return normalize_chart_review_primary_engine(cfg.get("PRIMARY_ENGINE"))


def _symbol_key(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw.endswith("=X"):
        raw = raw[:-2]
    return "".join(ch for ch in raw if ch.isalnum())


def _candidate_id(row: dict[str, Any]) -> str | None:
    value = row.get("signalId") or row.get("signal_id") or row.get("id")
    return str(value).strip() if value not in (None, "") else None


def _candidate_revision(row: dict[str, Any]) -> str | None:
    value = row.get("timestamp") or row.get("decisionTime") or row.get("scan_timestamp")
    return str(value).strip() if value not in (None, "") else None


def _timestamp_age_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000.0
        else:
            parsed = datetime.fromisoformat(
                str(value).strip().replace(" ", "T").replace("Z", "+00:00")
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            ts = parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0.0, datetime.now(timezone.utc).timestamp() - ts)


def _engine_b_source_fresh(engine_ctx: dict[str, Any]) -> bool:
    age = _timestamp_age_seconds(engine_ctx.get("latest_candle_ts"))
    policy = engine_ctx.get("timeframe_policy") or {}
    trigger_tf = str(
        policy.get("triggerTf")
        or policy.get("trigger_tf")
        or engine_ctx.get("timeframe")
        or ""
    ).upper()
    bucket = _TF_SECONDS.get(trigger_tf)
    return age is not None and bucket is not None and age <= max(300.0, bucket * 2.5)


def _candidate_matches_symbol(row: dict[str, Any], symbol: str) -> bool:
    target = _symbol_key(symbol)
    return bool(target) and target in {
        _symbol_key(row.get("symbol")),
        _symbol_key(row.get("pair")),
        _symbol_key(row.get("display")),
    }


def _runtime_rows(runtime: SimpleNamespace, attr: str) -> list[dict[str, Any]]:
    provider = getattr(runtime, attr, None)
    if not callable(provider):
        return []
    try:
        rows = provider() or []
    except Exception:
        log.exception("Unable to read %s for chart-review candidate lookup", attr)
        return []
    return [row for row in rows if isinstance(row, dict)]


def _resolve_server_candidate(
    symbol: str,
    screenshot_meta: dict[str, Any],
    cfg: dict[str, Any],
    runtime: SimpleNamespace,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Resolve a client assertion to an immutable server scan row.

    The browser may identify a candidate, but it never supplies authoritative
    score, entry, style or direction. Those values come only from the matching
    row in the server's latest per-engine scan cache.
    """
    requested_engine = _resolve_primary_engine(screenshot_meta, cfg)
    requested_id = str(screenshot_meta.get("candidate_id") or "").strip() or None
    requested_revision = str(screenshot_meta.get("candidate_revision") or "").strip() or None
    requested_direction = str(screenshot_meta.get("candidate_direction") or "").strip().upper()

    sources = {
        "A": _runtime_rows(runtime, "last_engine_a_rows_fn"),
        "B": _runtime_rows(runtime, "last_engine_b_rows_fn"),
    }
    matches: list[tuple[str, dict[str, Any]]] = []
    for engine, rows in sources.items():
        for row in rows:
            if not _candidate_matches_symbol(row, symbol):
                continue
            if requested_id and _candidate_id(row) != requested_id:
                continue
            if requested_revision and _candidate_revision(row) != requested_revision:
                continue
            matches.append((engine, row))

    if requested_id or requested_revision:
        if not matches:
            return requested_engine, None, "candidate_not_found_or_revision_changed"
    else:
        matches = [item for item in matches if item[0] == requested_engine]

    if requested_direction in {"LONG", "SHORT"}:
        directed = [
            item for item in matches
            if str(item[1].get("direction") or "").upper() == requested_direction
        ]
        if directed:
            matches = directed

    if not matches:
        return requested_engine, None, None

    engine_matches = [item for item in matches if item[0] == requested_engine]
    if engine_matches:
        matches = engine_matches
    if len(matches) != 1:
        return requested_engine, None, "candidate_lookup_ambiguous"

    primary_engine, candidate = matches[0]
    asserted_style = str(
        screenshot_meta.get("signal_style")
        or screenshot_meta.get("analyze_style")
        or ""
    ).strip().lower()
    candidate_style = str(candidate.get("horizon") or candidate.get("style") or "").strip().lower()
    if asserted_style and candidate_style and asserted_style != candidate_style:
        return primary_engine, None, "candidate_style_mismatch"
    if requested_direction in {"LONG", "SHORT"}:
        candidate_direction = str(candidate.get("direction") or "").upper()
        if candidate_direction and candidate_direction != requested_direction:
            return primary_engine, None, "candidate_direction_mismatch"
    if primary_engine != requested_engine:
        return primary_engine, None, "candidate_engine_mismatch"
    return primary_engine, candidate, None


def _attach_review_input_meta(
    response: dict[str, Any],
    *,
    engine_ctx: dict[str, Any],
    primary_engine: str = "A",
) -> None:
    structure_context = engine_ctx.get("structure_context")
    route = response.get("timeframeRoute") or response.get("timeframe_route") or {}
    is_b = primary_engine == "B"
    has_engine_a = not is_b and "passed" in engine_ctx
    has_engine_b = is_b or (
        isinstance(structure_context, dict) and bool(structure_context)
    )
    response["reviewInputMeta"] = {
        "symbol": engine_ctx.get("symbol"),
        "signalEngine": "B" if is_b else ("A" if has_engine_a else "B" if has_engine_b else "unknown"),
        "signalTimeframe": engine_ctx.get("timeframe"),
        "chartTimeframe": engine_ctx.get("chart_timeframe") or engine_ctx.get("timeframe"),
        "hasEngineASignal": has_engine_a,
        "hasEngineBOverlay": has_engine_b,
        "hasChartImage": True,
        "timeframeRouteApplied": bool(isinstance(route, dict) and route.get("enabled")),
    }


def _attach_review_summary(
    response: dict[str, Any],
    *,
    engine_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    concordance: dict[str, Any],
    provider_meta: dict[str, Any],
    mismatch_warnings: list[str],
    diagnostic_ai_review: dict[str, Any] | None = None,
    primary_engine: str = "A",
) -> dict[str, Any]:
    snapshots = engine_ctx.get("engine_snapshots")
    diagnostic_source = diagnostic_ai_review or ai_review
    clean_ai_review = sanitize_ai_review_missing_context(engine_ctx, diagnostic_source)
    response["ai_review"] = clean_ai_review

    # F4: the pre-provider geometry pass runs with ai_review=None, so the SL
    # thesis check never saw requiredConfirmation and always fell back to
    # ema50. Re-run it now that the model's text exists. Geometry is
    # recomputed identically; only the text-derived inputs are added.
    try:
        engine_ctx.update(
            attach_review_geometry(engine_ctx, ai_review=clean_ai_review)
        )
        if isinstance(engine_ctx.get("risk_geometry"), dict):
            engine_ctx["riskGeometry"] = engine_ctx["risk_geometry"]
    except Exception:  # never fail the review on an advisory recompute
        pass
    structured = clean_ai_review.get("structured") or {}
    if not isinstance(structured, dict):
        structured = {}
    model_summary = structured.get("aiReviewSummary") or structured.get("ai_review_summary")
    if primary_engine == "B":
        model_comparison = structured.get("engineBVerdictComparison") or structured.get(
            "engine_b_verdict_comparison"
        )
    else:
        model_comparison = structured.get("engineAVerdictComparison") or structured.get(
            "engine_a_verdict_comparison"
        )
    summary = build_ai_review_summary(
        engine_ctx,
        clean_ai_review,
        concordance,
        provider_meta,
        engine_snapshots=snapshots,
        mismatch_warnings=mismatch_warnings,
        model_summary=model_summary if isinstance(model_summary, dict) else None,
    )
    if primary_engine == "B":
        verdict_comparison = build_engine_b_verdict_comparison(
            engine_ctx,
            clean_ai_review,
            model_comparison=model_comparison if isinstance(model_comparison, dict) else None,
        )
        response["engineBVerdictComparison"] = verdict_comparison
        response["engine_b_verdict_comparison"] = verdict_comparison
    else:
        verdict_comparison = build_engine_a_verdict_comparison(
            engine_ctx,
            clean_ai_review,
            model_comparison=model_comparison if isinstance(model_comparison, dict) else None,
            engine_snapshots=snapshots,
        )
        response["engineAVerdictComparison"] = verdict_comparison
        response["engine_a_verdict_comparison"] = verdict_comparison
    response["aiReviewSummary"] = summary
    response["ai_review_summary"] = summary
    selected = response.get("selectedProvider")
    if selected:
        summary["selectedProvider"] = selected
    if response.get("fallbackUsed") is not None or response.get("fallback_used") is not None:
        summary["fallbackUsed"] = bool(
            response.get("fallbackUsed") or response.get("fallback_used")
        )
    failure = response.get("providerFailure") or response.get("provider_failure")
    if failure:
        summary["providerFailure"] = failure
        summary["provider_failure"] = failure
    elif selected and summary.get("provider"):
        selected_norm = normalize_ai_review_provider(selected)
        active_norm = normalize_ai_review_provider(summary.get("provider"))
        if selected_norm and active_norm and selected_norm != active_norm:
            mismatch = {
                "provider": selected_norm,
                "error": (
                    f"Selected provider {selected_norm} but active provider is {active_norm}"
                ),
                "providerStatus": "fallback_used",
            }
            summary["providerFailure"] = mismatch
            summary["provider_failure"] = mismatch
            summary["fallbackUsed"] = True
    ctx_diag = build_context_diagnostics(engine_ctx, diagnostic_source)
    response.update(ctx_diag)
    response["derivativesContext"] = ctx_diag.get("fundingOi")
    response["derivatives_context"] = ctx_diag.get("fundingOi")
    response["nonVisualContext"] = ctx_diag.get("nonVisualContext")
    if primary_engine == "B":
        response["engineBNonVisualContext"] = ctx_diag.get("nonVisualContext")
        response["engine_b_non_visual_context"] = ctx_diag.get("nonVisualContext")
    else:
        response["engineANonVisualContext"] = ctx_diag.get("engineANonVisualContext")
        response["engineAScoreAttribution"] = ctx_diag.get("scoreAttribution")
        response["engine_a_non_visual_context"] = ctx_diag.get("engineANonVisualContext")
        response["engine_a_score_attribution"] = ctx_diag.get("engineAScoreAttribution")
    response["primaryEngine"] = primary_engine
    ctx_key = "engine_b_context" if primary_engine == "B" else "engine_a_context"
    camel_key = "engineBContext" if primary_engine == "B" else "engineAContext"
    if ctx_key not in response:
        response[ctx_key] = engine_ctx
    response[camel_key] = response.get(ctx_key) or engine_ctx
    plan = clean_ai_review.get("suggestedTradePlan") or clean_ai_review.get("suggested_trade_plan")
    if isinstance(plan, dict):
        response["suggestedTradePlan"] = plan
        response["suggested_trade_plan"] = plan
    return response


def _attach_timeframe_route(
    response: dict[str, Any],
    *,
    engine_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    primary_engine: str = "A",
) -> dict[str, Any]:
    ctx_key = "engine_b_context" if primary_engine == "B" else "engine_a_context"
    response_ctx = response.get(ctx_key)
    if not isinstance(response_ctx, dict):
        response_ctx = engine_ctx
        response[ctx_key] = response_ctx
    comparison = (
        response.get("engineBVerdictComparison")
        or response.get("engine_b_verdict_comparison")
        if primary_engine == "B"
        else response.get("engineAVerdictComparison")
        or response.get("engine_a_verdict_comparison")
    )
    route = resolve_timeframe_route(
        asset_group=response_ctx.get("asset_group"),
        context_tf=response_ctx.get("timeframe") or response_ctx.get("chart_timeframe"),
        ai_review=ai_review,
        verdict_comparison=comparison if isinstance(comparison, dict) else None,
        policy_roles=response_ctx.get("timeframe_policy"),
        primary_engine=primary_engine,
    )
    response_ctx["timeframe_route"] = route
    response["timeframeRoute"] = route
    response["timeframe_route"] = route
    _attach_review_input_meta(response, engine_ctx=response_ctx, primary_engine=primary_engine)
    return response


def _build_strategy_layer_safely(
    *,
    cfg: dict[str, Any],
    engine_ctx: dict[str, Any],
    symbol: str,
    timeframe: str,
    screenshot_meta: dict[str, Any] | None = None,
    primary_engine: str = "A",
) -> dict[str, Any] | None:
    """Build the additive strategy-playbook layer behind STRATEGY_LAYER_ENABLED.

    Read-only and best-effort: any failure here returns None and leaves the
    rest of the AI chart-review pipeline untouched. Never executes trades,
    never mutates engine_a_ctx.
    """
    if not cfg.get("STRATEGY_LAYER_ENABLED", True):
        return None
    try:
        limit = int(cfg.get("STRATEGY_LAYER_OHLCV_LIMIT") or 80)
        ohlcv_window = engine_ctx.get("ohlcv_bars")
        if not isinstance(ohlcv_window, list) or not ohlcv_window:
            ohlcv_window = None
        overlays = (screenshot_meta or {}).get("overlays") or []
        if primary_engine == "B":
            engine_b_summary = build_engine_b_summary_from_context(engine_ctx)
        else:
            engine_b_summary = build_engine_b_summary_for_strategy(engine_ctx)
        if isinstance(overlays, list) and "engine_b" not in overlays:
            engine_b_summary = {**engine_b_summary, "available": False}
        return build_strategy_layer(
            engine_a_ctx=engine_ctx,
            ohlcv_window=ohlcv_window,
            engine_b_summary=engine_b_summary,
            engine_d_summary=None,
            symbol=symbol,
            timeframe=timeframe,
            asset_group=engine_ctx.get("asset_group"),
            direction=engine_ctx.get("direction"),
            ohlcv_limit=limit,
        )
    except Exception:
        log.exception(
            "Strategy layer build failed for %s %s; continuing without it",
            symbol,
            timeframe,
        )
        return None


def register_ai_chart_review_routes(app, runtime: SimpleNamespace) -> None:
    @app.post("/api/ai/chart-review")
    def api_ai_chart_review():
        cfg = runtime.CONFIG["AI_CHART_REVIEW"]
        if isinstance(cfg, dict):
            cfg["OPENAI_REVIEW_ENABLED"] = runtime.CONFIG.get(
                "OPENAI_REVIEW_ENABLED",
                cfg.get("OPENAI_REVIEW_ENABLED", True),
            )
        if not cfg.get("ENABLED"):
            return jsonify({"error": "AI chart review disabled"}), 503

        data = request.get_json(silent=True) or {}
        extra_err = _reject_extra_request_keys(data)
        if extra_err:
            return jsonify({"error": extra_err}), 400

        err = validate_request(data, cfg)
        if err:
            return jsonify({"error": err.message}), err.status

        symbol = str(data["symbol"]).strip()
        timeframe = str(data["timeframe"]).strip()
        screenshot_meta = dict(data.get("screenshot_meta") or {})
        provider = _resolve_provider_name(data, cfg, runtime.CONFIG)

        primary_engine, origin_signal, candidate_error = _resolve_server_candidate(
            symbol,
            screenshot_meta,
            cfg,
            runtime,
        )
        if candidate_error:
            return jsonify({"error": candidate_error}), 409
        review_type = _review_type_for_primary(primary_engine)
        chart_review_type = _chart_review_type_label(primary_engine)

        # P0-1: hard symbol parity before any levels / provider dispatch.
        resolve_pair = getattr(runtime, "resolve_pair_fn", None)
        engine_pair = resolve_pair(symbol) if callable(resolve_pair) else None
        entry_for_basis = None
        if isinstance(origin_signal, dict):
            try:
                entry_for_basis = float(
                    origin_signal.get("entry") or origin_signal.get("price") or 0
                ) or None
            except (TypeError, ValueError):
                entry_for_basis = None
        # Unconditional: a review with neither a candidate nor a resolved pair has
        # nothing to prove the chart symbol against, which is a reject, not a skip.
        parity = evaluate_symbol_parity(
            symbol,
            origin_signal if isinstance(origin_signal, dict) else None,
            engine_pair=engine_pair if isinstance(engine_pair, dict) else None,
            engine_entry=entry_for_basis,
        )
        if not parity.get("ok"):
            return (
                jsonify(
                    {
                        "error": "symbol_parity_mismatch",
                        "reason": parity.get("reason"),
                        "detail": parity.get("detail")
                        or "Chart symbol does not match engine candidate",
                        "chart_symbol": symbol,
                        "engine_keys": parity.get("engine_keys") or [],
                        "diagnostics": "UNAVAILABLE",
                        "levels_emitted": False,
                        "execution_permitted": False,
                        "symbol_alias_applied": parity.get("symbol_alias_applied"),
                    }
                ),
                409,
            )
        symbol_alias_applied = parity.get("symbol_alias_applied")

        if primary_engine == "B":
            engine_ctx = assemble_engine_b_context(
                symbol,
                timeframe,
                screenshot_meta=screenshot_meta,
                resolve_pair_fn=getattr(runtime, "resolve_pair_fn", None),
                naked_analysis_fn=getattr(runtime, "naked_analysis_fn", None),
                last_engine_b_rows_fn=getattr(runtime, "last_engine_b_rows_fn", None),
                origin_signal=origin_signal,
            )
            if engine_ctx is None:
                return jsonify({"error": "Engine B returned no result"}), 422
        else:
            engine_ctx = assemble_engine_a_context(
                symbol,
                timeframe,
                screenshot_meta=screenshot_meta,
                resolve_pair_fn=getattr(runtime, "resolve_pair_fn", None),
                analyze_pair_fn=getattr(runtime, "analyze_pair_fn", None),
                btc_bias_fn=getattr(runtime, "btc_bias_fn", None),
                origin_signal=origin_signal,
                naked_analysis_fn=getattr(runtime, "naked_analysis_fn", None),
            )
            if engine_ctx is None:
                return jsonify({"error": "Engine A returned no result"}), 422

        if symbol_alias_applied:
            engine_ctx["symbol_alias_applied"] = symbol_alias_applied

        atr = engine_ctx.setdefault("atr", {})
        freshness = classify_atr_freshness(
            atr.get("atr_tf"),
            atr.get("atr_age_seconds"),
            atr.get("atr_confirmed_only"),
        )
        atr["atr_freshness_status"] = freshness.get("status")
        atr["max_expected_age_seconds"] = freshness.get("max_expected_age_seconds")

        # Review-layer live geometry (RR live, zone status, SL thesis, WATCH).
        geom = attach_review_geometry(engine_ctx)
        engine_ctx.update(geom)
        if isinstance(engine_ctx.get("risk_geometry"), dict):
            engine_ctx["riskGeometry"] = engine_ctx["risk_geometry"]

        # Phase 4: record the WATCH transition (observational only — WATCH never
        # bypasses risk_check() and never sizes anything).
        _watch = engine_ctx.get("watch_state")
        if isinstance(_watch, dict):
            _transition = resolve_watch_transition(previous=None, current=_watch)
            if _transition:
                _geom_block = engine_ctx.get("geometry") or {}
                engine_ctx["watch_transition"] = log_watch_transition(
                    transition=_transition,
                    watch_state=_watch,
                    price=_geom_block.get("current_price")
                    or _geom_block.get("live_price"),
                    atr=(engine_ctx.get("atr") or {}).get("atr_value"),
                    symbol=symbol,
                )

        # P2-1/P2-2/P2-3: honest applicable-gate view. Presentation only —
        # Engine A's own pass/fail is preserved on each gate's engineValue.
        _origin = origin_signal if isinstance(origin_signal, dict) else {}
        _predicates = _origin.get("predicates") or engine_ctx.get("predicates")
        if _predicates:
            engine_ctx["component_gates"] = normalize_review_gates(
                predicates=_predicates,
                score_group=(
                    _origin.get("scoreGroup")
                    or _origin.get("score_group")
                    or engine_ctx.get("score_group")
                    or engine_ctx.get("asset_group")
                ),
                scan_timestamp=(
                    engine_ctx.get("review_timestamp")
                    or engine_ctx.get("scan_timestamp")
                ),
                quant_weights_by_group=runtime.CONFIG.get(
                    "ENGINE_A_QUANT_WEIGHTS_BY_GROUP"
                ),
            )
            engine_ctx["componentGates"] = engine_ctx["component_gates"]

        # P2-7: enforce freshness pre-provider (no API spend on stale reviews).
        # review_timestamp is stamped now() at context assembly, so capture skew
        # alone is always small on a normal submit; the candidate age is what
        # catches stale levels joined to freshly rebuilt factors.
        freshness = evaluate_review_freshness(engine_ctx, screenshot_meta, cfg)
        mismatch_warnings = freshness["warnings"]
        max_delta = freshness["maxSeconds"]
        engine_ctx["levels_provenance"] = freshness["levelsProvenance"]
        if freshness["blocking"] and bool(
            cfg.get("ENFORCE_CAPTURE_SKEW_PRE_PROVIDER", True)
        ):
            primary_block = freshness["blocking"][0]
            return (
                jsonify(
                    {
                        "error": primary_block["reason"],
                        "detail": primary_block["detail"],
                        "blocking": freshness["blocking"],
                        "mismatch_warnings": mismatch_warnings,
                        "max_seconds": max_delta,
                        "capture_skew_seconds": freshness["captureSkewSeconds"],
                        "candidate_age_seconds": freshness["candidateAgeSeconds"],
                        "levels_provenance": freshness["levelsProvenance"],
                        "diagnostics": "UNAVAILABLE",
                        "levels_emitted": False,
                        "execution_permitted": False,
                    }
                ),
                409,
            )
        if primary_engine == "B" and not _engine_b_source_fresh(engine_ctx):
            mismatch_warnings.append(
                "engine_b_latest_trigger_candle_stale_or_unavailable"
            )
        overlays = screenshot_meta.get("overlays") or []
        if isinstance(overlays, list) and "engine_b" in overlays:
            chart_snapshot = screenshot_meta.get("chart_snapshot") or {}
            overlay_status = (
                chart_snapshot.get("engineBOverlayStatus")
                if isinstance(chart_snapshot, dict)
                else None
            )
            if overlay_status != "ready":
                return jsonify({"error": "engine_b_overlay_not_fresh"}), 409
            # P1-5: use the shared contract, not a third private key list.
            struct = engine_ctx.get("structure_context") or {}
            if not has_engine_b_structure_evidence(struct):
                mismatch_warnings.append(
                    "engine_b_overlays_enabled_but_server_structure_context_empty"
                )
        engine_ctx["mismatch_warnings"] = mismatch_warnings
        engine_ctx["chart_captured_at"] = screenshot_meta.get("captured_at")

        screenshot_bytes = decode_screenshot_bytes(str(data["screenshot_base64"]))
        screenshot_hash = hashlib.sha256(screenshot_bytes).hexdigest()[:16]

        dedup = find_recent_review_by_hash(
            symbol,
            timeframe,
            screenshot_hash,
            int(cfg.get("DEDUP_WINDOW_SECONDS") or 60),
            audit_db=getattr(runtime, "AUDIT_DB", None),
            review_type=review_type,
        )
        if dedup:
            cached_provider = normalize_ai_review_provider(dedup.get("provider"))
            requested_provider = normalize_ai_review_provider(provider)
            if (
                cached_provider
                and requested_provider
                and cached_provider != requested_provider
            ):
                dedup = None

        if dedup and origin_signal is not None:
            ctx_key = "engine_b_context" if primary_engine == "B" else "engine_a_context"
            cached_ctx = dedup.get(ctx_key) or {}
            cached_origin = (
                cached_ctx.get("candidate_origin")
                if isinstance(cached_ctx, dict)
                else None
            )
            cached_origin = cached_origin if isinstance(cached_origin, dict) else {}
            if (
                str(cached_origin.get("candidate_id") or "")
                != str(_candidate_id(origin_signal) or "")
                or str(cached_origin.get("revision") or "")
                != str(_candidate_revision(origin_signal) or "")
            ):
                dedup = None

        if dedup:
            dedup["dedup_hit"] = True
            ctx_key = "engine_b_context" if primary_engine == "B" else "engine_a_context"
            dedup_engine_ctx = dedup.get(ctx_key) or engine_ctx
            dedup_ai_raw = dedup.get("ai_review") or {}
            dedup_ai = sanitize_ai_review_missing_context(
                dedup_engine_ctx,
                dedup_ai_raw,
            )
            dedup["ai_review"] = dedup_ai
            if primary_engine == "B":
                dedup["concordance"] = compute_engine_b_ai_concordance(
                    dedup_engine_ctx,
                    dedup_ai,
                    cfg=cfg,
                )
            else:
                dedup["concordance"] = compute_engine_a_ai_concordance(
                    dedup_engine_ctx,
                    dedup_ai,
                    cfg=cfg,
                )
            pmeta = provider_meta_from_persisted(
                provider=str(dedup.get("provider") or provider),
                model=str(dedup.get("model") or ""),
                ai_review=dedup_ai,
            )
            dedup["selectedProvider"] = provider
            dedup["fallbackUsed"] = bool(pmeta.get("fallback_used"))
            dedup["fallback_used"] = dedup["fallbackUsed"]
            dedup["primaryEngine"] = primary_engine
            _attach_review_summary(
                dedup,
                engine_ctx=dedup_engine_ctx,
                ai_review=dedup_ai,
                concordance=dedup.get("concordance") or {},
                provider_meta=pmeta,
                mismatch_warnings=list(dedup.get("mismatch_warnings") or []),
                diagnostic_ai_review=dedup_ai_raw,
                primary_engine=primary_engine,
            )
            _attach_timeframe_route(
                dedup,
                engine_ctx=dedup_engine_ctx,
                ai_review=dedup_ai,
                primary_engine=primary_engine,
            )
            return jsonify(runtime.json_safe(dedup))

        prompt = build_chart_review_prompt(engine_ctx)
        strategy_layer = _build_strategy_layer_safely(
            cfg=cfg,
            engine_ctx=engine_ctx,
            symbol=symbol,
            timeframe=timeframe,
            screenshot_meta=screenshot_meta,
            primary_engine=primary_engine,
        )
        if strategy_layer:
            try:
                strategy_block = render_strategy_block_for_prompt(strategy_layer)
                if strategy_block:
                    prompt = prompt + "\n" + strategy_block
            except Exception:
                log.exception("Failed to render strategy block; using unmodified prompt")
        payload = build_payload(
            data,
            engine_ctx,
            prompt=prompt,
            mismatch_warnings=mismatch_warnings,
            strategy_layer=strategy_layer,
        )

        try:
            raw = run_chart_review(provider, payload)
        except (
            ProviderChartReviewError,
            PermissionError,
            RuntimeError,
            NotImplementedError,
            ValueError,
        ) as exc:
            body, status = provider_error_response(exc, provider=provider)
            return jsonify(body), status

        normalized_raw = normalize_chart_review_response(
            raw.get("raw_text") or "",
            review_type=chart_review_type,
        )
        normalized = sanitize_ai_review_missing_context(engine_ctx, normalized_raw)
        if primary_engine == "B":
            concordance = compute_engine_b_ai_concordance(
                engine_ctx, normalized, cfg=cfg
            )
        else:
            concordance = compute_engine_a_ai_concordance(
                engine_ctx, normalized, cfg=cfg
            )

        provider_meta = apply_parse_fallback(
            {
                "provider": raw.get("provider") or provider,
                "model": raw.get("model"),
                "provider_status": raw.get("provider_status") or "success",
                "fallback_used": bool(raw.get("fallback_used") or raw.get("fallbackUsed")),
                "latency_ms": raw.get("latency_ms"),
            },
            normalized,
        )

        response: dict[str, Any]
        if cfg.get("PERSIST_REVIEWS", True):
            record_kwargs: dict[str, Any] = {
                "symbol": symbol,
                "timeframe": timeframe,
                "asset_group": engine_ctx.get("asset_group"),
                "provider": str(provider_meta.get("provider") or provider),
                "model": str(provider_meta.get("model") or cfg.get("ANTHROPIC_MODEL") or ""),
                "latency_ms": raw.get("latency_ms"),
                "screenshot_hash": screenshot_hash,
                "screenshot_bytes": len(screenshot_bytes),
                "screenshot_meta": screenshot_meta,
                "ai_review": normalized,
                "concordance": concordance,
                "mismatch_warnings": mismatch_warnings,
                "audit_db": getattr(runtime, "AUDIT_DB", None),
                "review_type": review_type,
            }
            if primary_engine == "B":
                record_kwargs["engine_b_context"] = engine_ctx
            else:
                record_kwargs["engine_a_context"] = engine_ctx
            response = record_review(**record_kwargs)
        else:
            ctx_key = "engine_b_context" if primary_engine == "B" else "engine_a_context"
            response = {
                "review_id": None,
                "provider": provider_meta.get("provider") or provider,
                "model": provider_meta.get("model"),
                "latency_ms": raw.get("latency_ms"),
                ctx_key: engine_ctx,
                "primaryEngine": primary_engine,
                "ai_review": normalized,
                "concordance": concordance,
                "timestamps": {
                    "scan_timestamp": engine_ctx.get("scan_timestamp"),
                    "chart_captured_at": engine_ctx.get("chart_captured_at"),
                    "latest_candle_ts": engine_ctx.get("latest_candle_ts"),
                },
                "mismatch_warnings": mismatch_warnings,
                "dedup_hit": False,
            }

        response["selectedProvider"] = raw.get("selectedProvider") or provider
        response["fallbackUsed"] = bool(
            raw.get("fallbackUsed")
            or raw.get("fallback_used")
            or provider_meta.get("fallback_used")
        )
        response["fallback_used"] = response["fallbackUsed"]
        if raw.get("providerFailure"):
            response["providerFailure"] = raw.get("providerFailure")
            response["provider_failure"] = raw.get("providerFailure")

        _attach_review_summary(
            response,
            engine_ctx=engine_ctx,
            ai_review=normalized,
            concordance=concordance,
            provider_meta=provider_meta,
            mismatch_warnings=mismatch_warnings,
            diagnostic_ai_review=normalized_raw,
            primary_engine=primary_engine,
        )
        _attach_timeframe_route(
            response,
            engine_ctx=engine_ctx,
            ai_review=normalized,
            primary_engine=primary_engine,
        )

        if strategy_layer is not None:
            response["strategy_layer"] = strategy_layer
            response["strategyLayer"] = strategy_layer

        return jsonify(runtime.json_safe(response))

    @app.post("/api/ai/chart-review/stream")
    def api_ai_chart_review_stream():
        """SSE streaming variant of /api/ai/chart-review.

        Config-gated by AI_STREAMING_ENABLED (410 when off). Reuses the
        non-streaming assembly verbatim via the registered view function, then
        emits SSE events: start {review_id, provider, model}, token {text}
        with the narrative (chartReadSummary + supporting_reasons), and done
        {ok, full_payload} carrying the complete structured response so the
        client can render the full card. Advisory-only; no execution.
        """
        if not is_streaming_enabled():
            return jsonify({"error": "streaming_disabled", "fallback": "/api/ai/chart-review"}), 410

        view = app.view_functions.get("api_ai_chart_review")
        if view is None:
            return jsonify({"error": "streaming_unavailable"}), 503

        result = view()
        if isinstance(result, tuple):
            resp, status = result
        else:
            resp, status = result, 200
        if status != 200:
            # Forward validation/disabled/provider errors as-is.
            return resp

        payload = resp.get_json() or {}

        ai_review = payload.get("ai_review") or {}
        if isinstance(ai_review, dict):
            narrative_parts: list[str] = []
            crs = ai_review.get("chartReadSummary") or ai_review.get("chart_read_summary")
            if isinstance(crs, str) and crs.strip():
                narrative_parts.append(crs)
            reasons = ai_review.get("supporting_reasons") or []
            if isinstance(reasons, list) and reasons:
                narrative_parts.extend(str(r) for r in reasons if r)
            narrative = "\n".join(narrative_parts)
        else:
            narrative = ""

        meta = {
            "review_id": payload.get("review_id"),
            "provider": payload.get("provider"),
            "model": payload.get("model"),
            "primaryEngine": payload.get("primaryEngine"),
        }

        def generate():
            yield sse_format("start", meta)
            if narrative:
                # Chunk the narrative into ~80-char token-like fragments so the
                # client sees a progressive "live read" effect.
                for i in range(0, len(narrative), 80):
                    yield sse_format("token", {"text": narrative[i : i + 80]})
            yield sse_format("done", {"ok": True, "full_payload": payload})

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
