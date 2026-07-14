"""Candle freshness diagnostics and execution-time data integrity gates."""

from __future__ import annotations

from typing import Any

from config import CONFIG

from athena_app.services.market_state import (
    apply_staleness_calendar_policies,
    candle_freshness_diagnostic,
    candle_timestamp_epoch,
    forex_market_closed_at,
    get_bucket_start_epoch,
    market_state_offset_hours,
    split_market_state,
    trim_mt5_d1_broker_session_ahead_tail,
)
from engine_a_scoring_profile import resolve_engine_a_scoring_profile


def d1_consumed_by_score_group(score_group: str | None, style: str | None) -> bool:
    """Return True if D1 feeds the trend or momentum scoring for this group/style.

    Groups that drop D1 from their trend layers (e.g., energy_oil/commodity_other
    use H4+H1 only) and don't anchor momentum on D1 do not consume D1, so stale D1
    must not block them pre-scoring or at execution. Conservative default True
    (require D1) when the profile can't be resolved or the group is unknown, so
    unaudited groups keep the historic fail-closed D1 gate.
    """
    if not score_group:
        return True
    try:
        profile = resolve_engine_a_scoring_profile(
            score_group=str(score_group), asset_type="", style=str(style or "intraday")
        )
        tfs = {str(t).upper() for t in (profile.get("trend_timeframes") or [])}
        momentum_tf = str(profile.get("momentum_tf") or "H4").upper()
        return "D1" in tfs or momentum_tf == "D1"
    except Exception:
        return True


DEFAULT_DATA_FRESHNESS_GATES = {
    "WARN_ON_STALE_SCAN": True,
    "BLOCK_EXECUTION_ON_STALE": True,
    # Include lower TFs used by Engine D so execution-time gates match scalp/structure feeds.
    "BLOCK_TIMEFRAMES": ["M5", "M15", "H1", "H4", "D1"],
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

CONFIRMED_ONLY_PRE_SCORING_TYPES = {
    "crypto",
    "forex",
    "stock",
    "index",
    "commodity",
    "etf",
    "etf_bond",
}


def pre_scoring_allows_confirmed_only_stale_1(pair: dict[str, Any] | None) -> bool:
    """Return whether one-bucket lag is expected for confirmed-only Engine A scoring."""
    if not isinstance(pair, dict):
        return False
    pair_type = str(pair.get("type") or "").strip().lower()
    return pair_type in CONFIRMED_ONLY_PRE_SCORING_TYPES


def _mt5_intraday_calendar_gap_grace_buckets(tf: str) -> int:
    """Max H4/H1 bucket lag tolerated as session calendar gaps for MT5 equities."""
    cfg = CONFIG.get("MT5_INTRADAY_CALENDAR_GAP_GRACE_BUCKETS") or {}
    if isinstance(cfg, dict):
        raw = cfg.get(str(tf or "").upper()) or cfg.get("default")
    else:
        raw = cfg
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _us_equity_off_hours_now() -> bool:
    """True when US cash session is off-hours (UTC approximation)."""
    from datetime import datetime, timezone

    from market_structure import _equity_session_bucket

    bucket = _equity_session_bucket(datetime.now(timezone.utc))
    return bucket.get("session") == "off_hours"


def pre_scoring_allows_intraday_calendar_gap(
    pair: dict[str, Any] | None,
    tf: str,
    diagnostic: dict[str, Any] | None,
) -> bool:
    """Allow expected H4/H1 lag for stock/index/commodity when the cash session is closed."""
    if not isinstance(pair, dict) or not isinstance(diagnostic, dict):
        return False
    tf_u = str(tf or "").upper()
    if tf_u not in ("H4", "H1"):
        return False
    if str(pair.get("source") or "").lower() != "mt5":
        return False
    pair_type = str(pair.get("type") or "").strip().lower()
    if pair_type not in ("stock", "index", "commodity", "etf", "etf_bond", "forex"):
        return False

    severity = str(diagnostic.get("stalenessSeverity") or diagnostic.get("stale_status") or "")
    if severity == "intraday_calendar_gap_policy_ok":
        return True

    if severity != "stale_multi_bucket":
        return False

    if pair_type == "forex":
        if not forex_market_closed_at():
            return False
        try:
            bucket_lag = int(diagnostic.get("bucketLag") or diagnostic.get("bucket_lag") or 0)
        except (TypeError, ValueError):
            bucket_lag = 0
        grace = _mt5_intraday_calendar_gap_grace_buckets(tf_u)
        return grace >= 2 and 2 <= bucket_lag <= grace

    if pair_type in ("stock", "index", "etf", "etf_bond") and _us_equity_off_hours_now():
        return True

    try:
        bucket_lag = int(diagnostic.get("bucketLag") or diagnostic.get("bucket_lag") or 0)
    except (TypeError, ValueError):
        bucket_lag = 0
    grace = _mt5_intraday_calendar_gap_grace_buckets(tf_u)
    return grace >= 2 and 2 <= bucket_lag <= grace


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
    scoring_candles: list[dict[str, Any]] | None = None,
    market_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one log/API-safe diagnostic row for a symbol/timeframe."""
    meta = fetch_meta if isinstance(fetch_meta, dict) else {}
    series = list(candles or [])
    series, _ = trim_mt5_d1_broker_session_ahead_tail(
        pair, str(timeframe or "").upper(), series, time_now=time_now
    )
    tf_u = str(timeframe or "").upper()
    offset_hours = float(market_state_offset_hours(pair, tf_u))

    if scoring_candles is None and isinstance(market_state, dict):
        scoring_candles = list(market_state.get("confirmed") or [])
    elif scoring_candles is None:
        state = split_market_state(
            series,
            tf_u,
            pair.get("display") or pair.get("symbol") or "",
            time_now=time_now,
            offset_hours=offset_hours,
        )
        scoring_candles = list(state.get("confirmed") or [])
        market_state = state
    else:
        scoring_candles = list(scoring_candles or [])

    scoring_diag = candle_freshness_diagnostic(
        pair,
        tf_u,
        scoring_candles,
        time_now=time_now,
        source=source or meta.get("upstream") or pair.get("source"),
    )

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

    raw_last_epoch = diag.get("lastBarEpoch")
    scoring_last_epoch = scoring_diag.get("lastBarEpoch")
    expected_epoch = scoring_diag.get("expectedCurrentBucketEpoch")
    lag_seconds = None
    if scoring_last_epoch is not None and time_now is not None:
        lag_seconds = max(0.0, float(time_now) - float(scoring_last_epoch))
    elif scoring_last_epoch is not None:
        import time as _time

        lag_seconds = max(0.0, _time.time() - float(scoring_last_epoch))

    dropped_forming = bool(
        isinstance(market_state, dict)
        and market_state.get("forming")
        and len(series) > len(scoring_candles)
    )

    diag.update(
        {
            "asset_type": pair.get("type"),
            "candle_count": len(series),
            "raw_count": len(series),
            "scoring_count": len(scoring_candles),
            "last_raw_ts": raw_last_epoch,
            "last_scoring_ts": scoring_last_epoch,
            "expected_latest_confirmed_ts": expected_epoch,
            "lag_seconds": round(lag_seconds, 1) if lag_seconds is not None else None,
            "bucket_lag": scoring_diag.get("bucketLag"),
            "stale_status": scoring_diag.get("stalenessSeverity"),
            "stale_reason": scoring_diag.get("stalenessSeverity"),
            "confirmed_only": True,
            "dropped_forming_candle": dropped_forming,
            "fallback_used": bool(meta.get("fallback_used")),
            "fallback_provider": meta.get("fallback_provider") or meta.get("fallback"),
            "fallback_reason": meta.get("fallback_reason"),
            "primary_provider": meta.get("primary_provider") or meta.get("requestedSource"),
            "broker_offset": offset_hours,
            "isFromCache": bool(meta.get("cacheHit")),
            "cache_age_seconds": meta.get("cacheAgeSec"),
            "cacheBypassed": bool(meta.get("cacheBypass") or meta.get("cacheWriteSkipped")),
            "fetchDurationMs": round(float(fetch_duration_ms), 3)
            if fetch_duration_ms is not None
            else None,
            "providerStatus": provider_status,
            "providerError": provider_error,
            "provider_timestamp": meta.get("provider_timestamp") or scoring_last_epoch,
            "freshness_lag": meta.get("freshness_lag") or scoring_diag.get("bucketLag"),
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
        if name == "cache":
            offset_h = market_state_offset_hours(pair, tf)
            out["offsetHours"] = offset_h
            if time_now is not None:
                out["expectedCurrentBucketEpoch"] = int(
                    get_bucket_start_epoch(tf, time_now, offset_h)
                )
        raw_severity = out.get("stalenessSeverity") or out.get("stale_status")
        raw_lag = out.get("bucketLag")
        if raw_lag is None:
            raw_lag = out.get("bucket_lag") or out.get("freshness_lag")
        if raw_severity and raw_lag is not None:
            try:
                out["stalenessSeverity"] = apply_staleness_calendar_policies(
                    pair,
                    tf,
                    str(raw_severity),
                    int(raw_lag),
                    time_now=time_now,
                )
            except (TypeError, ValueError):
                pass
    elif isinstance(payload, dict) and ("confirmed" in payload or "forming" in payload):
        candles = list(payload.get("confirmed") or [])
        if payload.get("forming"):
            candles.append(payload["forming"])
        candles, _ = trim_mt5_d1_broker_session_ahead_tail(pair, tf, candles, time_now=time_now)
        out = candle_freshness_diagnostic(pair, tf, candles, time_now=time_now)
        state = split_market_state(
            candles,
            tf,
            pair.get("display") or pair.get("symbol") or "",
            time_now=time_now,
            offset_hours=market_state_offset_hours(pair, tf),
        )
        confirmed_epoch, forming_epoch = _state_epochs(state)
        out["confirmedLatestEpoch"] = confirmed_epoch
        out["formingEpoch"] = forming_epoch
        out["usesForming"] = bool(forming_epoch and out.get("lastBarEpoch") == forming_epoch)
    elif isinstance(payload, list):
        series = list(payload or [])
        series, _ = trim_mt5_d1_broker_session_ahead_tail(pair, tf, series, time_now=time_now)
        out = candle_freshness_diagnostic(pair, tf, series, time_now=time_now)
        state = split_market_state(
            series,
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
    """Compare live candle state across provider/cache/engine/scanner paths.
    
    Candle policy classification:
    - CONFIRMED_ONLY: Engine uses confirmed candles only (intentional one-bucket lag behind raw forming)
    - FORMING_ALLOWED: Engine uses confirmed + forming candles
    - RAW_CURRENT_REQUIRED: Engine must have current forming candle
    """
    from athena_app.services.market_state import _timeframe_seconds
    
    tf = str(timeframe or "").upper()
    expected_offset = market_state_offset_hours(pair, tf)
    expected_bucket = (
        get_bucket_start_epoch(tf, time_now, expected_offset)
        if time_now is not None
        else None
    )
    expected_confirmed_bucket = expected_bucket - _timeframe_seconds(tf) if expected_bucket else None
    
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

    # Determine candle policy for each engine path and compute policy status
    engine_paths = {}
    raw_has_current = diagnostics.get("raw_provider", {}).get("hasCurrentBucket", False)
    raw_current_epoch = diagnostics.get("raw_provider", {}).get("lastBarEpoch") if raw_has_current else None
    
    for name in ("engine_a", "engine_b", "scanner", "compare"):
        if name in diagnostics:
            diag = diagnostics[name]
            uses_forming = diag.get("usesForming", False)
            bucket_lag = int(diag.get("bucketLag") or 0)
            confirmed_epoch = diag.get("confirmedLatestEpoch")
            forming_epoch = diag.get("formingEpoch")
            last_bar_epoch = diag.get("lastBarEpoch")
            
            # Determine policy based on pair type and source
            if pair.get("source") == "mt5" and pair.get("type") == "forex":
                policy = "CONFIRMED_ONLY"
            elif name == "engine_b":
                policy = "CONFIRMED_ONLY"
            elif name == "scanner":
                policy = "CONFIRMED_ONLY"
            elif name == "compare":
                policy = "CONFIRMED_ONLY"
            elif uses_forming:
                policy = "FORMING_ALLOWED"
            else:
                policy = "CONFIRMED_ONLY"
            
            # Determine policy status
            if policy == "FORMING_ALLOWED":
                # Should have forming candle at raw current epoch
                if forming_epoch and raw_current_epoch and forming_epoch == raw_current_epoch:
                    policy_status = "POLICY_OK"
                elif bucket_lag == 0:
                    policy_status = "POLICY_OK"
                else:
                    policy_status = "POLICY_LAG"
            elif policy == "CONFIRMED_ONLY":
                # Confirmed-only feeds/engines may only expose the latest closed bucket.
                # Classify that as OK when it matches the expected confirmed bucket,
                # even if the raw provider did not include the current forming bucket.
                expected_confirmed = expected_confirmed_bucket
                if confirmed_epoch and expected_confirmed:
                    if confirmed_epoch == expected_confirmed:
                        policy_status = "POLICY_OK"
                    elif confirmed_epoch < expected_confirmed:
                        policy_status = "POLICY_LAG"
                    else:
                        policy_status = "POLICY_MISMATCH"
                elif confirmed_epoch and raw_current_epoch:
                    expected_confirmed = raw_current_epoch - _timeframe_seconds(tf)
                    if confirmed_epoch == expected_confirmed:
                        policy_status = "POLICY_OK"
                    elif confirmed_epoch < expected_confirmed:
                        policy_status = "POLICY_LAG"
                    else:
                        policy_status = "POLICY_MISMATCH"
                elif bucket_lag == 1:
                    policy_status = "POLICY_OK"
                else:
                    policy_status = "POLICY_LAG"
            else:
                policy_status = "POLICY_OK"
            
            engine_paths[name] = {
                "policy": policy,
                "policyStatus": policy_status,
                "usesForming": uses_forming,
                "bucketLag": bucket_lag,
                "lastBarEpoch": last_bar_epoch,
                "confirmedLatestEpoch": confirmed_epoch,
                "formingEpoch": forming_epoch,
            }

    # Check for path mismatch - only compare paths with the SAME policy
    confirmed_only_epochs = {
        k: v.get("confirmedLatestEpoch")
        for k, v in engine_paths.items()
        if v.get("confirmedLatestEpoch") is not None and v.get("policy") == "CONFIRMED_ONLY"
    }
    if len(set(confirmed_only_epochs.values())) > 1:
        return {
            "status": "ERROR_PATH_MISMATCH",
            "reason": f"confirmed-only paths have different epochs: {confirmed_only_epochs}",
            "paths": diagnostics,
            "enginePolicies": engine_paths,
        }
    
    forming_allowed_epochs = {
        k: v.get("lastBarEpoch")
        for k, v in engine_paths.items()
        if v.get("lastBarEpoch") is not None and v.get("policy") == "FORMING_ALLOWED"
    }
    if len(set(forming_allowed_epochs.values())) > 1:
        return {
            "status": "ERROR_PATH_MISMATCH",
            "reason": f"forming-allowed paths have different epochs: {forming_allowed_epochs}",
            "paths": diagnostics,
            "enginePolicies": engine_paths,
        }

    # Check for one-bucket lag - classify based on policy
    has_one_bucket_lag = any(
        int(d.get("bucketLag") or 0) == 1 for d in diagnostics.values()
    )
    
    if has_one_bucket_lag:
        # Check if all engine paths are POLICY_OK
        all_policy_ok = all(
            ep.get("policyStatus") == "POLICY_OK"
            for ep in engine_paths.values()
        )
        
        if all_policy_ok and engine_paths:
            # This is intentional confirmed-only behavior
            return {
                "status": "CONFIRMED_ONLY_OK",
                "reason": "engine paths use confirmed-only policy (intentional one-bucket lag behind current/forming bucket)",
                "paths": diagnostics,
                "enginePolicies": engine_paths,
            }
        else:
            # This is a true lag issue
            return {
                "status": "WARNING_ONE_BUCKET_LAG",
                "reason": "one or more paths lag by one bucket",
                "paths": diagnostics,
                "enginePolicies": engine_paths,
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
            "enginePolicies": engine_paths,
        }

    return {
        "status": "OK",
        "reason": "",
        "paths": diagnostics,
        "enginePolicies": engine_paths,
    }


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
    _d1_required = d1_consumed_by_score_group(
        sig.get("scoreGroup") or sig.get("score_group"),
        sig.get("horizon") or sig.get("style"),
    )
    blocked: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def _add(tf: str, code: str, source: str, detail: dict[str, Any] | None = None) -> None:
        tf_u = str(tf or "").upper()
        if tf_u not in gate["BLOCK_TIMEFRAMES"]:
            return
        # D1 freshness only blocks when D1 feeds the signal's trend/momentum
        # scoring. Groups that drop D1 from their trend layers (energy_oil,
        # commodity_other) and don't anchor momentum on D1 must not be blocked
        # on stale D1 that doesn't affect their score.
        if tf_u == "D1" and not _d1_required:
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

    consistency = sig.get("candleConsistency")
    if isinstance(consistency, dict):
        for tf, diag in consistency.items():
            if isinstance(diag, dict):
                status = diag.get("status")
                # CONFIRMED_ONLY_OK is not a blocking condition - it's intentional confirmed-only policy
                if status and status not in ("OK", "CONFIRMED_ONLY_OK"):
                    _add(tf, status, "candleConsistency", diag)
            elif isinstance(diag, str) and diag not in ("OK", "CONFIRMED_ONLY_OK"):
                _add(tf, diag, "candleConsistency", {})
    
    # Determine if any timeframe has CONFIRMED_ONLY_OK status
    # This indicates the staleness is intentional policy lag, not true stale data
    has_confirmed_only_ok = False
    if isinstance(consistency, dict):
        for tf, diag in consistency.items():
            if isinstance(diag, dict):
                status = diag.get("status")
                if status == "CONFIRMED_ONLY_OK":
                    has_confirmed_only_ok = True
                    break
            elif diag == "CONFIRMED_ONLY_OK":
                has_confirmed_only_ok = True
                break
    
    freshness_by_tf: dict[str, dict[str, Any]] = {}
    _cf = sig.get("candleFreshness")
    if isinstance(_cf, dict):
        for tf, diag in _cf.items():
            if isinstance(diag, dict):
                freshness_by_tf[str(tf or "").upper()] = diag

    _policy_ok_severities = {
        "d1_calendar_gap_policy_ok",
        "intraday_calendar_gap_policy_ok",
    }

    for source_key in ("candleFreshness", "candleFetchMeta"):
        meta = sig.get(source_key)
        if not isinstance(meta, dict):
            continue
        for tf, diag in meta.items():
            if not isinstance(diag, dict):
                continue
            tf_u = str(tf or "").upper()
            # candleFreshness is authoritative per TF; fetch meta must not override it.
            if source_key == "candleFetchMeta" and tf_u in freshness_by_tf:
                continue
            severity = diag.get("stalenessSeverity")
            if severity:
                # If this timeframe has CONFIRMED_ONLY_OK consistency, exclude stale_1_bucket (policy-expected lag)
                # Otherwise, block on all severities as configured
                if has_confirmed_only_ok and severity == "stale_1_bucket":
                    # Skip blocking on stale_1_bucket when policy-aware status is CONFIRMED_ONLY_OK
                    continue
                if severity in _policy_ok_severities:
                    # Calendar gap policy is intentional session/weekend tolerance; do not block
                    continue
                _add(tf, severity, source_key, diag)

    allowed = not (bool(gate.get("BLOCK_EXECUTION_ON_STALE", True)) and blocked)
    reason = ""
    if not allowed:
        first = blocked[0]
        reason = f"STALE_DATA_BLOCK:{first['timeframe']}:{first['severity']}"

    # #region agent log
    if blocked and gate.get("BLOCK_EXECUTION_ON_STALE", True):
        severe = False
        for b in blocked:
            sev = str(b.get("severity") or "")
            if "stale_multi" in sev or "multi_bucket" in sev:
                severe = True
                break
        if severe:
            try:
                from athena_app.debug_ndjson_agent import append_agent_ndjson

                append_agent_ndjson(
                    {
                        "hypothesisId": "H_risk_blocked_stale_multi",
                        "location": "data_freshness.evaluate_execution_data_freshness",
                        "message": "exec_blocked_stale_multi",
                        "runId": "post-fix",
                        "data": {
                            "signalPair": (sig.get("pair") if isinstance(sig, dict) else None),
                            "blocked": blocked[:4],
                            "reason": reason,
                        },
                    }
                )
            except Exception:
                pass
    # #endregion

    return {
        "allowed": allowed,
        "blocked": blocked,
        "warnings": warnings,
        "reason": reason,
        "warnOnStaleScan": bool(gate.get("WARN_ON_STALE_SCAN", True)),
        "blockExecutionOnStale": bool(gate.get("BLOCK_EXECUTION_ON_STALE", True)),
    }


def evaluate_live_quote_age(
    pair: dict[str, Any] | None,
    age_sec: float | int | None,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare a quote's age against the per-asset-type `LIVE_PRICE_MAX_AGE_SEC` config.

    When no threshold is configured for the pair's asset type, returns
    ``{"enabled": False, ...}``. ``would_block`` is True only when execution
    blocking is enabled via ``LIVE_PRICE_BLOCK_EXECUTION`` and the quote is
    stale (or age unknown when ``LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE``).

    Returns:
        dict with keys: enabled, assetType, thresholdSec, ageSec, stale,
        reason, would_block.
    """
    cfg = config or {}
    asset_type = ""
    if isinstance(pair, dict):
        asset_type = str(pair.get("type") or "").strip().lower()
    threshold_type = "etf" if asset_type == "etf_bond" else asset_type

    cfg_root = cfg.get("LIVE_PRICE_MAX_AGE_SEC")
    threshold: float | None = None
    if isinstance(cfg_root, dict) and threshold_type:
        raw = cfg_root.get(threshold_type)
        if isinstance(raw, (int, float)) and raw > 0:
            threshold = float(raw)

    age_val: float | None = None
    if isinstance(age_sec, (int, float)) and age_sec >= 0:
        age_val = float(age_sec)

    block_exec = bool(cfg.get("LIVE_PRICE_BLOCK_EXECUTION", False))
    block_unknown = bool(cfg.get("LIVE_PRICE_BLOCK_EXECUTION_ON_UNKNOWN_AGE", False))

    if threshold is None:
        return {
            "enabled": False,
            "assetType": asset_type or None,
            "thresholdSec": None,
            "ageSec": age_val,
            "stale": False,
            "reason": "DISABLED" if not asset_type else f"NO_THRESHOLD_FOR_{asset_type.upper()}",
            "would_block": False,
        }

    if age_val is None:
        return {
            "enabled": True,
            "assetType": asset_type,
            "thresholdSec": threshold,
            "ageSec": None,
            "stale": False,
            "reason": "AGE_UNKNOWN",
            "would_block": bool(block_exec and block_unknown),
        }

    stale = age_val > threshold
    return {
        "enabled": True,
        "assetType": asset_type,
        "thresholdSec": threshold,
        "ageSec": age_val,
        "stale": stale,
        "reason": "STALE" if stale else "FRESH",
        "would_block": bool(block_exec and stale),
    }
