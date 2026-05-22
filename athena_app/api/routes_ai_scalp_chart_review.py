"""Flask route for read-only Engine D scalp chart review."""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace
from typing import Any

from flask import jsonify, request

from ai_review.persistence import ensure_schema, find_recent_review_by_hash, record_review
from ai_review.provider_meta import (
    ProviderChartReviewError,
    apply_parse_fallback,
    provider_error_response,
    provider_meta_from_persisted,
)
from ai_review.validation import decode_screenshot_bytes, validate_request
from ai_scalp_review.concordance import compute_scalp_ai_concordance
from ai_scalp_review.context_diagnostics import build_scalp_context_diagnostics
from ai_scalp_review.engine_d_context import (
    assemble_engine_d_context,
    build_engine_d_summary_for_strategy,
)
from ai_scalp_review.normalizer import normalize_scalp_chart_review_response
from ai_scalp_review.payload_schema import build_scalp_payload
from ai_scalp_review.prompt_builder import build_scalp_chart_review_prompt
from ai_scalp_review.provider import run_scalp_chart_review
from ai_scalp_review.scalp_verdict import build_scalp_verdict_comparison
from ai_scalp_review.summary import build_scalp_ai_review_summary
from athena_ai.ai_review_payload_builder import (
    build_strategy_layer,
    render_strategy_block_for_prompt,
)

log = logging.getLogger("sentinel.ai_scalp_chart_review")

_ALLOWED_REQUEST_KEYS = frozenset(
    {"symbol", "timeframe", "provider", "screenshot_base64", "screenshot_meta"}
)
_REVIEW_TYPE = "engine_d"


def _resolve_provider_name(data: dict[str, Any], cfg: dict[str, Any]) -> str:
    provider = str(data.get("provider") or cfg.get("DEFAULT_PROVIDER") or "anthropic")
    if provider in ("", "default", "none"):
        provider = cfg.get("DEFAULT_PROVIDER") or "anthropic"
    return provider


def _reject_extra_request_keys(data: dict[str, Any]) -> str | None:
    extra = sorted(set(data.keys()) - _ALLOWED_REQUEST_KEYS)
    if extra:
        return f"unexpected request keys: {', '.join(extra)}"
    return None


def _build_scalp_review_input_meta(engine_d_ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": engine_d_ctx.get("symbol"),
        "signalEngine": "D",
        "signalTimeframe": engine_d_ctx.get("execution_tf") or engine_d_ctx.get("timeframe"),
        "chartTimeframe": engine_d_ctx.get("timeframe"),
        "hasEngineASignal": False,
        "hasEngineBOverlay": False,
        "hasChartImage": True,
        "timeframeRouteApplied": False,
    }


def _attach_scalp_review_summary(
    response: dict[str, Any],
    *,
    engine_d_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    provider_meta: dict[str, Any],
    mismatch_warnings: list[str],
) -> dict[str, Any]:
    structured = ai_review.get("structured") or {}
    if not isinstance(structured, dict):
        structured = {}
    model_summary = structured.get("aiReviewSummary") or structured.get("ai_review_summary")
    model_comparison = structured.get("scalpVerdictComparison") or structured.get(
        "scalp_verdict_comparison"
    )
    summary = build_scalp_ai_review_summary(
        engine_d_ctx,
        ai_review,
        provider_meta,
        mismatch_warnings=mismatch_warnings,
        model_summary=model_summary if isinstance(model_summary, dict) else None,
    )
    verdict_comparison = build_scalp_verdict_comparison(
        engine_d_ctx,
        ai_review,
        model_comparison=model_comparison if isinstance(model_comparison, dict) else None,
    )
    ctx_diag = build_scalp_context_diagnostics(engine_d_ctx, ai_review)
    response["ai_review"] = ai_review
    response["aiReviewSummary"] = summary
    response["ai_review_summary"] = summary
    response["scalpVerdictComparison"] = verdict_comparison
    response["scalp_verdict_comparison"] = verdict_comparison
    response.update(ctx_diag)
    response["reviewInputMeta"] = _build_scalp_review_input_meta(engine_d_ctx)
    plan = ai_review.get("suggestedTradePlan") or ai_review.get("suggested_trade_plan")
    if isinstance(plan, dict):
        response["suggestedTradePlan"] = plan
        response["suggested_trade_plan"] = plan
    return response


def _build_strategy_layer_safely(
    *,
    cfg: dict[str, Any],
    engine_d_ctx: dict[str, Any],
    symbol: str,
    timeframe: str,
) -> dict[str, Any] | None:
    if not cfg.get("STRATEGY_LAYER_ENABLED", True):
        return None
    try:
        limit = int(cfg.get("STRATEGY_LAYER_OHLCV_LIMIT") or 80)
        ohlcv_window = engine_d_ctx.get("ohlcv_bars")
        if not isinstance(ohlcv_window, list) or not ohlcv_window:
            ohlcv_window = None
        engine_d_summary = build_engine_d_summary_for_strategy(engine_d_ctx)
        return build_strategy_layer(
            engine_a_ctx={},
            ohlcv_window=ohlcv_window,
            engine_b_summary=None,
            engine_d_summary=engine_d_summary,
            symbol=symbol,
            timeframe=timeframe,
            asset_group=None,
            direction=engine_d_ctx.get("direction"),
            ohlcv_limit=limit,
        )
    except Exception:
        log.exception(
            "Scalp strategy layer build failed for %s %s; continuing without it",
            symbol,
            timeframe,
        )
        return None


def register_ai_scalp_chart_review_routes(app, runtime: SimpleNamespace) -> None:
    ensure_schema(getattr(runtime, "AUDIT_DB", None))

    @app.post("/api/ai/scalp-chart-review")
    def api_ai_scalp_chart_review():
        cfg = runtime.CONFIG["AI_SCALP_CHART_REVIEW"]
        if not cfg.get("ENABLED"):
            return jsonify({"error": "AI scalp chart review disabled"}), 503

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
        provider = _resolve_provider_name(data, cfg)

        engine_d_ctx = assemble_engine_d_context(
            symbol,
            timeframe,
            screenshot_meta=screenshot_meta,
            resolve_pair_fn=getattr(runtime, "resolve_pair_fn", None),
            run_scalp_scan_fn=getattr(runtime, "run_scalp_scan_fn", None),
            scalp_ui_signal_fn=getattr(runtime, "scalp_ui_signal_fn", None),
        )
        if engine_d_ctx is None:
            return jsonify({"error": "Engine D returned no result for symbol"}), 422

        mismatch_warnings = list(engine_d_ctx.get("mismatch_warnings") or [])
        engine_d_ctx["chart_captured_at"] = screenshot_meta.get("captured_at")

        screenshot_bytes = decode_screenshot_bytes(str(data["screenshot_base64"]))
        screenshot_hash = hashlib.sha256(screenshot_bytes).hexdigest()[:16]

        dedup = find_recent_review_by_hash(
            symbol,
            timeframe,
            screenshot_hash,
            int(cfg.get("DEDUP_WINDOW_SECONDS") or 60),
            audit_db=getattr(runtime, "AUDIT_DB", None),
            review_type=_REVIEW_TYPE,
        )
        if dedup:
            dedup["dedup_hit"] = True
            dedup_engine_ctx = dedup.get("engine_d_context") or engine_d_ctx
            dedup_ai = dedup.get("ai_review") or {}
            dedup["concordance"] = compute_scalp_ai_concordance(dedup_engine_ctx, dedup_ai)
            pmeta = provider_meta_from_persisted(
                provider=str(dedup.get("provider") or provider),
                model=str(dedup.get("model") or ""),
                ai_review=dedup_ai,
            )
            _attach_scalp_review_summary(
                dedup,
                engine_d_ctx=dedup_engine_ctx,
                ai_review=dedup_ai,
                provider_meta=pmeta,
                mismatch_warnings=list(dedup.get("mismatch_warnings") or []),
            )
            return jsonify(runtime.json_safe(dedup))

        prompt = build_scalp_chart_review_prompt(engine_d_ctx)
        strategy_layer = _build_strategy_layer_safely(
            cfg=cfg,
            engine_d_ctx=engine_d_ctx,
            symbol=symbol,
            timeframe=timeframe,
        )
        if strategy_layer:
            try:
                strategy_block = render_strategy_block_for_prompt(strategy_layer)
                if strategy_block:
                    prompt = prompt + "\n" + strategy_block
            except Exception:
                log.exception("Failed to render scalp strategy block; using unmodified prompt")

        payload = build_scalp_payload(
            data,
            engine_d_ctx,
            prompt=prompt,
            mismatch_warnings=mismatch_warnings,
            strategy_layer=strategy_layer,
        )

        try:
            raw = run_scalp_chart_review(data.get("provider"), payload)
        except (
            ProviderChartReviewError,
            PermissionError,
            RuntimeError,
            NotImplementedError,
            ValueError,
        ) as exc:
            body, status = provider_error_response(exc, provider=provider)
            return jsonify(body), status

        setup = engine_d_ctx.get("scalpSetup") or {}
        backend_levels = {
            "stop_loss": setup.get("stopLoss"),
            "stopLoss": setup.get("stopLoss"),
            "entry": setup.get("entry"),
        }
        normalized = normalize_scalp_chart_review_response(
            raw.get("raw_text") or "",
            backend_levels=backend_levels,
        )
        concordance = compute_scalp_ai_concordance(engine_d_ctx, normalized)
        provider_meta = apply_parse_fallback(
            {
                "provider": raw.get("provider") or provider,
                "model": raw.get("model"),
                "provider_status": raw.get("provider_status") or "success",
                "fallback_used": bool(raw.get("fallback_used")),
                "latency_ms": raw.get("latency_ms"),
            },
            normalized,
        )

        if cfg.get("PERSIST_REVIEWS", True):
            response = record_review(
                symbol=symbol,
                timeframe=timeframe,
                asset_group=None,
                provider=provider,
                model=str(provider_meta.get("model") or cfg.get("ANTHROPIC_MODEL") or ""),
                latency_ms=raw.get("latency_ms"),
                screenshot_hash=screenshot_hash,
                screenshot_bytes=len(screenshot_bytes),
                screenshot_meta=screenshot_meta,
                engine_d_context=engine_d_ctx,
                ai_review=normalized,
                concordance=concordance,
                mismatch_warnings=mismatch_warnings,
                audit_db=getattr(runtime, "AUDIT_DB", None),
                review_type=_REVIEW_TYPE,
            )
        else:
            response = {
                "review_id": None,
                "provider": provider,
                "model": provider_meta.get("model"),
                "latency_ms": raw.get("latency_ms"),
                "engine_d_context": engine_d_ctx,
                "ai_review": normalized,
                "concordance": concordance,
                "timestamps": {
                    "scan_timestamp": engine_d_ctx.get("scan_timestamp"),
                    "chart_captured_at": engine_d_ctx.get("chart_captured_at"),
                    "latest_candle_ts": engine_d_ctx.get("latest_candle_ts"),
                },
                "mismatch_warnings": mismatch_warnings,
                "dedup_hit": False,
            }

        _attach_scalp_review_summary(
            response,
            engine_d_ctx=engine_d_ctx,
            ai_review=normalized,
            provider_meta=provider_meta,
            mismatch_warnings=mismatch_warnings,
        )

        if strategy_layer is not None:
            response["strategy_layer"] = strategy_layer
            response["strategyLayer"] = strategy_layer

        return jsonify(runtime.json_safe(response))
