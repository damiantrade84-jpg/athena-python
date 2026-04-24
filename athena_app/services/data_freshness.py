"""Candle freshness diagnostics and execution-time data integrity gates."""

from __future__ import annotations

from typing import Any

from athena_app.services.market_state import (
    candle_freshness_diagnostic,
    candle_timestamp_epoch,
    get_bucket_start_epoch,
    market_state_offset_hours,
    split_market_state,
)


DEFAULT_DATA_FRESHNESS_GATES = {
    "WARN_ON_STALE_SCAN": True,
    "BLOCK_EXECUTION_ON_STALE": True,
    "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
    "BLOCK_SEVERITIES": [
        "missing_current_bucket",
        "stale_1_bucket",
        "stale_multi_bucket",
        "error_path_mismatch",
        "error_offset_mismatch",
    ],
}

_ERROR_STATUSES = {
    "ERROR_STALE_MULTI_BUCKET",
    "ERROR_PATH_MISMATCH",
    "ERROR_OFFSET_MISMATCH",
}
_WARNING_STATUSES = {
    "WARNING_FORMING_USED",
    "WARNING_ONE_BUCKET_LAG",
}


def _tf_seconds(tf: str) -> int:
    return {
        "M1": 60,
        "M5": 300,
        "M15": 900,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400,
    }.get(str(tf or "").upper(), 3600)


def _normalize_block_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("ERROR_") or text.startswith("WARNING_") or text == "OK":
        text = text.lower()
    return text.lower()


def _latest_epoch(candles: list[dict[str, Any]] | None) -> int | None:
    if not candles:
        return None
    epoch = candle_timestamp_epoch(candles[-1] if isinstance(candles[-1], dict) else None)
    return int(epoch) if epoch else None


def _state_epochs(state: dict[str, Any]) -> tuple[int | None, int | None]:
    confirmed = state.get("confirmed") or []
    forming = state.get("forming")
    confirmed_epoch = _latest_epoch(confirmed)
    forming_epoch = candle_timestamp_epoch(forming) if isinstance(forming, dict) else 0
    return confirmed_epoch, int(forming_epoch) if forming_epoch else None


def build_live_feed_diagnostic(
    pair: dict[str, Any],
    timeframe: str,
    candles: list[dict[str, Any]] | None,
    *,
    fetch_meta: dict[str, Any] | None = None,
    time_now: float | None = None,
    source: str | None = None,
    fetch_duration_ms: float | None = None,
    provider_status: str | None = None,
    provider_error: str | None = None,
) -> dict[str, Any]:
    """Return one log/API-safe diagnostic row for a symbol/timeframe."""
    meta = fetch_meta if isinstance(fetch_meta, dict) else {}
    series = list(candles or [])
    diag = candle_freshness_diagnostic(
        pair,
        timeframe,
        series,
        time_now=time_now,
        source=source or meta.get("upstream") or pair.get("source"),
    )
    if provider_status is None:
        if provider_error or meta.get("error"):
            provider_status = "error"
        elif meta.get("rateLimited"):
            provider_status = "rate_limited"
        elif not series:
            provider_status = "unavailable"
        else:
            provider_status = "ok"

    provider_error = (
        provider_error
        or meta.get("detail")
        or meta.get("errorDetail")
        or meta.get("error")
        or None
    )
    diag.update(
        {
            "asset_type": pair.get("type"),
            "candle_count": len(series),
            "isFromCache": bool(meta.get("cacheHit")),
            "cacheBypassed": bool(meta.get("cacheBypass") or meta.get("cacheWriteSkipped")),
            "fetchDurationMs": round(float(fetch_duration_ms), 3)
            if fetch_duration_ms is not None
            else None,
            "providerStatus": provider_status,
            "providerError": provider_error,
        }
    )
    return diag


def _normalise_path(
    pair: dict[str, Any],
    timeframe: str,
    name: str,
    payload: Any,
    *,
    time_now: float | None,
) -> dict[str, Any]:
    tf = str(timeframe or "").upper()
    if isinstance(payload, dict) and "expectedCurrentBucketEpoch" in payload:
        out = dict(payload)
    elif isinstance(payload, dict) and ("confirmed" in payload or "forming" in payload):
        confirmed_epoch, forming_epoch = _state_epochs(payload)
        candles = list(payload.get("confirmed") or [])
        if payload.get("forming"):
            candles.append(payload["forming"])
        out = candle_freshness_diagnostic(pair, tf, candles, time_now=time_now)
        out["confirmedLatestEpoch"] = confirmed_epoch
        out["formingEpoch"] = forming_epoch
        out["usesForming"] = bool(forming_epoch and out.get("lastBarEpoch") == forming_epoch)
    elif isinstance(payload, list):
        out = candle_freshness_diagnostic(pair, tf, payload, time_now=time_now)
        state = split_market_state(
            payload,
            tf,
            pair.get("display") or pair.get("symbol") or "",
            time_now=time_now,
            offset_hours=market_state_offset_hours(pair, tf),
        )
        confirmed_epoch, forming_epoch = _state_epochs(state)
        out["confirmedLatestEpoch"] = confirmed_epoch
        out["formingEpoch"] = forming_epoch
        out["usesForming"] = bool(forming_epoch and out.get("lastBarEpoch") == forming_epoch)
    else:
        out = candle_freshness_diagnostic(pair, tf, [], time_now=time_now)
        out["confirmedLatestEpoch"] = None
        out["formingEpoch"] = None
        out["usesForming"] = False
    out["path"] = name
    return out


def check_live_candle_consistency(
    pair: dict[str, Any],
    timeframe: str,
    paths: dict[str, Any],
    *,
    time_now: float | None = None,
) -> dict[str, Any]:
    """Compare live candle state across provider/cache/engine/scanner paths."""
    tf = str(timeframe or "").upper()
    expected_offset = market_state_offset_hours(pair, tf)
    expected_bucket = (
        get_bucket_start_epoch(tf, time_now, expected_offset)
        if time_now is not None
        else None
    )
    diagnostics = {
        name: _normalise_path(pair, tf, name, payload, time_now=time_now)
        for name, payload in (paths or {}).items()
    }
    reasons: list[str] = []

    for name, diag in diagnostics.items():
        diag_offset = float(diag.get("offsetHours", 0.0) or 0.0)
        if abs(diag_offset - expected_offset) > 1e-9:
            reasons.append(f"{name}: offset {diag_offset} != expected {expected_offset}")
        if expected_bucket is not None and diag.get("expectedCurrentBucketEpoch") != int(expected_bucket):
            reasons.append(
                f"{name}: expected bucket {diag.get('expectedCurrentBucketEpoch')} != {int(expected_bucket)}"
            )
    if reasons:
        return {
            "status": "ERROR_OFFSET_MISMATCH",
            "reason": "; ".join(reasons),
            "paths": diagnostics,
        }

    if any(
        d.get("stalenessSeverity") in ("missing_current_bucket", "stale_multi_bucket")
        for d in diagnostics.values()
    ):
        return {
            "status": "ERROR_STALE_MULTI_BUCKET",
            "reason": "one or more paths are missing the current bucket or lag multiple buckets",
            "paths": diagnostics,
        }

    engine_epochs = {
        k: (v.get("confirmedLatestEpoch") if v.get("usesForming") else v.get("lastBarEpoch"))
        for k, v in diagnostics.items()
        if k in ("engine_a", "engine_b", "scanner", "compare")
        and (v.get("confirmedLatestEpoch") if v.get("usesForming") else v.get("lastBarEpoch")) is not None
    }
    if len(set(engine_epochs.values())) > 1:
        return {
            "status": "ERROR_PATH_MISMATCH",
            "reason": f"path latest epochs differ: {engine_epochs}",
            "paths": diagnostics,
        }

    if any(int(d.get("bucketLag") or 0) == 1 for d in diagnostics.values()):
        return {
            "status": "WARNING_ONE_BUCKET_LAG",
            "reason": "one or more paths lag by one bucket",
            "paths": diagnostics,
        }

    if any(
        d.get("usesForming")
        for name, d in diagnostics.items()
        if name in ("engine_a", "engine_b", "scanner", "compare")
    ):
        return {
            "status": "WARNING_FORMING_USED",
            "reason": "one or more scoring paths include the forming bucket",
            "paths": diagnostics,
        }

    return {"status": "OK", "reason": "", "paths": diagnostics}


def _gate_config(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(DEFAULT_DATA_FRESHNESS_GATES)
    raw = (config or {}).get("DATA_FRESHNESS_GATES")
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["BLOCK_TIMEFRAMES"] = {str(v).upper() for v in cfg.get("BLOCK_TIMEFRAMES", [])}
    cfg["BLOCK_SEVERITIES"] = {
        _normalize_block_code(v) for v in cfg.get("BLOCK_SEVERITIES", [])
    }
    return cfg


def evaluate_execution_data_freshness(
    signal: dict[str, Any] | None,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate signal candle diagnostics against config-gated execution blocks."""
    sig = signal or {}
    gate = _gate_config(config)
    blocked: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def _add(tf: str, code: str, source: str, detail: dict[str, Any] | None = None) -> None:
        tf_u = str(tf or "").upper()
        if tf_u not in gate["BLOCK_TIMEFRAMES"]:
            return
        code_norm = _normalize_block_code(code)
        item = {
            "timeframe": tf_u,
            "severity": code_norm,
            "source": source,
            "detail": detail or {},
        }
        if code_norm in gate["BLOCK_SEVERITIES"]:
            blocked.append(item)
        elif code_norm:
            warnings.append(item)

    for source_key in ("candleFreshness", "candleFetchMeta"):
        meta = sig.get(source_key)
        if not isinstance(meta, dict):
            continue
        for tf, diag in meta.items():
            if not isinstance(diag, dict):
                continue
            severity = diag.get("stalenessSeverity")
            if severity:
                _add(tf, severity, source_key, diag)

    consistency = sig.get("candleConsistency")
    if isinstance(consistency, dict):
        for tf, diag in consistency.items():
            if isinstance(diag, dict):
                status = diag.get("status")
                if status and status != "OK":
                    _add(tf, status, "candleConsistency", diag)
            elif isinstance(diag, str) and diag != "OK":
                _add(tf, diag, "candleConsistency", {})

    allowed = not (bool(gate.get("BLOCK_EXECUTION_ON_STALE", True)) and blocked)
    reason = ""
    if not allowed:
        first = blocked[0]
        reason = f"STALE_DATA_BLOCK:{first['timeframe']}:{first['severity']}"

    return {
        "allowed": allowed,
        "blocked": blocked,
        "warnings": warnings,
        "reason": reason,
        "warnOnStaleScan": bool(gate.get("WARN_ON_STALE_SCAN", True)),
        "blockExecutionOnStale": bool(gate.get("BLOCK_EXECUTION_ON_STALE", True)),
    }
