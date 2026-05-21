"""Flask route for read-only AI chart review."""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace
from typing import Any

from flask import jsonify, request

from ai_review.concordance import compute_engine_a_ai_concordance
from ai_review.context_diagnostics import (
    build_context_diagnostics,
    sanitize_ai_review_missing_context,
)
from ai_review.engine_a_context import assemble_engine_a_context
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
from ai_review.providers.router import run_chart_review
from ai_review.summary import build_ai_review_summary
from ai_review.timestamp_contract import evaluate_timestamp_mismatch
from ai_review.validation import decode_screenshot_bytes, validate_request

log = logging.getLogger("sentinel.ai_chart_review")


def _resolve_provider_name(data: dict[str, Any], cfg: dict[str, Any]) -> str:
    provider = str(data.get("provider") or cfg.get("DEFAULT_PROVIDER") or "anthropic")
    if provider in ("", "default", "none"):
        provider = cfg.get("DEFAULT_PROVIDER") or "anthropic"
    return provider


def _attach_review_summary(
    response: dict[str, Any],
    *,
    engine_a_ctx: dict[str, Any],
    ai_review: dict[str, Any],
    concordance: dict[str, Any],
    provider_meta: dict[str, Any],
    mismatch_warnings: list[str],
    diagnostic_ai_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshots = engine_a_ctx.get("engine_snapshots")
    diagnostic_source = diagnostic_ai_review or ai_review
    clean_ai_review = sanitize_ai_review_missing_context(engine_a_ctx, diagnostic_source)
    response["ai_review"] = clean_ai_review
    summary = build_ai_review_summary(
        engine_a_ctx,
        clean_ai_review,
        concordance,
        provider_meta,
        engine_snapshots=snapshots,
        mismatch_warnings=mismatch_warnings,
    )
    response["aiReviewSummary"] = summary
    response["ai_review_summary"] = summary
    response.update(build_context_diagnostics(engine_a_ctx, diagnostic_source))
    return response


def register_ai_chart_review_routes(app, runtime: SimpleNamespace) -> None:
    ensure_schema(getattr(runtime, "AUDIT_DB", None))

    @app.post("/api/ai/chart-review")
    def api_ai_chart_review():
        cfg = runtime.CONFIG["AI_CHART_REVIEW"]
        if not cfg.get("ENABLED"):
            return jsonify({"error": "AI chart review disabled"}), 503

        data = request.get_json(silent=True) or {}
        err = validate_request(data, cfg)
        if err:
            return jsonify({"error": err.message}), err.status

        symbol = str(data["symbol"]).strip()
        timeframe = str(data["timeframe"]).strip()
        screenshot_meta = dict(data.get("screenshot_meta") or {})
        provider = _resolve_provider_name(data, cfg)

        engine_a_ctx = assemble_engine_a_context(
            symbol,
            timeframe,
            screenshot_meta=screenshot_meta,
            resolve_pair_fn=getattr(runtime, "resolve_pair_fn", None),
            analyze_pair_fn=getattr(runtime, "analyze_pair_fn", None),
            btc_bias_fn=getattr(runtime, "btc_bias_fn", None),
        )
        if engine_a_ctx is None:
            return jsonify({"error": "Engine A returned no result"}), 422

        atr = engine_a_ctx.setdefault("atr", {})
        freshness = classify_atr_freshness(
            atr.get("atr_tf"),
            atr.get("atr_age_seconds"),
            atr.get("atr_confirmed_only"),
        )
        atr["atr_freshness_status"] = freshness.get("status")
        atr["max_expected_age_seconds"] = freshness.get("max_expected_age_seconds")

        mismatch_warnings = evaluate_timestamp_mismatch(engine_a_ctx, screenshot_meta, cfg)
        engine_a_ctx["mismatch_warnings"] = mismatch_warnings
        engine_a_ctx["chart_captured_at"] = screenshot_meta.get("captured_at")

        screenshot_bytes = decode_screenshot_bytes(str(data["screenshot_base64"]))
        screenshot_hash = hashlib.sha256(screenshot_bytes).hexdigest()[:16]

        dedup = find_recent_review_by_hash(
            symbol,
            timeframe,
            screenshot_hash,
            int(cfg.get("DEDUP_WINDOW_SECONDS") or 60),
            audit_db=getattr(runtime, "AUDIT_DB", None),
        )
        if dedup:
            dedup["dedup_hit"] = True
            dedup_engine_ctx = dedup.get("engine_a_context") or engine_a_ctx
            dedup_ai_raw = dedup.get("ai_review") or {}
            dedup_ai = sanitize_ai_review_missing_context(
                dedup_engine_ctx,
                dedup_ai_raw,
            )
            dedup["ai_review"] = dedup_ai
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
            _attach_review_summary(
                dedup,
                engine_a_ctx=dedup_engine_ctx,
                ai_review=dedup_ai,
                concordance=dedup.get("concordance") or {},
                provider_meta=pmeta,
                mismatch_warnings=list(dedup.get("mismatch_warnings") or []),
                diagnostic_ai_review=dedup_ai_raw,
            )
            return jsonify(runtime.json_safe(dedup))

        prompt = build_chart_review_prompt(engine_a_ctx)
        payload = build_payload(
            data,
            engine_a_ctx,
            prompt=prompt,
            mismatch_warnings=mismatch_warnings,
        )

        try:
            raw = run_chart_review(data.get("provider"), payload)
        except (
            ProviderChartReviewError,
            PermissionError,
            RuntimeError,
            NotImplementedError,
            ValueError,
        ) as exc:
            body, status = provider_error_response(exc, provider=provider)
            return jsonify(body), status

        normalized_raw = normalize_chart_review_response(raw.get("raw_text") or "")
        normalized = sanitize_ai_review_missing_context(engine_a_ctx, normalized_raw)
        concordance = compute_engine_a_ai_concordance(
            engine_a_ctx, normalized, cfg=cfg
        )

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

        response: dict[str, Any]
        if cfg.get("PERSIST_REVIEWS", True):
            response = record_review(
                symbol=symbol,
                timeframe=timeframe,
                asset_group=engine_a_ctx.get("asset_group"),
                provider=provider,
                model=str(provider_meta.get("model") or cfg.get("ANTHROPIC_MODEL") or ""),
                latency_ms=raw.get("latency_ms"),
                screenshot_hash=screenshot_hash,
                screenshot_bytes=len(screenshot_bytes),
                screenshot_meta=screenshot_meta,
                engine_a_context=engine_a_ctx,
                ai_review=normalized,
                concordance=concordance,
                mismatch_warnings=mismatch_warnings,
                audit_db=getattr(runtime, "AUDIT_DB", None),
            )
        else:
            response = {
                "review_id": None,
                "provider": provider,
                "model": provider_meta.get("model"),
                "latency_ms": raw.get("latency_ms"),
                "engine_a_context": engine_a_ctx,
                "ai_review": normalized,
                "concordance": concordance,
                "timestamps": {
                    "scan_timestamp": engine_a_ctx.get("scan_timestamp"),
                    "chart_captured_at": engine_a_ctx.get("chart_captured_at"),
                    "latest_candle_ts": engine_a_ctx.get("latest_candle_ts"),
                },
                "mismatch_warnings": mismatch_warnings,
                "dedup_hit": False,
            }

        _attach_review_summary(
            response,
            engine_a_ctx=engine_a_ctx,
            ai_review=normalized,
            concordance=concordance,
            provider_meta=provider_meta,
            mismatch_warnings=mismatch_warnings,
            diagnostic_ai_review=normalized_raw,
        )

        return jsonify(runtime.json_safe(response))
