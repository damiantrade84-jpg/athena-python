"""Report-only Engine A payload adapter for future visual audit panels.

Does not import Engine A scoring code or participate in execution. Reshapes an
already-produced Engine A result/signal into a source-mapped diagnostic payload.
Field aliases are declared in ``FIELD_SPECS``; see
``docs/visual_trade_review_engine_a_payload_contract.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_review.payload_mapping import (
    append_source_field,
    apply_field,
    assign_path,
    first_source,
    parse_feed_multiplier,
    to_float,
)


@dataclass(frozen=True)
class FieldSpec:
    output_path: str
    sources: tuple[tuple[str, str], ...]
    block: str | None = None
    also_assign: tuple[str, ...] = ()


DIRECTIONAL_RAMP_SOURCES: tuple[tuple[str, str], ...] = (
    ("engine_a_result", "directional_ramp_multiplier"),
    ("signal", "factorDiagnostics.directionalRampMult"),
    ("engine_a_result", "engine_a_asset_diagnostics.directional_ramp.multiplier"),
    ("signal", "factorDiagnostics.engineAAssetDiagnostics.directional_ramp.multiplier"),
)

FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("symbol", (
        ("signal", "symbol"), ("signal", "display"), ("signal", "pair"),
        ("engine_a_result", "symbol"), ("engine_a_result", "pair"),
    )),
    FieldSpec("timeframe", (("signal", "timeframe"), ("engine_a_result", "timeframe"))),
    FieldSpec("direction", (("engine_a_result", "direction"), ("signal", "direction"))),
    FieldSpec("final_score", (
        ("engine_a_result", "final_score"), ("engine_a_result", "score"), ("signal", "confluenceScore"),
    )),
    FieldSpec("threshold", (
        ("signal", "threshold"), ("signal", "liveThreshold"),
        ("signal", "scanThresholdEffective"), ("signal", "scanThreshold"),
    )),
    FieldSpec("trend_block.trend_score", (
        ("engine_a_result", "factor_scores.trend"), ("signal", "factorScores.trend"),
        ("engine_a_result", "directional_score"),
    ), block="trend_block"),
    FieldSpec("trend_block.trend_coherence", (
        ("engine_a_result", "trend_coherence"), ("signal", "factorDiagnostics.trendCoherence"),
    ), block="trend_block"),
    FieldSpec(
        "trend_block.directional_ramp_multiplier",
        DIRECTIONAL_RAMP_SOURCES,
        block="trend_block",
        also_assign=("multiplier_chain.directional_ramp_multiplier",),
    ),
    FieldSpec("trend_block.min_directional_threshold", (
        ("engine_a_result", "min_directional_threshold"), ("signal", "factorDiagnostics.minDirectionalThreshold"),
        ("engine_a_result", "engine_a_asset_diagnostics.directional_ramp.min_directional"),
    ), block="trend_block"),
    FieldSpec("trend_block.effective_min_directional", (
        ("engine_a_result", "effective_min_directional"), ("signal", "factorDiagnostics.effectiveMinDirectional"),
    ), block="trend_block"),
    FieldSpec("momentum_block.momentum_quality", (
        ("engine_a_result", "momentum_quality"), ("signal", "factorDiagnostics.momentumQuality"),
        ("engine_a_result", "factor_scores.momentum"), ("signal", "factorScores.momentum"),
    ), block="momentum_block"),
    FieldSpec("momentum_block.filtered_indicators", (("engine_a_result", "filtered_indicators"),), block="momentum_block"),
    FieldSpec("adx_di_block.adx_value", (
        ("engine_a_result", "adx_value"), ("signal", "factorDiagnostics.regimeLabelsDualCapture.trendStateAdxValue"),
    ), block="adx_di_block"),
    FieldSpec("adx_di_block.adx_source", (
        ("engine_a_result", "adx_source"), ("signal", "factorDiagnostics.regimeLabelsDualCapture.trendStateAdxSource"),
    ), block="adx_di_block"),
    FieldSpec("adx_di_block.adx_multiplier", (
        ("engine_a_result", "adx_multiplier"), ("signal", "factorDiagnostics.adxMultiplier"),
    ), block="adx_di_block"),
    FieldSpec("addon_block.addon_value", (
        ("engine_a_result", "addon_value"), ("engine_a_result", "factor_scores.addon"), ("signal", "factorScores.addon"),
    ), block="addon_block"),
    FieldSpec("addon_block.addon_type", (("engine_a_result", "addon_type"),), block="addon_block"),
    FieldSpec("addon_block.addon_unsupported", (("engine_a_result", "addon_unsupported"),), block="addon_block"),
    FieldSpec("addon_block.research_lab_value", (
        ("engine_a_result", "research_lab_value"), ("signal", "factorDiagnostics.researchLabValue"),
        ("engine_a_result", "factor_scores.research_lab"), ("signal", "factorScores.research_lab"),
    ), block="addon_block"),
    FieldSpec("multiplier_chain.session_multiplier", (
        ("engine_a_result", "session_multiplier"), ("signal", "session_multiplier"),
    ), block="multiplier_chain"),
    FieldSpec("multiplier_chain.equity_session_multiplier", (
        ("engine_a_result", "equity_session_multiplier"), ("signal", "equity_session_multiplier"),
    ), block="multiplier_chain"),
    FieldSpec("multiplier_chain.volatility_regime_multiplier", (
        ("engine_a_result", "volatility_regime_multiplier"), ("signal", "volatility_regime_multiplier"),
    ), block="multiplier_chain"),
    FieldSpec("adjustments.research_lab_detail", (
        ("engine_a_result", "research_lab_detail"), ("signal", "factorDiagnostics.researchLabDetail"),
    ), block="adjustments"),
    FieldSpec("adjustments.mean_reversion_value", (
        ("engine_a_result", "mean_reversion_value"), ("engine_a_result", "factor_scores.mean_reversion"),
        ("signal", "factorScores.mean_reversion"),
    ), block="adjustments"),
    FieldSpec("adjustments.mean_reversion_detail", (("engine_a_result", "mean_reversion_detail"),), block="adjustments"),
    FieldSpec("adjustments.intermarket_confirmation", (
        ("engine_a_result", "intermarket_confirmation"), ("signal", "intermarketConfirmation"),
        ("signal", "factorDiagnostics.intermarket"),
    ), block="adjustments"),
    FieldSpec("adjustments.intermarket_engine_a_delta", (
        ("engine_a_result", "intermarket_engine_a_delta"), ("signal", "intermarketConfirmation.engineADelta"),
    ), block="adjustments"),
    FieldSpec("adjustments.structure_context_adjustment", (
        ("engine_a_result", "structure_context_adjustment"), ("signal", "factorDiagnostics.explicitStructureContext"),
    ), block="adjustments"),
    FieldSpec("adjustments.engine_a_correlated_overlay_guard", (
        ("engine_a_result", "engine_a_correlated_overlay_guard"),
        ("signal", "factorDiagnostics.engineACorrelatedOverlayGuard"),
    ), block="adjustments"),
    FieldSpec("risk_block.entry", (("signal", "entry"), ("signal", "price")), block="risk_block"),
    FieldSpec("risk_block.sl", (("signal", "sl"),), block="risk_block"),
    FieldSpec("risk_block.tp", (("signal", "tp"), ("signal", "tp1")), block="risk_block"),
    FieldSpec("risk_block.atr", (("signal", "atr"),), block="risk_block"),
    FieldSpec("risk_block.atr_timeframe", (("signal", "atrDiagnostics.atr_tf"),), block="risk_block"),
    FieldSpec("risk_block.rr", (("signal", "rr"), ("signal", "rr1")), block="risk_block"),
)

FEED_MULTIPLIER_FIELDS: tuple[tuple[str, str], ...] = (
    ("multiplier_chain.vwap_filter", "vwap_filter"),
    ("multiplier_chain.volatility_scaler", "vol_scaler"),
)


def _empty_payload() -> dict[str, Any]:
    block = lambda: {"source_fields": []}  # noqa: E731
    return {
        "engine": "A",
        "symbol": None,
        "timeframe": None,
        "direction": None,
        "final_score": None,
        "max_score": 3.0,
        "threshold": None,
        "trend_block": {
            "trend_score": None,
            "trend_coherence": {},
            "directional_ramp_multiplier": None,
            "min_directional_threshold": None,
            "effective_min_directional": None,
            **block(),
        },
        "momentum_block": {"momentum_quality": None, "filtered_indicators": {}, **block()},
        "adx_di_block": {
            "adx_value": None,
            "adx_source": None,
            "adx_multiplier": None,
            "di_alignment_multiplier": None,
            **block(),
        },
        "addon_block": {
            "addon_type": None,
            "addon_value": None,
            "addon_unsupported": None,
            "research_lab_value": None,
            "feed_status": {},
            "status": "UNAVAILABLE",
            **block(),
        },
        "multiplier_chain": {
            "session_multiplier": None,
            "equity_session_multiplier": None,
            "volatility_regime_multiplier": None,
            "directional_ramp_multiplier": None,
            "vwap_filter": None,
            "volatility_scaler": None,
            **block(),
        },
        "adjustments": {
            "research_lab_detail": {},
            "mean_reversion_value": None,
            "mean_reversion_detail": {},
            "intermarket_confirmation": None,
            "intermarket_engine_a_delta": None,
            "structure_context_adjustment": {},
            "engine_a_correlated_overlay_guard": {},
            **block(),
        },
        "risk_block": {
            "entry": None,
            "sl": None,
            "tp": None,
            "atr": None,
            "atr_timeframe": None,
            "rr": None,
            **block(),
        },
    }


def _addon_status(addon_value: Any, addon_unsupported: Any, feed_status: dict[str, Any]) -> str:
    if addon_unsupported is True:
        return "UNAVAILABLE"
    feed_addon = str(feed_status.get("addon", "")).lower() if isinstance(feed_status, dict) else ""
    if "error" in feed_addon:
        return "ERROR"
    if "unsupported" in feed_addon:
        return "UNAVAILABLE"
    value = to_float(addon_value)
    if value is None:
        return "UNAVAILABLE"
    if value > 0:
        return "CONFIRMING"
    if value < 0:
        return "OPPOSING"
    return "NEUTRAL"


def build_engine_a_visual_audit_payload(
    engine_a_result: dict,
    signal: dict | None = None,
) -> dict:
    """Build the future Trader's-Eye vs Engine A report payload."""
    if not isinstance(engine_a_result, dict):
        engine_a_result = {}
    if signal is not None and not isinstance(signal, dict):
        signal = None

    roots = {"engine_a_result": engine_a_result, "signal": signal}
    unavailable_fields: list[str] = []
    source_field_map: dict[str, str] = {}
    payload = _empty_payload()
    block_sources: dict[str, list[str]] = {
        block: payload[block]["source_fields"]
        for block in (
            "trend_block", "momentum_block", "adx_di_block", "addon_block",
            "multiplier_chain", "adjustments", "risk_block",
        )
    }

    source_field_map["engine"] = "schema.constant"
    max_score, max_score_source, max_score_found = first_source(
        roots,
        (("signal", "maxScore"), ("engine_a_result", "maxScoreOverride")),
    )
    if max_score_found:
        payload["max_score"] = max_score
        source_field_map["max_score"] = max_score_source or ""
    else:
        source_field_map["max_score"] = "schema.engine_a_v2_max_score"

    for spec in FIELD_SPECS:
        block_list = block_sources.get(spec.block) if spec.block else None
        value = apply_field(
            payload,
            spec.output_path,
            roots,
            spec.sources,
            unavailable_fields=unavailable_fields,
            source_field_map=source_field_map,
            block_source_fields=block_list,
        )
        if spec.also_assign and value is not None:
            source = source_field_map.get(spec.output_path)
            for alias_path in spec.also_assign:
                assign_path(payload, alias_path, value)
                if source:
                    source_field_map[alias_path] = source
                    alias_block = alias_path.split(".", 1)[0]
                    if alias_block in block_sources:
                        append_source_field(block_sources[alias_block], source)

    feed_status, feed_source, feed_found = first_source(
        roots,
        (("engine_a_result", "feed_status"), ("signal", "factorDiagnostics.feedStatus")),
    )
    if not isinstance(feed_status, dict):
        feed_status = {}
    if feed_found:
        source_field_map["addon_block.feed_status"] = feed_source or ""
        payload["addon_block"]["feed_status"] = feed_status
        append_source_field(block_sources["addon_block"], feed_source)
    else:
        unavailable_fields.append("addon_block.feed_status")

    feed_source_prefix = feed_source or "engine_a_result.feed_status"
    di_mult, di_source = parse_feed_multiplier(feed_status, "di_align", feed_source_prefix)
    if di_mult is not None:
        payload["adx_di_block"]["di_alignment_multiplier"] = di_mult
        source_field_map["adx_di_block.di_alignment_multiplier"] = di_source or ""
        append_source_field(block_sources["adx_di_block"], di_source)
    else:
        apply_field(
            payload,
            "adx_di_block.di_alignment_multiplier",
            roots,
            (("signal", "factorDiagnostics.diAlignMult"),),
            unavailable_fields=unavailable_fields,
            source_field_map=source_field_map,
            block_source_fields=block_sources["adx_di_block"],
        )

    addon_value = payload["addon_block"]["addon_value"]
    addon_unsupported = payload["addon_block"]["addon_unsupported"]
    payload["addon_block"]["status"] = _addon_status(addon_value, addon_unsupported, feed_status)
    source_field_map["addon_block.status"] = "derived:addon_value/addon_unsupported/feed_status.addon"

    for output_path, feed_key in FEED_MULTIPLIER_FIELDS:
        value, source = parse_feed_multiplier(feed_status, feed_key, feed_source_prefix)
        block_key = output_path.split(".", 1)[0]
        if value is not None:
            assign_path(payload, output_path, value)
            source_field_map[output_path] = source or ""
            append_source_field(block_sources[block_key], source)
        else:
            unavailable_fields.append(output_path)

    payload["unavailable_fields"] = sorted(set(unavailable_fields))
    payload["source_field_map"] = source_field_map
    return payload
