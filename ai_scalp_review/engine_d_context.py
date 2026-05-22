"""Assemble server-trusted Engine D context for scalp chart review."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _symbol_keys(symbol: str) -> set[str]:
    raw = str(symbol or "").strip().upper()
    compact = raw.replace("/", "").replace("-", "").replace(" ", "")
    return {k for k in (raw, compact) if k}


def _signal_matches_symbol(signal: dict[str, Any], symbol: str) -> bool:
    keys = _symbol_keys(symbol)
    for field in ("symbol", "pair", "display"):
        val = str(signal.get(field) or "").strip().upper()
        if not val:
            continue
        compact = val.replace("/", "").replace("-", "").replace(" ", "")
        if val in keys or compact in keys:
            return True
    return False


def _pick_best_signal(signals: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    matches = [s for s in signals if isinstance(s, dict) and _signal_matches_symbol(s, symbol)]
    if not matches:
        return None
    directional = [
        s
        for s in matches
        if str(s.get("direction") or "NONE").upper() in ("LONG", "SHORT")
    ]
    pool = directional or matches
    return max(pool, key=lambda s: _to_float(s.get("ai_score")) or 0.0)


def build_engine_d_summary_for_strategy(engine_d_ctx: dict[str, Any]) -> dict[str, Any]:
    signal = engine_d_ctx.get("signal") or {}
    setup = engine_d_ctx.get("scalpSetup") or signal.get("scalpSetup") or {}
    source = engine_d_ctx.get("sourceContract") or signal.get("sourceContract") or {}
    return {
        "available": bool(engine_d_ctx.get("setup_available")),
        "passed": bool(engine_d_ctx.get("executable")),
        "ai_grade": engine_d_ctx.get("ai_grade"),
        "ai_score": engine_d_ctx.get("ai_score"),
        "direction": engine_d_ctx.get("direction"),
        "setup_type": setup.get("setupType") or signal.get("zone_type"),
        "strict_fabio_pass": engine_d_ctx.get("strict_fabio_pass"),
        "source_quality_ok": source.get("strictOrderflowSourcePass"),
    }


def build_engine_d_prompt_context(engine_d_ctx: dict[str, Any]) -> dict[str, Any]:
    signal = engine_d_ctx.get("signal") or {}
    setup = engine_d_ctx.get("scalpSetup") or signal.get("scalpSetup") or {}
    location = engine_d_ctx.get("marketLocation") or signal.get("marketLocation") or {}
    aggression = engine_d_ctx.get("aggressionContext") or signal.get("aggressionContext") or {}
    source = engine_d_ctx.get("sourceContract") or signal.get("sourceContract") or {}
    return {
        "symbol": engine_d_ctx.get("symbol"),
        "timeframe": engine_d_ctx.get("timeframe"),
        "executionTf": engine_d_ctx.get("execution_tf"),
        "direction": engine_d_ctx.get("direction"),
        "aiGrade": engine_d_ctx.get("ai_grade"),
        "aiScore": engine_d_ctx.get("ai_score"),
        "executable": engine_d_ctx.get("executable"),
        "gateResult": engine_d_ctx.get("gate_result"),
        "setupType": setup.get("setupType") or signal.get("zone_type"),
        "entry": setup.get("entry"),
        "stopLoss": setup.get("stopLoss"),
        "tp1": setup.get("tp1"),
        "tp2": setup.get("tp2"),
        "rr1": setup.get("rr1"),
        "locationLabel": location.get("locationLabel"),
        "poc": location.get("poc"),
        "vah": location.get("vah"),
        "val": location.get("val"),
        "lvnLevels": location.get("lvnLevels"),
        "aggressionLabel": aggression.get("aggressionLabel"),
        "cvd": aggression.get("cvd"),
        "cvdSlope": aggression.get("cvdSlope"),
        "absorptionDetected": aggression.get("absorptionDetected"),
        "sourceContract": {
            "candleSourceIsReal": source.get("candleSourceIsReal"),
            "volumeSourceIsReal": source.get("volumeSourceIsReal"),
            "orderflowSourceIsReal": source.get("orderflowSourceIsReal"),
            "cvdSourceIsReal": source.get("cvdSourceIsReal"),
            "vpSourceIsReal": source.get("vpSourceIsReal"),
            "strictOrderflowSourcePass": source.get("strictOrderflowSourcePass"),
            "strictTimestampAlignmentPass": source.get("strictTimestampAlignmentPass"),
            "unavailableReasons": source.get("unavailableReasons") or [],
        },
    }


def _build_mismatch_warnings(engine_d_ctx: dict[str, Any], screenshot_meta: dict[str, Any] | None) -> list[str]:
    warnings: list[str] = []
    source = engine_d_ctx.get("sourceContract") or {}
    if source.get("venueMismatch"):
        warnings.append("venue_mismatch_between_data_and_execution")
    if source.get("strictOrderflowSourcePass") is False:
        warnings.append("orderflow_source_not_verified_real")
    if source.get("strictTimestampAlignmentPass") is False:
        warnings.append("timestamp_alignment_failed")
    unavailable = source.get("unavailableReasons") or []
    if isinstance(unavailable, list) and unavailable:
        warnings.append("source_contract_incomplete")
    chart_tf = str((screenshot_meta or {}).get("chart_timeframe") or "").upper()
    exec_tf = str(engine_d_ctx.get("execution_tf") or "").upper()
    if chart_tf and exec_tf and chart_tf != exec_tf:
        warnings.append(f"chart_tf_{chart_tf}_differs_from_execution_tf_{exec_tf}")
    return warnings


def assemble_engine_d_context(
    symbol: str,
    timeframe: str,
    *,
    screenshot_meta: dict[str, Any] | None = None,
    resolve_pair_fn: Callable[[str], dict[str, Any] | None] | None = None,
    run_scalp_scan_fn: Callable[[list[str]], dict[str, Any] | None] | None = None,
    scalp_ui_signal_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    if not resolve_pair_fn or not run_scalp_scan_fn or not scalp_ui_signal_fn:
        return None

    pair = resolve_pair_fn(symbol)
    if not pair:
        return None

    display = str(pair.get("display") or pair.get("symbol") or symbol).strip()
    scan = run_scalp_scan_fn([display])
    if not isinstance(scan, dict):
        return None

    raw_signals = scan.get("signals") or []
    ui_signals = [
        scalp_ui_signal_fn(s) for s in raw_signals if isinstance(s, dict)
    ]
    ui_signals = [s for s in ui_signals if isinstance(s, dict)]
    picked = _pick_best_signal(ui_signals, symbol)
    if picked is None:
        skipped = scan.get("skipped") or []
        for row in skipped:
            if isinstance(row, dict) and _signal_matches_symbol(row, symbol):
                picked = scalp_ui_signal_fn(row) if scalp_ui_signal_fn else row
                break
    if picked is None:
        return None

    scan_ts = picked.get("timestamp") or datetime.now(timezone.utc).isoformat()
    source = picked.get("sourceContract") or {}
    latest_candle_ts = source.get("latestCandleTs")

    ctx = {
        "symbol": picked.get("symbol") or picked.get("pair") or display,
        "timeframe": timeframe,
        "execution_tf": picked.get("execution_tf") or picked.get("timeframe") or timeframe,
        "direction": str(picked.get("direction") or "NONE").upper(),
        "ai_grade": picked.get("ai_grade"),
        "ai_score": _to_float(picked.get("ai_score")),
        "executable": bool(picked.get("executable")),
        "gate_result": picked.get("gate_result") or picked.get("gateResult"),
        "strict_fabio_pass": picked.get("strict_fabio_pass"),
        "setup_available": str(picked.get("direction") or "NONE").upper() in ("LONG", "SHORT"),
        "scan_timestamp": scan_ts,
        "latest_candle_ts": latest_candle_ts,
        "chart_captured_at": (screenshot_meta or {}).get("captured_at"),
        "signal": picked,
        "scalpSetup": picked.get("scalpSetup") or {},
        "marketLocation": picked.get("marketLocation") or {},
        "aggressionContext": picked.get("aggressionContext") or {},
        "sourceContract": picked.get("sourceContract") or {},
        "ohlcv_bars": [],
    }
    ctx["mismatch_warnings"] = _build_mismatch_warnings(ctx, screenshot_meta)
    return ctx
