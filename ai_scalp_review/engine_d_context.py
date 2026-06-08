"""Assemble server-trusted Engine D context for scalp chart review."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ai_scalp_review.session_context import (
    resolve_engine_d_session_context,
    resolve_session_conviction_hint,
)
from config import CONFIG


def _chart_snapshot_cfg(key: str, default: Any) -> Any:
    cfg = CONFIG.get("AI_SCALP_CHART_REVIEW") or {}
    return cfg.get(key, default)


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


_ENGINE_D_MARKET_STATE_TO_PLAYBOOK = {
    "balance": "balancing",
    "imbalance": "trending",
}


def _map_engine_d_market_state(raw: Any) -> str | None:
    """Map deterministic Engine D states to playbook vocabulary for AI review."""
    state = str(raw or "").strip().lower()
    if not state:
        return None
    return _ENGINE_D_MARKET_STATE_TO_PLAYBOOK.get(state, state)


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


def _resolve_candle_understanding(engine_d_ctx: dict[str, Any]) -> dict[str, Any] | None:
    signal = engine_d_ctx.get("signal") or {}
    block = (
        engine_d_ctx.get("candle_understanding")
        or engine_d_ctx.get("candleUnderstanding")
        or signal.get("candle_understanding")
        or signal.get("candleUnderstanding")
    )
    return block if isinstance(block, dict) else None


def build_engine_d_prompt_context(engine_d_ctx: dict[str, Any]) -> dict[str, Any]:
    signal = engine_d_ctx.get("signal") or {}
    setup = engine_d_ctx.get("scalpSetup") or signal.get("scalpSetup") or {}
    location = engine_d_ctx.get("marketLocation") or signal.get("marketLocation") or {}
    aggression = engine_d_ctx.get("aggressionContext") or signal.get("aggressionContext") or {}
    source = engine_d_ctx.get("sourceContract") or signal.get("sourceContract") or {}

    when_raw = (
        engine_d_ctx.get("chart_captured_at")
        or engine_d_ctx.get("scan_timestamp")
        or engine_d_ctx.get("latest_candle_ts")
        or source.get("latestCandleTs")
    )
    asset_type = str(signal.get("type") or engine_d_ctx.get("asset_type") or "forex").lower()
    session_context = resolve_engine_d_session_context(
        when=_parse_iso_ts(when_raw),
        asset_type=asset_type,
        signal=signal,
        session_risk_state=signal.get("session_risk_state"),
    )
    session_conviction_hint = resolve_session_conviction_hint(session_context)

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
        "htfBias": engine_d_ctx.get("htf_bias") or signal.get("htf_bias"),
        "htfEngineA": engine_d_ctx.get("htf_engine_a") or signal.get("htf_engine_a"),
        "marketState": _map_engine_d_market_state(
            signal.get("market_state") or signal.get("marketState")
        ),
        "engineMarketState": signal.get("market_state") or signal.get("marketState"),
        "sessionContext": session_context,
        "sessionConvictionHint": session_conviction_hint,
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
        "chartSnapshot": engine_d_ctx.get("chart_snapshot"),
        "renderedLayers": engine_d_ctx.get("rendered_layers"),
        "candleUnderstanding": _resolve_candle_understanding(engine_d_ctx),
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
    warnings.extend(_validate_chart_snapshot_warnings(engine_d_ctx, screenshot_meta))
    return warnings


def _parse_iso_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _level_close(a: Any, b: Any, tol_pct: float = 0.0015) -> bool:
    fa, fb = _to_float(a), _to_float(b)
    if fa is None or fb is None:
        return True
    tol = max(abs(fa) * tol_pct, 1e-8)
    return abs(fa - fb) <= tol


def _validate_chart_snapshot_warnings(
    engine_d_ctx: dict[str, Any],
    screenshot_meta: dict[str, Any] | None,
) -> list[str]:
    warnings: list[str] = []
    snapshot = (screenshot_meta or {}).get("chart_snapshot")
    if not isinstance(snapshot, dict):
        return warnings
    selected = snapshot.get("selectedSignal") or {}

    snap_dir = str(selected.get("direction") or "").upper()
    srv_dir = str(engine_d_ctx.get("direction") or "").upper()
    if not snap_dir or not srv_dir or snap_dir == "NONE" or srv_dir == "NONE":
        return warnings

    setup = engine_d_ctx.get("scalpSetup") or {}
    location = engine_d_ctx.get("marketLocation") or {}
    profile = snapshot.get("profile") or {}

    if snap_dir != srv_dir:
        warnings.append("client_server_direction_mismatch")

    for field, snap_key, srv_key in (
        ("entry", "entry", "entry"),
        ("stopLoss", "stopLoss", "stopLoss"),
        ("tp1", "tp1", "tp1"),
    ):
        if not _level_close(selected.get(snap_key), setup.get(srv_key)):
            warnings.append(f"client_server_{field}_mismatch")

    for field, snap_key, srv_key in (("poc", "poc", "poc"), ("vah", "vah", "vah"), ("val", "val", "val")):
        if not _level_close(profile.get(snap_key), location.get(srv_key)):
            warnings.append(f"client_server_profile_{field}_mismatch")

    snap_grade = str(selected.get("grade") or selected.get("ai_grade") or "").upper()
    srv_grade = str(engine_d_ctx.get("ai_grade") or "").upper()
    if snap_grade and srv_grade and snap_grade != srv_grade:
        warnings.append("client_server_grade_mismatch")

    snap_candle = _parse_iso_ts(snapshot.get("latestCandleTs"))
    srv_candle = _parse_iso_ts(engine_d_ctx.get("latest_candle_ts"))
    if snap_candle and srv_candle and snap_candle < srv_candle:
        warnings.append("client_chart_older_than_server_latest_candle")

    return warnings


def _timeframe_bar_seconds(timeframe: str | None) -> int:
    raw = str(timeframe or "").strip().upper()
    if raw.startswith("M") and raw[1:].isdigit():
        return int(raw[1:]) * 60
    if raw.startswith("H") and raw[1:].isdigit():
        return int(raw[1:]) * 3600
    if raw in {"D", "D1", "1D"}:
        return 86400
    return 60


def _chart_data_staleness_budget_seconds(
    timeframe: str | None,
    *,
    max_age: int | None = None,
) -> int:
    """Max allowed lag between chart bar open time and capture time."""
    base = int(
        max_age
        if max_age is not None
        else _chart_snapshot_cfg("MAX_CHART_AGE_SECONDS", 120)
    )
    bar_seconds = _timeframe_bar_seconds(timeframe)
    # One full bar can still be forming; allow two bars plus a small clock-skew buffer.
    return max(base, (bar_seconds * 2) + 30)


def severe_chart_snapshot_reject_reason(
    symbol: str,
    engine_d_ctx: dict[str, Any],
    screenshot_meta: dict[str, Any] | None,
    *,
    has_screenshot: bool = True,
) -> str | None:
    if not has_screenshot:
        return "missing_screenshot"
    snapshot = (screenshot_meta or {}).get("chart_snapshot")
    if not isinstance(snapshot, dict):
        return "missing_chart_snapshot"
    selected = snapshot.get("selectedSignal")
    if not isinstance(selected, dict):
        return "missing_selected_signal"

    snap_symbol = str(snapshot.get("symbol") or "").strip().upper()
    srv_symbol = str(engine_d_ctx.get("symbol") or symbol or "").strip().upper()
    snap_compact = snap_symbol.replace("/", "").replace("-", "")
    srv_compact = srv_symbol.replace("/", "").replace("-", "")
    if snap_symbol and srv_symbol and snap_compact != srv_compact and snap_symbol != srv_symbol:
        keys = _symbol_keys(symbol)
        if snap_symbol not in keys and snap_compact not in {k.replace("/", "").replace("-", "") for k in keys}:
            return "symbol_mismatch"

    snap_dir = str(selected.get("direction") or "").upper()
    srv_dir = str(engine_d_ctx.get("direction") or "").upper()
    if snap_dir and srv_dir and srv_dir != "NONE" and snap_dir != "NONE" and snap_dir != srv_dir:
        return "direction_mismatch"

    captured_at = _parse_iso_ts((screenshot_meta or {}).get("captured_at"))
    latest = _parse_iso_ts(snapshot.get("latestCandleTs") or engine_d_ctx.get("latest_candle_ts"))
    max_age = int(_chart_snapshot_cfg("MAX_CHART_AGE_SECONDS", 120))
    chart_tf = str(
        snapshot.get("timeframe")
        or (screenshot_meta or {}).get("chart_timeframe")
        or engine_d_ctx.get("timeframe")
        or ""
    ).upper()
    data_staleness_budget = _chart_data_staleness_budget_seconds(chart_tf, max_age=max_age)
    now = datetime.now(timezone.utc)
    if captured_at and (now - captured_at).total_seconds() > max_age:
        return "chart_capture_too_old"
    if (
        latest
        and captured_at
        and (captured_at - latest).total_seconds() > data_staleness_budget
    ):
        return "chart_data_stale"

    return None


def sanitize_client_chart_snapshot(screenshot_meta: dict[str, Any] | None) -> dict[str, Any] | None:
    snapshot = (screenshot_meta or {}).get("chart_snapshot")
    if not isinstance(snapshot, dict):
        return None
    return {
        "symbol": snapshot.get("symbol"),
        "timeframe": snapshot.get("timeframe"),
        "latestCandleTs": snapshot.get("latestCandleTs"),
        "selectedSignal": snapshot.get("selectedSignal"),
        "profile": snapshot.get("profile"),
        "liquidity": snapshot.get("liquidity"),
        "engineBContext": snapshot.get("engineBContext"),
        "orderFlow": snapshot.get("orderFlow"),
        "advisory": snapshot.get("advisory"),
        "renderedLayers": snapshot.get("renderedLayers"),
        "thesisBadge": snapshot.get("thesisBadge"),
    }


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
        "chart_snapshot": (screenshot_meta or {}).get("chart_snapshot"),
        "rendered_layers": (screenshot_meta or {}).get("renderedLayers")
        or (screenshot_meta or {}).get("rendered_layers"),
        "signal": picked,
        "scalpSetup": picked.get("scalpSetup") or {},
        "marketLocation": picked.get("marketLocation") or {},
        "aggressionContext": picked.get("aggressionContext") or {},
        "sourceContract": picked.get("sourceContract") or {},
        "ohlcv_bars": [],
    }
    ctx["mismatch_warnings"] = _build_mismatch_warnings(ctx, screenshot_meta)
    ctx["clientChartSnapshot"] = sanitize_client_chart_snapshot(screenshot_meta)
    rendered = (screenshot_meta or {}).get("rendered_layers")
    if isinstance(rendered, dict):
        ctx["rendered_layers"] = rendered
    return ctx
